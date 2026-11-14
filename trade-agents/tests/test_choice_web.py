"""The human-only choice surface preserves safety and the existing signed evidence UI."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from trade_agents.buyer_agent.profiles import BuyerProfile
from trade_agents.choices import ChoiceService
from web import server


@pytest.fixture
def choice_ui(gateway, settings, mandate, monkeypatch):
    profile = BuyerProfile("hotel", "Test kitchen", mandate,
                           passport=gateway.issue_passport("agent-1", "offline", "pytest",
                                                           "autonomous"))
    options = [gateway.plan("maito", 10, "L", date(2026, 11, 16), product_ids=[pid])["options"][0]
               for pid in ("100", "300")]
    choice = ChoiceService(gateway).create(profile, options, "web-choice")
    monkeypatch.setattr(server, "gateway", lambda: gateway)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setenv("TTA_DEMO_WALLET", "1")
    return TestClient(server.app), choice, gateway


def test_choice_is_visible_without_order_and_wallet_is_required(choice_ui, monkeypatch):
    client, choice, gateway = choice_ui
    overview = client.get("/ui/overview").json()
    assert overview["orders"] == []
    assert overview["choices"][0]["choice_id"] == choice["choice_id"]
    monkeypatch.delenv("TTA_DEMO_WALLET")
    response = client.post(f"/ui/choices/{choice['choice_id']}/decision",
                           json={"approver": "chef@hotel", "selection": 0})
    assert response.status_code == 404
    assert gateway.store.list_orders() == []


@pytest.mark.parametrize("selection", [0, 1, None])
def test_human_choice_decision_and_signed_evidence(choice_ui, selection):
    client, choice, gateway = choice_ui
    endpoint = f"/ui/choices/{choice['choice_id']}/decision"
    denied = client.post(endpoint, json={"approver": "outsider", "selection": selection})
    assert denied.status_code == 400
    assert gateway.store.list_orders() == []
    payload = {"approver": "chef@hotel", "selection": selection, "note": "Chef's decision"}
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == ("declined" if selection is None else "selected")
    repeated = client.post(endpoint, json=payload)
    assert repeated.status_code == 200
    orders = client.get("/ui/overview").json()["orders"]
    if selection is None:
        assert orders == []
    else:
        assert len(orders) == 1
        assert orders[0]["status"] == "placed"
        assert orders[0]["verification"]["mandate"] is True
        assert all(orders[0]["verification"]["offers"])
        assert orders[0]["verification"]["receipt"] is True
    assert gateway.audit()["chain_valid"]


def test_choice_route_rejects_invalid_selection(choice_ui):
    client, choice, gateway = choice_ui
    for selection in (-1, 2, True, "0"):
        response = client.post(f"/ui/choices/{choice['choice_id']}/decision",
                               json={"approver": "chef@hotel", "selection": selection})
        assert response.status_code == 422
    assert gateway.store.list_orders() == []
