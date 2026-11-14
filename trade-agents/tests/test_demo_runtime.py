"""Explicit demo storage/clock and bounded Postgres connection failures, all offline."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock

import psycopg
import pytest

from trade_agents import cli
from trade_agents.buyer_agent.profiles import _valid, next_weekday
from trade_agents.clock import current_time, demo_time
from trade_agents.config import TZ, Settings
from trade_agents.offers import assess_delivery
from trade_agents.signing import decode_unverified, issue_passport, now_iso, passport_problem
from trade_agents.store import Store, StoreConnectionError, now, open_store, store_url
from trade_agents.store_pg import PgStore

AT = '2026-11-12T10:00:00+02:00'


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    for name in ('TTA_LOCAL', 'TTA_DEMO_CLOCK', 'TTA_STORE_URL', 'AIVEN_PG_URL'):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_postgres_connect_timeout_and_sanitized_error(monkeypatch, tmp_path):
    url = 'postgresql://demo-user:demo-password@invalid.example/database?connect_timeout=90'
    connect = Mock(side_effect=psycopg.OperationalError('cannot connect: ' + url))
    monkeypatch.setattr(psycopg, 'connect', connect)
    monkeypatch.setenv('AIVEN_PG_URL', url)
    with pytest.raises(StoreConnectionError) as error:
        open_store(tmp_path / 'state.sqlite')
    assert connect.call_args.kwargs['connect_timeout'] == 3
    assert 'demo-password' not in str(error.value)
    assert 'invalid.example' not in str(error.value)
    assert 'No SQLite fallback' in str(error.value)
    assert not (tmp_path / 'state.sqlite').exists()
    assert error.value.__suppress_context__


def test_reconnect_keeps_connection_timeout(monkeypatch):
    connect = Mock(side_effect=psycopg.OperationalError('offline'))
    monkeypatch.setattr(psycopg, 'connect', connect)
    store = PgStore.__new__(PgStore)
    store._url = 'postgresql://local/test'
    store.schema = None
    for _ in range(2):
        with pytest.raises(StoreConnectionError):
            store._connect()
    assert all(call.kwargs['connect_timeout'] == 3 for call in connect.call_args_list)


@pytest.mark.parametrize('flag', ['TTA_LOCAL', 'TTA_DEMO_CLOCK'])
def test_local_store_and_keys_isolated_but_static_catalog_shared(monkeypatch, tmp_path, flag):
    monkeypatch.setenv('TTA_DATA_DIR', str(tmp_path))
    original = Settings()
    original.state_db.write_text('production-state')
    monkeypatch.setenv('AIVEN_PG_URL', 'postgresql://unused/test')
    monkeypatch.setenv('TTA_STORE_URL', 'postgresql://also-unused/test')
    monkeypatch.setenv(flag, '1' if flag == 'TTA_LOCAL' else AT)
    local = Settings()
    assert local.catalog_db == original.catalog_db == tmp_path / 'catalog.sqlite'
    assert local.raw_dir == original.raw_dir
    assert local.state_db == tmp_path / 'local-demo/state.sqlite'
    assert local.keys_dir == tmp_path / 'local-demo/keys'
    assert local.buyers_dir == tmp_path / 'local-demo/buyers'
    assert store_url() is None
    store = open_store(local.state_db)
    assert isinstance(store, Store)
    store.con.close()
    assert (tmp_path / 'state.sqlite').read_text() == 'production-state'


def test_real_time_unchanged_without_demo_flag():
    wall = datetime.now(TZ)
    assert current_time(wall) == wall
    assert demo_time() is None


def test_clock_consistent_for_cutoff_mandate_offer_and_passport(monkeypatch, gateway):
    monkeypatch.setenv('TTA_DEMO_CLOCK', AT)
    at = datetime.fromisoformat(AT)
    assert now() == at
    assert now_iso() == AT
    assert _valid() == at + timedelta(days=30)
    assert next_weekday(0).isoformat() == '2026-11-16'
    delivery = assess_delivery({
        'closingTime': '2026-11-12T12:00:00+0200',
        'scheduleLines': [{'availableDate': '2026-11-16', 'availableAmount': 100}],
    }, datetime(2026, 11, 16).date(), 10)
    assert delivery.status == 'available'
    offer = gateway.quote(['100'], datetime(2026, 11, 16).date())[0]
    expires = datetime.fromisoformat(offer['valid_until'])
    assert expires == at + timedelta(minutes=gateway.settings.offer_ttl_minutes)
    passport = issue_passport(gateway.signer, 'demo', 'offline', 'test', 'autonomous', valid_days=1)
    claims = decode_unverified(passport)
    assert claims['issued_at'] == AT
    assert claims['exp'] == int((at + timedelta(days=1)).timestamp())
    assert passport_problem(claims) is None
    monkeypatch.setenv('TTA_DEMO_CLOCK', (at + timedelta(days=1)).isoformat())
    assert 'expired' in passport_problem(claims)


@pytest.mark.parametrize('value', ['not-a-date', '2026-11-12T10:00:00', '2026-11-12'])
def test_invalid_clock_fails_before_storage(monkeypatch, tmp_path, value):
    monkeypatch.setenv('TTA_DEMO_CLOCK', value)
    monkeypatch.setenv('TTA_DATA_DIR', str(tmp_path / 'unused'))
    with pytest.raises(ValueError, match='timezone'):
        Settings()
    assert not (tmp_path / 'unused').exists()


def test_cli_flags_after_command(monkeypatch, capsys):
    observed = []
    monkeypatch.setattr(cli, 'cmd_drift', lambda args: observed.append((store_url(), now())))
    monkeypatch.setenv('AIVEN_PG_URL', 'postgresql://unused/test')
    cli.main(['drift', 'ignored.json', '--local', '--demo-clock', AT])
    assert observed == [(None, datetime.fromisoformat(AT))]
    assert 'DEMO CLOCK' in capsys.readouterr().err


def test_cli_invalid_clock(monkeypatch):
    with pytest.raises(SystemExit) as error:
        cli.main(['--demo-clock', '2026-11-12T10:00:00', 'drift', 'ignored.json'])
    assert error.value.code == 2
