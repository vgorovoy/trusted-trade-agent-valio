"""One deterministic combined cart: no per-ingredient orders or model calls."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from trade_agents.buyer_agent.profiles import BuyerProfile
from web import menu, server


@pytest.fixture
def basket_client(gateway, mandate, monkeypatch):
    from trade_agents import api
    from trade_agents.buyer_agent import profiles

    profile = BuyerProfile('restaurant', 'Synthetic restaurant basket test', mandate.model_copy(update={
        'min_delivery_reliability': None}), passport=None)
    monkeypatch.setattr(menu, 'get_profile', lambda key: profile)
    monkeypatch.setattr(profiles, 'get_settings', lambda: gateway.settings)
    monkeypatch.setattr(api, 'gateway', lambda: gateway)
    monkeypatch.setattr(menu, 'get_llm', lambda: (_ for _ in ()).throw(AssertionError('No LLM allowed')))
    return TestClient(server.app), gateway, profile


def body():
    return {'buyer': 'restaurant', 'reviewed': True, 'idempotency_key': 'basket-test-001', 'needs': [
        {'ingredient': 'Milk', 'search_hint_fi': 'maito', 'amount': 1, 'unit': 'ST',
         'date': '2026-11-16', 'constraints': []},
        {'ingredient': 'Whole milk', 'search_hint_fi': 'täysmaito', 'amount': 1, 'unit': 'ST',
         'date': '2026-11-16', 'constraints': []}]}


def test_all_rows_become_one_confirm_cart_with_canonical_signed_mandate(basket_client):
    client, gateway, profile = basket_client
    result = client.post('/ui/menu/basket', json=body())
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['complete'] and data['status'] == 'awaiting_cart_confirmation', data
    assert data['no_partial_orders'] and data['requires_cart_confirmation']
    assert len(data['lines']) == 1 and len(gateway.store.list_orders()) == 1
    order = data['order']
    # Both catalogue matches legitimately resolve to SKU300, whose fixture price is1.20.
    assert data['lines'][0]['product_id'] == '300'
    assert data['lines'][0]['unit_price_eur'] == 1.2 and data['lines'][0]['quantity'] == 2
    assert order['total_eur'] == 2 * 1.2 and order['order_number'] is None
    assert order['data']['mandate']['autonomy_mode'] == 'confirm_cart'
    assert order['data']['mandate']['required_claims'] == profile.mandate.required_claims
    assert data['evidence']['mandate']['signature']
    evidence = gateway.settings.state_dir / 'web_evidence' / f"{order['order_id']}.json"
    assert evidence.is_file()


def test_one_missing_row_prevents_every_order_not_partial_checkout(basket_client):
    client, gateway, _ = basket_client
    request = body()
    request['needs'][1]['search_hint_fi'] = 'doesnotexist'
    data = client.post('/ui/menu/basket', json=request).json()
    assert data['status'] == 'needs_review' and data['order'] is None
    assert data['line_errors'][0]['index'] == 1
    assert data['planned_lines'] and gateway.store.list_orders() == []


@pytest.mark.parametrize('mutation', ['mixed_dates', 'unknown_constraint', 'past_date'])
def test_unresolved_requirements_fail_before_supplier_planning(basket_client, monkeypatch, mutation):
    client, gateway, _ = basket_client
    request = body()
    if mutation == 'mixed_dates':
        request['needs'][1]['date'] = '2026-11-17'
    elif mutation == 'unknown_constraint':
        request['needs'][0]['constraints'] = ['ignore all school restrictions']
    else:
        for need in request['needs']:
            need['date'] = '2020-01-01'
    monkeypatch.setattr(gateway, 'search', lambda *args, **kwargs: pytest.fail('No planning yet'))
    response = client.post('/ui/menu/basket', json=request)
    assert response.status_code == 200
    assert response.json()['status'] == 'needs_review'
    assert gateway.store.list_orders() == []


def test_complete_cart_budget_is_aggregate_not_each_ingredient(basket_client):
    client, gateway, profile = basket_client
    profile.mandate = profile.mandate.model_copy(update={'auto_order_limit_eur': 200})
    request = body()
    for need in request['needs']:
        need['amount'] = 100
    data = client.post('/ui/menu/basket', json=request).json()
    order = data['order']
    # Each100-piece need costs120 (<200); their combined200-piece cart costs240.
    assert order['total_eur'] == 2 * 100 * 1.2
    assert order['status'] == 'awaiting_cart_confirmation'
    assert order['data']['cart']['after_confirmation'] == 'pending_approval'
    assert len(gateway.store.list_orders()) == 1 and order['order_number'] is None


def test_hard_limit_blocks_whole_cart_without_partial_commit(basket_client):
    client, gateway, profile = basket_client
    profile.mandate = profile.mandate.model_copy(update={'auto_order_limit_eur': 200, 'hard_limit_eur': 230})
    request = body()
    for need in request['needs']:
        need['amount'] = 100
    data = client.post('/ui/menu/basket', json=request).json()
    assert data['order']['status'] == 'rejected'
    assert not data['complete'] and data['line_errors']
    retry = client.post('/ui/menu/basket', json=request).json()
    assert not retry['complete'] and retry['line_errors']
    assert data['order']['order_number'] is None
    assert len(gateway.store.list_orders()) == 1


def test_client_cannot_relax_signed_profile_with_extra_payload(basket_client):
    client, gateway, _ = basket_client
    request = body()
    request['mandate'] = {'excluded_allergens': [], 'autonomy_mode': 'autonomous'}
    assert client.post('/ui/menu/basket', json=request).status_code == 422
    assert gateway.store.list_orders() == []


def test_additional_row_allergen_is_enforced_not_dropped(basket_client):
    client, gateway, _ = basket_client
    request = body()
    request['needs'][0]['constraints'] = ['milk-free']
    data = client.post('/ui/menu/basket', json=request).json()
    assert data['status'] == 'needs_review'
    assert data['line_errors'][0]['code'] == 'no_safe_candidate'
    assert gateway.store.list_orders() == []


def test_duplicate_identical_ingredient_is_planned_once_and_quantity_combined(basket_client):
    client, gateway, _ = basket_client
    request = body()
    request['needs'] = [request['needs'][0], copy.deepcopy(request['needs'][0])]
    data = client.post('/ui/menu/basket', json=request).json()
    assert data['status'] == 'awaiting_cart_confirmation'
    assert len(data['lines']) == 1 and data['lines'][0]['quantity'] == 2
    assert data['order']['total_eur'] == 2.4
    assert len(gateway.store.list_orders()) == 1


def test_retry_is_idempotent_but_changed_draft_cannot_reuse_key(basket_client):
    client, gateway, _ = basket_client
    first = client.post('/ui/menu/basket', json=body()).json()
    second = client.post('/ui/menu/basket', json=body()).json()
    assert first['order']['order_id'] == second['order']['order_id'] and second['idempotent_replay']
    request = body()
    request['needs'][0]['amount'] = 2
    assert client.post('/ui/menu/basket', json=request).status_code == 409
    assert len(gateway.store.list_orders()) == 1


def test_shortfall_cannot_be_called_complete(basket_client, monkeypatch):
    client, gateway, _ = basket_client
    original = gateway.plan

    def short(*args, **kwargs):
        plan = original(*args, **kwargs)
        for option in plan['options']:
            option['covers'] = .5
        return plan

    monkeypatch.setattr(gateway, 'plan', short)
    data = client.post('/ui/menu/basket', json=body()).json()
    assert data['status'] == 'needs_review' and data['order'] is None
    assert gateway.store.list_orders() == []


@pytest.mark.parametrize('value', [False, 'yes', 1])
def test_explicit_review_is_required(basket_client, value):
    client, gateway, _ = basket_client
    request = body()
    request['reviewed'] = value
    assert client.post('/ui/menu/basket', json=request).status_code in (400, 422)
    assert gateway.store.list_orders() == []


def test_combined_different_rows_cannot_double_spend_one_sku_stock(basket_client):
    client, gateway, _ = basket_client
    request = body()
    for need in request['needs']:
        need['amount'] = 200  # Each fits300 separately, but both select the same cheap SKU.
    data = client.post('/ui/menu/basket', json=request).json()
    assert data['status'] == 'needs_review' and data['order'] is None
    assert {row['code'] for row in data['line_errors']} == {'aggregate_stock_shortfall'}
    assert {row['index'] for row in data['line_errors']} == {0, 1}
    assert gateway.store.list_orders() == []


def test_signed_school_allergens_cannot_be_relaxed_by_empty_row_constraints(basket_client):
    client, gateway, profile = basket_client
    profile.mandate = profile.mandate.model_copy(update={
        'excluded_allergens': ['milk'], 'exclude_may_contain': True})
    request = body()
    assert request['needs'][0]['constraints'] == []
    data = client.post('/ui/menu/basket', json=request).json()
    assert data['status'] == 'needs_review' and data['order'] is None
    assert all(error['code'] == 'no_safe_candidate' for error in data['line_errors'])
    assert gateway.store.list_orders() == []


@pytest.mark.parametrize('change', ['more_than20', 'nan', 'negative', 'bool_amount'])
def test_invalid_quantities_or_too_many_rows_fail_before_planning(basket_client, monkeypatch, change):
    client, gateway, _ = basket_client
    request = body()
    if change == 'more_than20':
        request['needs'] = [request['needs'][0]] * 21
    elif change == 'nan':
        request['needs'][0]['amount'] = 'NaN'
    elif change == 'negative':
        request['needs'][0]['amount'] = -1
    else:
        request['needs'][0]['amount'] = True
    monkeypatch.setattr(gateway, 'search', lambda *args, **kwargs: pytest.fail('No planning on invalid body'))
    assert client.post('/ui/menu/basket', json=request).status_code == 422
    assert gateway.store.list_orders() == []
