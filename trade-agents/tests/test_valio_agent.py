"""Valio's review agent: deterministic checks, grounded explanation, hook, Valio view."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trade_agents import valio_agent
from trade_agents.llm import LLMResponse, ScriptedLLM
from trade_agents.service import OrderLineIn

MONDAY = date(2026, 11, 16)


@pytest.fixture(autouse=True)
def _review_on(monkeypatch):
    monkeypatch.setenv("TTA_VALIO_REVIEW", "sync")


def _order(gw, mandate, pid, qty, key):
    offer = gw.quote([pid], MONDAY)[0]
    return gw.place_order(
        mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=qty)], key, "test",
        agent_passport=gw.issue_passport(mandate.agent_id, "test", "pytest", "autonomous"))


def _by(review, name):
    return [c for c in review["checks"] if c["check"] == name]


def _rv(gw, order_id):
    return gw.store.get_valio_review(order_id)


def test_hook_reviews_every_new_order(gateway, mandate):
    o = _order(gateway, mandate, "100", 100, "valio-k1")
    assert "valio_review" not in o["data"]          # buyer-facing return value is unchanged
    rv = _rv(gateway, o["order_id"])
    assert rv["advisory_only"] and rv["llm"]["status"] == "offline"
    assert rv["risk"] in ("low", "medium", "high") and rv["summary"]
    assert _by(rv, "history.first_order")[0]["status"] == "note"
    assert all(c["status"] == "ok" for c in _by(rv, "integrity.locked_offer"))
    assert _by(rv, "integrity.receipt")[0]["status"] == "ok"
    entries = gateway.audit(o["order_id"])["entries"]
    assert any(e["actor"] == "valio-agent" and e["event"] == "valio.reviewed" for e in entries)
    assert gateway.audit()["chain_valid"]


def test_history_quantity_spike_is_flagged(gateway, mandate):
    for i in range(3):
        _order(gateway, mandate, "100", 100, f"valio-hist-{i}")
    spike = _order(gateway, mandate, "100", 340, "valio-spike")   # 476 EUR, still auto
    rv = _rv(gateway, spike["order_id"])
    q = _by(rv, "history.quantity")[0]
    assert q["status"] == "flag" and q["data"]["usual"] == 100
    assert _by(rv, "history.unit_price")[0]["status"] == "ok"
    assert rv["risk"] in ("medium", "high")


def test_pending_order_notes_reliability_and_policy(gateway, mandate):
    o = _order(gateway, mandate, "300", 10, "valio-pending")       # reliability 0.8
    assert o["status"] == "pending_approval"
    rv = _rv(gateway, o["order_id"])
    assert _by(rv, "delivery.reliability")[0]["status"] == "note"
    assert _by(rv, "mandate.policy")[0]["status"] == "note"
    approved = gateway.approve(o["order_id"], "chef@hotel", "ok")
    rv2 = _rv(gateway, approved["order_id"])
    assert rv2["trigger"] == "order.placed" and _by(rv2, "mandate.humans")


def test_tampered_price_is_flagged(gateway, mandate):
    o = _order(gateway, mandate, "100", 10, "valio-tamper")
    data = o["data"]
    data["lines"][0]["offer"]["unit_price"] = 0.5
    gateway.store.update_order(o["order_id"], data=data)
    rv = valio_agent.review_order(gateway, o["order_id"], use_llm=False)
    assert _by(rv, "integrity.locked_offer")[0]["status"] == "flag"
    assert _by(rv, "integrity.total")[0]["status"] == "flag"


def test_llm_explanation_grounded_and_risk_floor(gateway, mandate, monkeypatch):
    monkeypatch.setenv("TTA_VALIO_REVIEW", "off")
    o = _order(gateway, mandate, "100", 10, "valio-llm")
    assert _rv(gateway, o["order_id"]) is None   # hook off
    reply = {"summary": "Hotel ordered 10 ST milk.", "risk": "low", "flags": [],
             "suggested_action": "None."}
    llm = ScriptedLLM([LLMResponse(json.dumps(reply))])
    data = gateway.order(o["order_id"])["data"]
    data["lines"][0]["offer"]["unit_price"] = 0.5     # two flags -> deterministic high
    gateway.store.update_order(o["order_id"], data=data)
    rv = valio_agent.review_order(gateway, o["order_id"], llm=llm)
    assert rv["llm"]["status"] == "ok" and rv["summary"] == reply["summary"]
    assert rv["risk"] == "high"                        # model cannot lower the rules' risk
    prompt = json.loads(llm.calls[0][0]["content"])
    assert prompt["order"]["order_id"] == o["order_id"] and prompt["checks"]


def test_llm_failure_keeps_deterministic_review(gateway, mandate, monkeypatch):
    monkeypatch.setenv("TTA_VALIO_REVIEW", "off")
    o = _order(gateway, mandate, "100", 10, "valio-bad")
    rv = valio_agent.review_order(gateway, o["order_id"],
                                  llm=ScriptedLLM([LLMResponse("not json")]))
    assert rv["llm"]["status"] == "unavailable" and rv["checks"] and rv["summary"]


def test_hook_never_breaks_buyer_flow(gateway, mandate, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("valio agent down")
    monkeypatch.setattr(valio_agent, "review_order", boom)
    o = _order(gateway, mandate, "100", 10, "valio-boom")
    assert o["status"] == "placed"


def test_async_mode(gateway, mandate, monkeypatch):
    import threading

    monkeypatch.setenv("TTA_VALIO_REVIEW", "async")
    o = _order(gateway, mandate, "100", 10, "valio-async")
    for t in threading.enumerate():
        if t.name == "valio-review":
            t.join(5)
    assert _rv(gateway, o["order_id"])["trigger"] == "order.created"


def test_rest_valio_view(gateway, mandate, monkeypatch):
    from trade_agents import api

    monkeypatch.setitem(api.app.dependency_overrides, api.gateway, lambda: gateway)
    o = _order(gateway, mandate, "300", 10, "valio-rest")
    c = TestClient(api.app)
    rows = c.get("/v1/valio/orders", params={"status": "pending_approval"}).json()
    assert [r["order_id"] for r in rows] == [o["order_id"]] and rows[0]["valio_review"]
    risk = rows[0]["valio_review"]["risk"]
    assert c.get("/v1/valio/orders", params={"risk": risk}).json()
    r = c.post(f"/v1/valio/orders/{o['order_id']}/review").json()
    assert r["trigger"] == "manual"
    assert c.post("/v1/valio/orders/ord_missing/review").status_code == 404


# -- review stored apart from order.data: no lost update with a human decision ------------

def test_review_interleaved_with_approve_keeps_approval_and_receipt(gateway, mandate,
                                                                   monkeypatch):
    from datetime import datetime

    monkeypatch.setenv("TTA_VALIO_REVIEW", "off")
    o = _order(gateway, mandate, "300", 10, "valio-race")
    assert o["status"] == "pending_approval"
    real = valio_agent.run_checks

    def checks_then_human_approves(gw, order):
        out = real(gw, order)              # review read the order while it was pending ...
        gw.approve(order["order_id"], "chef@hotel", "ok")   # ... a human approves meanwhile
        return out

    monkeypatch.setattr(valio_agent, "run_checks", checks_then_human_approves)
    rv = valio_agent.review_order(gateway, o["order_id"], use_llm=False)
    after = gateway.order(o["order_id"])
    assert after["status"] == "placed" and after["data"]["approvals"][0]["by"] == "chef@hotel"
    assert after["data"]["receipt"]["jws"] and "valio_review" not in after["data"]
    assert _rv(gateway, o["order_id"])["reviewed_at"] == rv["reviewed_at"]
    assert gateway.store.autonomous_spend_since(
        mandate.buyer_id, mandate.mandate_id,
        datetime.fromisoformat(after["created_at"])) == 0.0


# -- allergens: the review uses the same predicate as policy.evaluate ----------------------

class _Cat:
    def __init__(self, products, subs=None):
        self.products, self.subs = products, subs or {}

    def get(self, pid):
        return self.products.get(pid)

    def substitutes_of(self, pid):
        return self.subs.get(pid, [])


def _prod(pid, contains=(), may=(), claims=(), co2=None, rel=None):
    return SimpleNamespace(id=pid, name=f"P{pid}", contains=list(contains),
                           may_contain=list(may), claims=list(claims),
                           co2_per_kg=co2, reliability=rel)


def _safety(excluded, contains, claims=(), may=(), exclude_may=False):
    from trade_agents.valio_agent.checks import safety_checks

    order = {"data": {"mandate": {"excluded_allergens": excluded,
                                  "exclude_may_contain": exclude_may},
                      "lines": [{"quantity": 1, "offer": {
                          "product_id": "x", "name": "X", "contains_allergens": list(contains),
                          "may_contain_allergens": list(may), "claims": list(claims)}}]}}
    return {c["check"]: c["status"]
            for c in safety_checks(SimpleNamespace(catalog=_Cat({})), order)}


@pytest.mark.parametrize("excluded,contains", [
    (["gluten"], ["UW"]),       # wheat is gluten
    (["lactose"], ["AM"]),      # milk without a lactose-free claim
    (["nuts"], ["SH"]),         # hazelnut is a tree nut
    (["milk"], ["ML"]),         # lactose is milk
])
def test_allergen_families_flagged(excluded, contains):
    assert _safety(excluded, contains)["safety.allergen"] == "flag"


def test_allergen_lactose_free_claim_and_traces():
    assert _safety(["lactose"], ["AM"], ["FREE_FROM_LACTOSE"])["safety.allergen"] == "ok"
    assert _safety(["gluten"], [], may=["UW"])["safety.may_contain"] == "note"
    assert _safety(["gluten"], [], may=["UW"], exclude_may=True)["safety.may_contain"] == "flag"


def test_suggestion_respects_allergen_families():
    src = _prod("1", co2=2.0)
    bad = _prod("2", contains=["UW"], co2=1.0)        # greener, but wheat = gluten
    good = _prod("3", co2=1.5)
    order = {"data": {"mandate": {"excluded_allergens": ["gluten"]},
                      "lines": [{"quantity": 1, "offer": {"product_id": "1"}}]}}
    gw = SimpleNamespace(catalog=_Cat({"1": src, "2": bad, "3": good}, {"1": ["2", "3"]}))
    assert valio_agent.suggestion(gw, order)["product_id"] == "3"
    gw.catalog.subs = {"1": ["2"]}
    assert valio_agent.suggestion(gw, order) is None
