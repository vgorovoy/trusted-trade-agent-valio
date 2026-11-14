"""HTTP menu drafting is bounded, review-only, and never places an order."""
import base64
import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trade_agents.buyer_agent.profiles import BuyerProfile
from trade_agents.llm import LLMResponse
from web import menu


@pytest.fixture
def menu_client(gateway, mandate, monkeypatch):
    profile = BuyerProfile("school", "Strict school", mandate,
                           default_delivery=date(2026, 11, 16))
    monkeypatch.setattr(menu, "get_profile", lambda key: profile)
    seen = []

    class Model:
        name = "menu-test"
        vision = True

        def chat(self, messages, *args, **kwargs):
            seen.append(messages)
            return LLMResponse(text=json.dumps({"needs": [
                {"ingredient": "pasta", "amount": 80, "unit": "KG", "date": "2026-11-16",
                 "constraints": [], "for_dishes": ["macaroni casserole"], "search_hint_fi": "makaroni"},
                {"ingredient": "oat drink", "amount": 100, "unit": "L", "date": "2026-11-16",
                 "constraints": ["milk-free"], "for_dishes": ["macaroni casserole"]}],
                "assumptions": ["Recipe quantities are estimates; check portions."],
                "warnings": ["Existing inventory is unknown."]}))

    monkeypatch.setattr(menu, "get_llm", lambda: Model())
    app = FastAPI()
    app.include_router(menu.router)
    return TestClient(app), gateway, seen


def test_text_menu_returns_review_draft_without_order(menu_client):
    client, gateway, seen = menu_client
    response = client.post("/ui/menu/draft", json={
        "text": "makarooni lattikko for 1000 kids, buy ingredients", "guests": 1000})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "draft"
    assert data["requires_review"] is True
    assert data["guests"] == 1000
    assert {need["ingredient"] for need in data["needs"]} == {"pasta", "oat drink"}
    assert data["assumptions"] and data["warnings"]
    assert gateway.store.list_orders() == []
    assert len(seen) == 1


def test_image_draft_and_offline_vision_message(menu_client, monkeypatch):
    client, gateway, seen = menu_client
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic-test").decode()
    body = {"text": "school menu", "guests": 1000, "image_base64": encoded, "mime": "image/png"}
    response = client.post("/ui/menu/draft", json=body)
    assert response.status_code == 200, response.text
    assert seen[0][0]["images"][0][1] == "image/png"

    class Offline:
        vision = False

    monkeypatch.setattr(menu, "get_llm", Offline)
    response = client.post("/ui/menu/draft", json=body)
    assert response.status_code == 400
    assert "offline mode does not support image drafts" in response.json()["detail"]
    assert gateway.store.list_orders() == []


@pytest.mark.parametrize("extra", [
    {"image_base64": "not base64!", "mime": "image/png"},
    {"image_base64": "YWJj", "mime": "image/png"},
    {"image_base64": "YWJj", "mime": "image/svg+xml"},
    {"mime": "image/png"},
    {"buyer": "invented-kitchen"},
    {"text": "   "},
])
def test_invalid_menu_input_fails_before_model(menu_client, extra):
    client, gateway, seen = menu_client
    response = client.post("/ui/menu/draft", json={"text": "menu", "guests": 100, **extra})
    assert response.status_code == 400
    assert seen == []
    assert gateway.store.list_orders() == []


@pytest.mark.parametrize("extra", [{"guests": True}, {"guests": 0}, {"guests": 10001},
                                   {"text": "a" * 12001},
                                   {"image_base64": "A" * (4 * ((menu.MAX_IMAGE_BYTES + 2) // 3) + 4)}])
def test_menu_payload_limits(menu_client, extra):
    client, _, seen = menu_client
    response = client.post("/ui/menu/draft", json={"text": "menu", "guests": 100, **extra})
    assert response.status_code == 422
    assert seen == []


def test_provider_error_is_redacted_and_no_order_created(menu_client, monkeypatch):
    client, gateway, _ = menu_client

    def fail():
        raise RuntimeError("private credential in a provider exception")

    monkeypatch.setattr(menu, "get_llm", fail)
    response = client.post("/ui/menu/draft", json={"text": "menu", "guests": 100})
    assert response.status_code == 502
    assert "private credential" not in response.text
    assert "No order was created" in response.text
    assert gateway.store.list_orders() == []
