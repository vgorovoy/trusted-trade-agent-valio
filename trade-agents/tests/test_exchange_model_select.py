"""Optional buyer-model selector on the Exchange product-request endpoint.

All model calls are replaced by the deterministic offline agent; no live provider is called.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trade_agents import api
from trade_agents.buyer_agent.offline import OfflineBuyerLLM
from trade_agents.buyer_agent.profiles import BuyerProfile
from trade_agents.config import TZ
from trade_agents.policy import Mandate
from web import live_models, server

REQUEST = "1000 litres of lactose-free milk by Monday"
CREDENTIAL_ENVS = sorted({key for value in live_models.MODELS.values() for key in value[2]})


@pytest.fixture
def client(gateway, settings, monkeypatch):
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
    for key in CREDENTIAL_ENVS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("TTA_LLM_MODEL", raising=False)
    monkeypatch.setenv("TTA_LLM_PROVIDER", "offline")
    monkeypatch.setenv("TTA_DEMO_WALLET", "1")
    monkeypatch.setattr(server, "gateway", lambda: gateway)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "get_profile", lambda key: profile)
    monkeypatch.setattr(server, "sign_mandate",
                        lambda m: m.sign(gateway.buyers.demo_key(m.buyer_id)))
    api.app.dependency_overrides[api.gateway] = lambda: gateway
    try:
        yield TestClient(server.app)
    finally:
        api.app.dependency_overrides.clear()


def _forbid_live(*args, **kwargs):
    raise AssertionError("no live model may be built in this test")


def _run(client, **extra):
    return client.post("/ui/agent/run", json={
        "buyer": "hotel", "mode": "confirm_cart", "request": REQUEST, **extra})


def test_default_path_unchanged(client, monkeypatch):
    monkeypatch.setattr(server, "bounded_model", _forbid_live)
    calls = []
    original = server.run_request
    monkeypatch.setattr(server, "run_request",
                        lambda *a, **kw: calls.append(1) or original(*a, **kw))
    response = _run(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert calls == [1]  # the default (deploy-overridable) path still runs
    assert result["status"] == "awaiting_cart_confirmation"
    assert result["model"] == "offline:rules"
    assert result["buyer_model"]["provider"] == "offline"
    order = next(o for o in client.get("/ui/overview").json()["orders"]
                 if o["order_id"] == result["order_id"])
    assert order["verification"]["mandate"] is True
    assert order["buyer_model"]["label"] == "Offline rule-based agent"


def test_models_endpoint_lists_only_configured(client, monkeypatch):
    monkeypatch.setenv("TTA_LIVE_BENCH", "1")
    monkeypatch.setenv("TTA_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    data = client.get("/ui/agent/models").json()
    assert data["default"]["label"] == "Gemini 3.5 Flash"
    assert data["options"] == []  # Gemini is the default; nothing else configured

    monkeypatch.setenv("VERDA_INFERENCE_API_KEY", "test-not-a-real-key")
    data = client.get("/ui/agent/models").json()
    ids = [o["id"] for o in data["options"]]
    assert ids == ["verda", "verda-deepseek"]
    assert all("on Verda" in o["label"] for o in data["options"])
    assert "test-not-a-real-key" not in str(data)

    monkeypatch.delenv("TTA_LIVE_BENCH")
    assert client.get("/ui/agent/models").json()["options"] == []


def test_valid_provider_runs_bounded_model_and_is_recorded(client, monkeypatch):
    monkeypatch.setenv("TTA_LIVE_BENCH", "1")
    monkeypatch.setenv("VERDA_INFERENCE_API_KEY", "test-not-a-real-key")
    built, closed = [], []

    def fake_bounded(provider, model):
        built.append((provider, model))
        llm = OfflineBuyerLLM()
        llm.name = f"{provider}:{model}"
        llm.client = SimpleNamespace(close=lambda: closed.append(1))
        return llm

    monkeypatch.setattr(server, "bounded_model", fake_bounded)
    monkeypatch.setattr(server, "run_request", _forbid_live)
    response = _run(client, provider="verda-deepseek")
    assert response.status_code == 200, response.text
    result = response.json()
    assert built == [("verda-deepseek", "deepseek-4-1-flash-1017")]
    assert closed == [1]
    assert result["model"] == "verda-deepseek:deepseek-4-1-flash-1017"
    assert result["buyer_model"] == {"provider": "verda-deepseek",
                                     "model": "deepseek-4-1-flash-1017",
                                     "label": "DeepSeek 4.1 Flash on Verda"}
    assert result["status"] == "awaiting_cart_confirmation"
    order = next(o for o in client.get("/ui/overview").json()["orders"]
                 if o["order_id"] == result["order_id"])
    assert order["buyer_model"]["label"] == "DeepSeek 4.1 Flash on Verda"
    assert order["verification"]["mandate"] is True


@pytest.mark.parametrize("provider,env", [
    ("not-a-model", {"TTA_LIVE_BENCH": "1"}),
    ("verda-deepseek", {"TTA_LIVE_BENCH": "1"}),             # no credential
    ("verda-nemotron", {"TTA_LIVE_BENCH": "1", "VERDA_INFERENCE_API_KEY": "x"}),  # wrong key
    ("gemini", {"GEMINI_API_KEY": "x"}),                       # live models not enabled
    ("", {"TTA_LIVE_BENCH": "1"}),
])
def test_unknown_or_unconfigured_provider_rejected(client, monkeypatch, provider, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(server, "bounded_model", _forbid_live)
    monkeypatch.setattr(server, "run_request", _forbid_live)  # no silent fallback
    response = _run(client, provider=provider)
    assert response.status_code == 400
    assert client.get("/ui/overview").json()["orders"] == []


def _fake_bounded(built):
    def fake(provider, model):
        built.append((provider, model))
        llm = OfflineBuyerLLM()
        llm.name = f"{provider}:{model}"
        llm.client = SimpleNamespace(close=lambda: None)
        return llm
    return fake


def _hosted_env(monkeypatch, nemotron: bool):
    monkeypatch.setenv("TTA_LIVE_BENCH", "1")
    monkeypatch.setenv("TTA_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    if nemotron:
        monkeypatch.setenv("VERDA_INFERENCE_KEY", "test-not-a-real-key")


def test_nemotron_on_verda_is_default_when_configured(client, monkeypatch):
    _hosted_env(monkeypatch, nemotron=True)
    data = client.get("/ui/agent/models").json()
    assert data["default"] == {"id": "", "provider": "verda-nemotron",
                               "model": "nemotron-3.5-super-vl",
                               "label": "Nemotron 3.5 Super VL on Verda"}
    assert [o["id"] for o in data["options"]] == ["gemini"]  # Gemini stays selectable
    built = []
    monkeypatch.setattr(server, "bounded_model", _fake_bounded(built))
    monkeypatch.setattr(server, "run_request", _forbid_live)
    result = _run(client).json()
    assert built == [("verda-nemotron", "nemotron-3.5-super-vl")]
    assert result["buyer_model"]["label"] == "Nemotron 3.5 Super VL on Verda"
    order = next(o for o in client.get("/ui/overview").json()["orders"]
                 if o["order_id"] == result["order_id"])
    assert order["buyer_model"]["provider"] == "verda-nemotron"

    # An explicit Gemini choice runs the bounded Gemini model, not Nemotron.
    result = _run(client, provider="gemini").json()
    assert built[-1] == ("gemini", "gemini-3.5-flash")
    assert result["buyer_model"]["label"] == "Gemini 3.5 Flash"


def test_gemini_default_when_nemotron_not_configured(client, monkeypatch):
    _hosted_env(monkeypatch, nemotron=False)
    data = client.get("/ui/agent/models").json()
    assert data["default"]["label"] == "Gemini 3.5 Flash"
    assert data["default"]["provider"] == "gemini"
    monkeypatch.setattr(server, "bounded_model", _forbid_live)
    calls = []
    from trade_agents.buyer_agent.agent import run_request as real_run

    def default_path(request, profile, **kw):  # stands in for the deployed Gemini buyer
        calls.append(1)
        return real_run(request, profile, llm=OfflineBuyerLLM(), **kw)

    monkeypatch.setattr(server, "run_request", default_path)
    result = _run(client).json()
    assert calls == [1]
    assert result["buyer_model"]["label"] == "Gemini 3.5 Flash"


def test_nemotron_failure_is_not_silently_replaced(client, monkeypatch):
    _hosted_env(monkeypatch, nemotron=True)

    class Failing:
        name, vision = "verda-nemotron:nemotron-3.5-super-vl", True
        client = SimpleNamespace(close=lambda: None)

        def chat(self, *args, **kwargs):
            raise ConnectionError("Live provider request failed; diagnostics omitted")

    monkeypatch.setattr(server, "bounded_model", lambda provider, model: Failing())
    monkeypatch.setattr(server, "run_request", _forbid_live)  # no Gemini fallback
    result = _run(client).json()
    assert result["status"] == "error"
    assert result["buyer_model"]["provider"] == "verda-nemotron"
