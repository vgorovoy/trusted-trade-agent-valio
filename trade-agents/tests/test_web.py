"""The human window completes the agent → cart → approval flow."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from trade_agents import api
from trade_agents.buyer_agent.profiles import BuyerProfile
from trade_agents.config import TZ
from trade_agents.policy import Mandate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import server


def test_human_window_flow(gateway, settings, monkeypatch):
    mandate = Mandate(
        mandate_id="web-demo", buyer_id="hotel-1", buyer_name="Hotel",
        agent_id="hotel-agent", valid_until=datetime.now(TZ) + timedelta(days=1),
        auto_order_limit_eur=500, hard_limit_eur=5000,
        required_claims=["FREE_FROM_LACTOSE"], approvers=["chef@hotel"],
    ).sign(gateway.buyers.demo_key("hotel-1"))
    profile = BuyerProfile("hotel", "Test hotel", mandate,
                           default_delivery=date(2026, 11, 16),
                           passport=gateway.issue_passport(
                               "hotel-agent", "offline", "pytest", "autonomous"))
    monkeypatch.setenv("TTA_LLM_PROVIDER", "offline")
    monkeypatch.setenv("TTA_DEMO_WALLET", "1")
    monkeypatch.setattr(server, "gateway", lambda: gateway)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "get_profile", lambda key: profile)
    monkeypatch.setattr(server, "sign_mandate", lambda m: m.sign(gateway.buyers.demo_key(m.buyer_id)))
    api.app.dependency_overrides[api.gateway] = lambda: gateway
    try:
        client = TestClient(server.app)
        assert "Trade exchange" in client.get("/").text
        assert client.get("/app.js").status_code == 200
        assert client.get("/api/openapi.json").status_code == 200

        response = client.post("/ui/agent/run", json={
            "buyer": "hotel", "mode": "confirm_cart",
            "request": "1000 litres of lactose-free milk by Monday",
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "awaiting_cart_confirmation", result
        order_id = result["order_id"]

        overview = client.get("/ui/overview").json()
        order = next(o for o in overview["orders"] if o["order_id"] == order_id)
        assert order["verification"]["mandate"] is True
        assert all(order["verification"]["offers"])
        assert overview["audit"]["chain_valid"] is True

        signed = client.post("/api/demo/wallet/cart-confirmations", json={
            "order_id": order_id, "confirmed_by": "chef@hotel", "decision": "confirm",
        }).json()["confirmation"]
        confirmed = client.post(f"/api/v1/carts/{order_id}/confirm",
                                json={"confirmation": signed}).json()
        assert confirmed["status"] == "pending_approval"
        approved = client.post(f"/api/v1/approvals/{order_id}/approve",
                               json={"approver": "chef@hotel"}).json()
        assert approved["status"] == "placed"
        final = client.get("/ui/overview").json()
        assert final["orders"][0]["verification"]["receipt"] is True
        assert final["audit"]["chain_valid"] is True
    finally:
        api.app.dependency_overrides.clear()
