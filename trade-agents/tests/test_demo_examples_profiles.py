"""Deterministic UI presets and distinct restaurant buyer contract; no model calls."""
from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from trade_agents.bench.menu_eval import MENU_DIR
from trade_agents.buyer_agent.profiles import get_profile
from web import menu, server


def test_restaurant_profile_is_distinct_signed_and_has_no_invented_diets(gateway, monkeypatch):
    monkeypatch.setattr('trade_agents.buyer_agent.profiles.get_settings', lambda: gateway.settings)
    monkeypatch.delenv('TTA_DEMO_SELF_ISSUE', raising=False)
    monkeypatch.delenv('TTA_AGENT_PASSPORT', raising=False)
    monkeypatch.delenv('TTA_AGENT_PASSPORT_RESTAURANT_BUYER_AGENT', raising=False)
    restaurant = get_profile('restaurant')
    hotel, school = get_profile('hotel'), get_profile('school')
    assert restaurant.key == 'restaurant' and restaurant.mandate.buyer_id != hotel.mandate.buyer_id
    assert restaurant.mandate.buyer_id != school.mandate.buyer_id
    assert restaurant.mandate.required_claims == restaurant.mandate.excluded_allergens == []
    assert restaurant.mandate.substitution_policy == 'approval'
    assert restaurant.mandate.auto_order_limit_eur == 500
    assert restaurant.mandate.hard_limit_eur == 5000
    assert restaurant.mandate.agent_id == 'restaurant-buyer-agent'
    assert restaurant.passport is None  # New profile must preserve the passport cap.
    assert restaurant.mandate.signature
    assert 'sous-chef' in restaurant.context()


def test_handwritten_preset_returns_fixed_png_without_oracle_or_live_calls(monkeypatch):
    monkeypatch.setattr(menu, 'get_llm', lambda: (_ for _ in ()).throw(AssertionError('No model on preset')))
    client = TestClient(server.app)
    response = client.get('/ui/menu/handwritten-example')
    assert response.status_code == 200
    body = response.json()
    assert body['buyer'] == 'restaurant' and body['guests'] == 1 and body['synthetic'] is True
    assert 'expect' not in body and 'needs' not in body
    image = client.get(body['image_url'])
    assert image.status_code == 200 and image.headers['content-type'] == 'image/png'
    case = json.loads((MENU_DIR / 'handwritten-v1/01-kitchen-note.json').read_text())
    assert hashlib.sha256(image.content).hexdigest() == case['image']['sha256']
    assert '10 kg' not in body['text'] and 'Naudan' not in body['text']
    assert client.get('/ui/menu/handwritten-example/../../.env').status_code == 404


def test_techcard_preset_returns_three_inputs_not_oracle_or_model_output(monkeypatch):
    monkeypatch.setattr(menu, 'get_llm', lambda: (_ for _ in ()).throw(AssertionError('No model on preset')))
    body = TestClient(server.app).get('/ui/menu/example').json()
    assert body['buyer'] == 'restaurant' and body['guests'] == 1
    assert {doc['kind'] for doc in body['documents']} == {'menu', 'recipe', 'stock'}
    assert len(body['documents']) == 3
    for doc in body['documents']:
        data = json.loads(doc['content'])
        assert 'expect' not in data and 'oracle' not in doc['name']
        assert 'exact_oracle' not in data
    assert 'INPUTS:' not in body['text']
    assert 'expect' not in body and 'needs' not in body
    assert len(body['text']) + sum(len(doc['content']) for doc in body['documents']) < 12000


def test_models_routes_serve_only_fixed_sanitized_report():
    client = TestClient(server.app)
    for path in ('/models', '/models.js', '/models.css'):
        assert client.get(path).status_code == 200
    response = client.get('/data/model-comparison-20261114-v1.json')
    assert response.status_code == 200 and response.headers['content-type'] == 'application/json'
    data = response.json()
    assert data['preliminary'] is True
    assert data['provenance']['raw_supplier_payloads_published'] is False
    for path in ('/data/.env', '/data/other.json', '/models/../../.env'):
        assert client.get(path).status_code == 404


def test_restaurant_without_passport_waits_for_cart_confirmation(gateway, monkeypatch):
    from datetime import date

    from trade_agents.service import OrderLineIn

    monkeypatch.setattr('trade_agents.buyer_agent.profiles.get_settings', lambda: gateway.settings)
    for name in ('TTA_DEMO_SELF_ISSUE', 'TTA_AGENT_PASSPORT',
                 'TTA_AGENT_PASSPORT_RESTAURANT_BUYER_AGENT'):
        monkeypatch.delenv(name, raising=False)
    profile = get_profile('restaurant')
    offer = gateway.quote(['100'], date(2026, 11, 16))[0]
    result = gateway.place_order(profile.mandate,
                                 [OrderLineIn(offer_id=offer['offer_id'], quantity=1)],
                                 'restaurant-passport-test', agent_passport=profile.passport)
    assert result['status'] == 'awaiting_cart_confirmation'
    assert result['order_number'] is None
    assert result['data']['autonomy']['effective'] == 'confirm_cart'


def test_menu_endpoint_accepts_real_restaurant_profile_without_hotel_claims(gateway, monkeypatch):
    from trade_agents.llm import LLMResponse, ScriptedLLM

    monkeypatch.setattr('trade_agents.buyer_agent.profiles.get_settings', lambda: gateway.settings)
    reply = {'needs': [{'ingredient': 'pasta', 'amount': 1, 'unit': 'KG', 'date': '2026-11-20'}],
             'assumptions': ['Supplied quantity; chef review required.'], 'warnings': []}
    model = ScriptedLLM([LLMResponse(text=json.dumps(reply))], name='scripted:restaurant-menu')
    monkeypatch.setattr(menu, 'get_llm', lambda: model)
    response = TestClient(server.app).post('/ui/menu/draft', json={
        'buyer': 'restaurant', 'guests': 1, 'text': 'Need pasta 1 KG for 2026-11-20; review only.'})
    assert response.status_code == 200
    draft = response.json()
    assert draft['mandate_constraints']['required_claims'] == []
    assert draft['mandate_constraints']['excluded_allergens'] == []
    assert draft['requires_review'] and draft['requires_approval']
    assert gateway.store.list_orders() == []
