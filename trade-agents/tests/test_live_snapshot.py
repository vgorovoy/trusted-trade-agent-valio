"""Official mock lane reuses the snapshot oracle, with fake provider/supplier in tests."""
from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scripts.compare_real_models import capture_snapshot
from trade_agents.llm import LLMResponse, ToolCall, last_tool_result
from web import live, live_snapshot, server


@pytest.fixture
def snapshot(gateway):
    value = capture_snapshot(gateway.catalog, gateway.client, ['100', '300', '400'],
                             date(2026, 11, 16), 'test-source-hash',
                             demo_clock=datetime.fromisoformat('2026-11-12T10:00:00+02:00'))
    mapping = {'100': '464073', '300': '464076', '400': '460825'}
    for product in value['products']:
        product['product_id'] = mapping[product['product_id']]
        product['raw']['id'] = product['product_id']
    for field in ('prices', 'availability'):
        value[field] = {mapping[pid]: dict(record, productId=mapping[pid])
                        for pid, record in value[field].items()}
    return value


class ExactControl:
    name = 'scripted:official-ui-control'
    vision = False
    client = SimpleNamespace(close=lambda: None)

    def chat(self, messages, *args, **kwargs):
        if 'Excluded allergens (hard rule): milk' in messages[0]['content']:
            return LLMResponse(tool_calls=[ToolCall('finish', {'status': 'no_order'})])
        order = last_tool_result(messages, 'place_order')
        if order:
            return LLMResponse(tool_calls=[ToolCall('finish', {'status': order['status']})])
        plan = last_tool_result(messages, 'plan_need')
        if plan is None:
            pid = re.search(r'of product (\d+):', messages[0]['content'])[1]
            return LLMResponse(tool_calls=[ToolCall('plan_need', {
                'query': 'maito', 'amount': 1, 'unit': 'ST', 'requested_date': '2026-11-16',
                'product_ids': [pid]})])
        if not plan['options']:
            return LLMResponse(tool_calls=[ToolCall('finish', {'status': 'no_order'})])
        lines = plan['options'][0]['lines']
        return LLMResponse(tool_calls=[ToolCall('place_order', {'lines': [
            {'offer_id': line['offer_id'], 'quantity': line['quantity']} for line in lines]})])


def test_official_metadata_is_three_diagnostics_not_synthetic42():
    response = TestClient(server.app).get('/ui/live/scenarios?dataset=official_snapshot')
    assert response.status_code == 200
    data = response.json()
    assert len(data['scenarios']) == 3 and data['modes'] == ['live']
    assert {row['id'] for row in data['scenarios']} == set(live_snapshot.DIAGNOSTICS)
    assert all(row['expected'] == 'capture-dependent' for row in data['scenarios'])
    assert TestClient(server.app).get('/ui/live/scenarios?dataset=arbitrary').status_code == 422


def test_official_lane_needs_configuration_and_live_mode(monkeypatch):
    monkeypatch.setattr(live, 'official_configured', lambda: False)
    manager = live.LiveRuns()
    with pytest.raises(HTTPException) as error:
        manager.start(live_snapshot.diagnostic_scenarios()[:1], 'live', 'gemini',
                      'gemini-3.5-flash', 'official_snapshot')
    assert error.value.status_code == 400 and manager.live_attempts == 0
    monkeypatch.setattr(live, 'official_configured', lambda: True)
    with pytest.raises(HTTPException):
        manager.start(live_snapshot.diagnostic_scenarios()[:1], 'offline', dataset='official_snapshot')


@pytest.mark.parametrize('case_id', ['real-purchase-464073', 'real-allergen_refusal-464073'])
def test_official_worker_streams_real_oracle_and_tools_without_raw_supplier_payload(
        snapshot, monkeypatch, case_id):
    monkeypatch.setattr(live_snapshot, 'bounded_model', lambda *args: ExactControl())
    events = []
    channel = SimpleNamespace(send=events.append)
    scenarios = [sc for sc in live_snapshot.diagnostic_scenarios() if sc.id == case_id]
    live_snapshot.run_official_worker(scenarios, channel,
                                      {'provider': 'gemini', 'model': 'gemini-3.5-flash'},
                                      snapshot=snapshot)
    provenance = next(data for kind, data in events if kind == 'snapshot')
    assert provenance['dataset'] == 'official_snapshot'
    assert provenance['production_orders'] is False
    assert 'snapshot' in provenance['availability_freshness']
    assert len(provenance['snapshot_sha256']) == 64
    assert 'headers' not in json.dumps(events)
    result = next(data for kind, data in events if kind == 'scenario_finished')
    assert result['passed'] and result['checks']
    report = next(data for kind, data in events if kind == 'report')
    assert report['scored'] == 1 and report['coverage']['fraction'] == 1
    assert report['execution']['dataset'] == 'official_snapshot'
    assert report['execution']['actual_model_calls'] > 0
    assert any(kind == 'tool' for kind, data in events)


def test_past_cutoff_is_visible_and_not_counted_as_eligible_purchase(snapshot, monkeypatch):
    snapshot = copy.deepcopy(snapshot)
    for record in snapshot['availability'].values():
        record['closingTime'] = '2026-11-12T09:00:00+0200'
    monkeypatch.setattr(live_snapshot, 'bounded_model', lambda *args: ExactControl())
    events = []
    live_snapshot.run_official_worker(live_snapshot.diagnostic_scenarios()[:1],
        SimpleNamespace(send=events.append), {'provider': 'gemini', 'model': 'gemini-3.5-flash'},
        snapshot=snapshot)
    started = next(data for kind, data in events if kind == 'scenario_started')
    assert not started['positive_transactable'] and started['expected_status'] == 'safe_refusal'
    finished = next(data for kind, data in events if kind == 'scenario_finished')
    assert finished['passed'] and finished['status'] == 'no_order'


def test_capture_uses_existing_narrow_client_and_provenance_without_secret(gateway, monkeypatch):
    monkeypatch.setattr(live_snapshot, 'APPROVED_PRODUCTS', ['100', '300', '400'])
    value = live_snapshot.capture_official(client=gateway.client, path=gateway.settings.catalog_db,
                                          requested=date(2026, 11, 16))
    assert len(value['http_calls']) == 2
    assert value['business_clock'] == value['capture_completed_at']
    assert 'simulated' not in value['clock_label']
    assert 'valio_api_key' not in json.dumps(live_snapshot.provenance(value))
