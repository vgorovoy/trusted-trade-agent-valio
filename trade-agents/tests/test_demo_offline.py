"""The video demo must prove the protocol offline, not merely print expected statuses."""
from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

from trade_agents.service import Gateway
from trade_agents.signing import SignatureError


@pytest.fixture
def demo():
    path = Path(__file__).resolve().parents[1] / 'scripts/demo_offline.py'
    spec = importlib.util.spec_from_file_location('demo_offline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protocol_demo_offline_and_isolated(demo, monkeypatch, tmp_path):
    def no_network(*args, **kwargs):
        raise AssertionError('The offline demo must not call any network service')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setenv('AIVEN_PG_URL', 'postgresql://unused:unused@invalid.example/database')
    monkeypatch.setenv('TTA_DATA_DIR', str(tmp_path / 'production-data'))
    monkeypatch.setenv('TTA_VALIO_REVIEW', 'sync')
    result = demo.run_demo()
    assert result['llm_used'] is False and result['live_valio_api_used'] is False
    hotel = result['hotel']
    assert hotel['transitions'] == ['awaiting_cart_confirmation', 'pending_approval', 'placed']
    assert hotel['total_eur_excl_vat'] == 540 and hotel['autonomous_limit_eur'] == 500
    assert all(hotel[key] for key in ('buyer_mandate_verified', 'signed_offer_verified',
                                      'signed_cart_confirmation_accepted', 'signed_receipt_verified',
                                      'audit_chain_valid'))
    assert hotel['audit_events'][-2:] == ['approval.granted', 'order.placed']
    assert hotel['mock_api_calls'] > 0
    assert len(hotel['audit_head']['hash']) == 64
    assert [case['allergen_source'] for case in result['school']] == ['contains', 'may_contain']
    assert all(case['status'] == 'rejected' and case['audit_chain_valid'] for case in result['school'])
    assert not (tmp_path / 'production-data').exists()
    assert demo.os.environ['TTA_VALIO_REVIEW'] == 'sync'


def test_demo_reproducible(demo):
    assert demo.run_demo() == demo.run_demo()


def test_demo_rejects_tampered_receipt(demo, monkeypatch):
    commit = Gateway._commit

    def tampered(self, *args, **kwargs):
        order = commit(self, *args, **kwargs)
        order['data']['receipt']['claims']['total_eur'] = 1
        return order

    monkeypatch.setattr(Gateway, '_commit', tampered)
    with pytest.raises(SignatureError, match='claims do not match'):
        demo.run_demo()


def test_demo_json_and_text(demo, capsys):
    demo.main(['--json'])
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'offline / scripted orchestration'
    demo.main([])
    text = capsys.readouterr().out
    assert 'no LLM, no live API, no production orders' in text
    assert 'awaiting_cart_confirmation → pending_approval → placed' in text
    assert 'Milk may_contain: REJECTED' in text
