"""REST callers get the same signed-basket choice path as in-process agents."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from trade_agents import api
from trade_agents.buyer_agent.profiles import BuyerProfile
from trade_agents.buyer_agent.tools import RestTransport


def test_rest_choice_creation_idempotency_and_signed_offers(gateway, mandate, monkeypatch):
    monkeypatch.setitem(api.app.dependency_overrides, api.gateway, lambda: gateway)
    transport = RestTransport("http://testserver", client=TestClient(api.app))
    profile = BuyerProfile("hotel", "Hotel", mandate, max_premium_pct=5)
    offers = gateway.quote(["100", "300"], date(2026, 11, 16), amount=200, unit="L")
    options = [{"lines": [{**offer, "quantity": 200}]} for offer in offers]
    choice = transport.propose_choice(profile, options, "rest-choice")
    assert choice["status"] == "awaiting_choice"
    assert gateway.store.list_orders() == []
    assert transport.propose_choice(profile, options, "rest-choice")["choice_id"] == choice["choice_id"]
    options[0]["lines"][0]["signature"] = "forged"
    with pytest.raises(ValueError, match="fewer than two"):
        transport.propose_choice(profile, options, "bad-rest-choice")
