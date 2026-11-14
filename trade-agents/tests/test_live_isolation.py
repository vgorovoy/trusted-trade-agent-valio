"""Real spawned workers must never lend a frozen World to concurrent Exchange requests."""
from __future__ import annotations

import multiprocessing
import os
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from trade_agents import api, store
from trade_agents.bench.scenario import load_scenarios
from trade_agents.buyer_agent import tools
from web import live, server


def holding_world(scenarios, mode, channel):
    """Keep the unsafe benchmark context open while the parent serves Exchange."""
    from trade_agents.bench.world import World
    from trade_agents.store import now

    with World(scenarios[0]) as world:
        channel.send(('held', {'directory': str(world.dir), 'pid': os.getpid(),
                               'clock': now().isoformat(), 'data_dir': os.environ['TTA_DATA_DIR']}))
        deadline = time.monotonic() + 12
        while not Path(scenarios[0].source).exists():
            if time.monotonic() > deadline:
                raise TimeoutError('test did not release its child World')
            time.sleep(.02)
    channel.send(('report', {'passed': 1, 'scored': 1, 'coverage': 1}))
    channel.close()


def successful_worker(scenarios, mode, channel):
    channel.send(('report', {'passed': 1, 'scored': 1, 'coverage': 1}))
    channel.close()


def crashed_worker(scenarios, mode, channel):
    os._exit(7)


def blocked_worker(scenarios, mode, channel):
    channel.send(('held', {'pid': os.getpid()}))
    time.sleep(20)


def wait_for(manager, run, *, kind=None, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(run.id, 0)
        if kind is not None:
            event = next((e for e in snapshot['events'] if e['kind'] == kind), None)
            if event:
                return event['data']
            assert not snapshot['done'], snapshot
        elif snapshot['done']:
            return snapshot
        time.sleep(.02)
    raise AssertionError('spawned worker did not reach its expected state')


def test_child_world_cannot_contaminate_first_parent_gateway(monkeypatch, settings, catalog, tmp_path):
    parent_dir = settings.data_dir
    monkeypatch.setenv('TTA_DATA_DIR', str(parent_dir))
    monkeypatch.setenv('VALIO_API_KEY', 'offline-isolation-test')
    monkeypatch.setenv('TTA_DEMO_CLOCK', '2030-01-02T10:00:00+02:00')
    monkeypatch.delenv('AIVEN_PG_URL', raising=False)
    monkeypatch.delenv('TTA_STORE_URL', raising=False)
    api.gateway.cache_clear()
    parent_uuid = (store.uuid, tools.uuid)
    parent_datetime = store.datetime
    release = tmp_path / 'release-worker'
    scenario = replace(load_scenarios(only=['basic-milk'])[0], source=str(release))
    manager = live.LiveRuns(worker=holding_world, worker_timeout=15)
    run = manager.start([scenario], 'offline')
    child = wait_for(manager, run, kind='held')
    try:
        assert child['pid'] != os.getpid()
        assert child['clock'].startswith('2026-11-13')
        assert Path(child['directory']).is_dir()
        assert child['data_dir'] != str(parent_dir)
        assert os.environ['TTA_DATA_DIR'] == str(parent_dir)
        assert os.environ['TTA_DEMO_CLOCK'] == '2030-01-02T10:00:00+02:00'
        assert store.now() == datetime.fromisoformat('2030-01-02T10:00:00+02:00')
        assert (store.uuid, tools.uuid) == parent_uuid == (uuid, uuid)
        assert store.datetime is parent_datetime is datetime
        assert api.gateway.cache_info().currsize == 0
        overview = TestClient(server.app).get('/ui/overview')
        assert overview.status_code == 200
        assert overview.json()['audit']['chain_valid'] is True
        gateway = api.gateway()
        assert str(gateway.settings.catalog_db) == catalog.con.execute('PRAGMA database_list').fetchone()[2]
        assert gateway.settings.keys_dir == parent_dir / 'local-demo/keys'
        assert gateway.settings.keys_dir.is_dir()
        assert gateway.settings.state_db.is_file()
        jwks = gateway.jwks()
    finally:
        release.touch()
        result = wait_for(manager, run)
        api.gateway.cache_clear()
    assert result['error'] is None and manager.active is None
    assert not Path(child['directory']).exists()
    assert parent_dir.is_dir() and (parent_dir / 'local-demo/keys').is_dir()
    assert gateway.jwks() == jwks
    assert gateway.store.verify_audit() == (True, None)
    assert os.environ['TTA_DATA_DIR'] == str(parent_dir)


@pytest.mark.parametrize('worker', [crashed_worker, blocked_worker])
def test_failed_worker_is_reaped_and_next_run_allowed(worker):
    before = {p.pid for p in multiprocessing.active_children()}
    manager = live.LiveRuns(worker=worker, worker_timeout=1)
    scenarios = load_scenarios(only=['basic-milk'])
    result = wait_for(manager, manager.start(scenarios, 'offline'))
    assert result['error'] is not None and manager.active is None
    assert any(e['kind'] == 'run_failed' for e in result['events'])
    assert {p.pid for p in multiprocessing.active_children()} <= before
    manager.worker = successful_worker
    recovered = wait_for(manager, manager.start(scenarios, 'offline'))
    assert recovered['error'] is None and recovered['report']['passed'] == 1
    assert manager.active is None


@pytest.mark.parametrize('failure', ['pipe', 'constructor', 'start'])
def test_spawn_setup_failure_releases_active_and_closes_pipes(monkeypatch, failure):
    manager = live.LiveRuns()
    context = Mock()
    receive, send = Mock(), Mock()
    process = Mock(pid=None)
    context.Pipe.return_value = (receive, send)
    context.Process.return_value = process
    if failure == 'pipe':
        context.Pipe.side_effect = OSError('pipe unavailable')
    elif failure == 'constructor':
        context.Process.side_effect = RuntimeError('cannot construct process')
    else:
        process.start.side_effect = OSError('spawn unavailable')
    monkeypatch.setattr(live.multiprocessing, 'get_context', lambda mode: context)
    run = manager.start([], 'offline')
    result = wait_for(manager, run)
    assert manager.active is None and result['done']
    assert result['error'] is not None
    if failure != 'pipe':
        receive.close.assert_called_once()
        send.close.assert_called_once()
    if failure == 'start':
        process.close.assert_called_once()


def test_thread_start_failure_releases_active(monkeypatch):
    manager = live.LiveRuns()
    monkeypatch.setattr(live.threading.Thread, 'start', Mock(side_effect=RuntimeError('thread unavailable')))
    run = manager.start([], 'offline')
    assert run.done and manager.active is None
    assert 'thread unavailable' in manager.snapshot(run.id, 0)['error']
