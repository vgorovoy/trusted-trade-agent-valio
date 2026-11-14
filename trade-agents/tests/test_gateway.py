from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from trade_agents.catalog import parse_name_size
from trade_agents.config import TZ
from trade_agents.offers import assess_delivery, snap
from trade_agents.service import GatewayError, OrderLineIn

MONDAY = date(2026, 11, 16)


# -- parsing and normalisation -------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Valio rasvaton maito 1 l laktoositon UHT", (1.0, "L")),
    ("Valio kahvimaito 5 dl laktoositon", (0.5, "L")),
    ("Valio kevytmaito 0,5 l laktoositon UHT", (0.5, "L")),
    ("Forssan Perunasalaatti 400g", (0.4, "KG")),
    ("6 x Saarioinen Vadelmahillo 500g", None),
    ("Arla Annosmaito 100x2 cl laktoositon", None),
    ("Munkkimiehet Lihapasteija 60x90g", None),
])
def test_parse_name_size(name, expected):
    assert parse_name_size(name) == expected


def test_snap_price_ratio():
    assert snap(13.32 / 1.33) == 10
    assert snap(15.09 / 7.55) == 2
    assert snap(1.0) == 1.0


def test_delivery_states():
    now = datetime(2026, 11, 13, 21, 0, tzinfo=TZ)
    line = {"salesBlock": False, "availableAmount": 51.0, "availableDate": "2026-11-16",
            "salesUnit": "ST"}
    rec = {"closingTime": "2026-11-15T17:00:00+0200", "scheduleLines": [line]}
    assert assess_delivery(rec, MONDAY, 10, now).status == "available"
    assert assess_delivery(rec, MONDAY, 100, now).status == "partial"
    assert assess_delivery(rec, date(2026, 11, 15), 10, now).status == "late"
    passed = {**rec, "closingTime": "2026-11-12T15:00:00+0200"}
    assert assess_delivery(passed, MONDAY, 10, now).status == "cutoff_passed"
    oos = {"scheduleLines": [{"salesBlock": False, "availableAmount": 0.0, "salesUnit": "ST"}]}
    assert assess_delivery(oos, MONDAY, 1, now).status == "out_of_stock"
    assert assess_delivery({"scheduleLines": []}, MONDAY, 1, now).status == "out_of_stock"
    big = {**rec, "scheduleLines": [{**line, "availableAmount": 666666666.0}]}
    d = assess_delivery(big, MONDAY, 10_000, now)
    assert d.status == "available" and d.available_amount is None


# -- catalogue ---------------------------------------------------------------------

def test_search_filters(catalog):
    ids = [p.id for p in catalog.search("maito")]
    assert set(ids) == {"100", "200", "300"}
    assert [p.id for p in catalog.search("1 l", require_claims=["FREE_FROM_LACTOSE"],
                                         exclude_allergens=["milk"])] == []
    assert [p.id for p in catalog.search("juoma", exclude_allergens=["oats"])] == []
    assert catalog.get("300").substitutes == ["100"]


# -- planning ------------------------------------------------------------------------

def test_plan_rules_out_cutoff_and_ranks_by_price(gateway):
    plan = gateway.plan("maito", 200, "L", MONDAY, require_claims=["FREE_FROM_LACTOSE"])
    reasons = {r["product_id"]: r["reason"] for r in plan["ruled_out"]}
    assert "cut-off" in reasons["200"]
    best = plan["options"][0]
    assert best["lines"][0]["product_id"] == "300"          # 1.20 EUR/L, 300 available
    assert best["total_eur"] == 240.0


def test_plan_splits_when_only_partial(gateway):
    plan = gateway.plan("maito", 1000, "L", MONDAY, product_ids=["300"])
    assert plan["options"] == []
    assert plan["partial_only"][0]["product_id"] == "300"
    plan = gateway.plan("maito", 1000, "L", MONDAY, product_ids=["300", "100"])
    kinds = [o["kind"] for o in plan["options"]]
    assert any(k.startswith("split") for k in kinds)
    split = next(o for o in plan["options"] if o["kind"].startswith("split"))
    assert split["covers"] == 1000
    assert [ln["product_id"] for ln in split["lines"]] == ["300", "100"]


# -- ordering and guardrails ------------------------------------------------------------

def _order(gateway, mandate, product_id, qty, key, **line):
    offer = gateway.quote([product_id], MONDAY)[0]
    return gateway.place_order(
        mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=qty, **line)], key, "test",
        agent_passport=gateway.issue_passport(mandate.agent_id, "test", "pytest", "autonomous"))


def test_auto_order_within_mandate(gateway, mandate):
    o = _order(gateway, mandate, "100", 100, "key-auto-1")
    assert o["status"] == "placed" and o["order_number"].startswith("VA-")
    assert o["total_eur"] == 140.0
    again = _order(gateway, mandate, "100", 100, "key-auto-1")
    assert again["order_id"] == o["order_id"]                       # idempotent


def test_over_limit_needs_approval_then_approved(gateway, mandate):
    o = _order(gateway, mandate, "100", 1000, "key-limit-1")
    assert o["status"] == "pending_approval"
    assert any("autonomous limit" in r for r in o["data"]["decision"]["reasons"])
    with pytest.raises(GatewayError):
        gateway.approve(o["order_id"], "intern@hotel")              # not an approver
    done = gateway.approve(o["order_id"], "chef@hotel", "ok for the conference")
    assert done["status"] == "placed" and done["order_number"]


def test_blocks(gateway, mandate, sign):
    o = _order(gateway, mandate, "300", 500, "key-partial-1")       # only 300 available
    assert o["status"] == "rejected"
    o = _order(gateway, mandate, "200", 10, "key-cutoff-1")         # cut-off passed
    assert o["status"] == "rejected"
    strict = sign(mandate.model_copy(update={"excluded_allergens": ["milk"]}))
    o = _order(gateway, strict, "100", 10, "key-allergen-1")
    assert o["status"] == "rejected"
    assert any("milk" in r for r in o["data"]["decision"]["reasons"])
    forged = mandate.model_copy(update={"auto_order_limit_eur": 1e9})  # signature now invalid
    o = _order(gateway, forged, "100", 10, "key-forged-1")
    assert o["status"] == "rejected"


def test_substitution_and_soft_rules(gateway, mandate, sign):
    # official Valio substitute (300 -> 100) under official_auto, but 100 costs 1.40 EUR/L
    # against 300's 1.20 (+16.7 %): above the mandate's +10 %, a human approves
    o = _order(gateway, mandate, "100", 10, "key-sub-1", substitutes_for="300")
    assert o["status"] == "pending_approval"
    assert any("+16.7 %" in r and "300" in r for r in o["data"]["decision"]["reasons"])
    # the buyer tolerates +20 %: the official substitute passes without a human
    lenient = sign(mandate.model_copy(update={"max_substitute_price_increase_pct": 20}))
    o = _order(gateway, lenient, "100", 10, "key-sub-1b", substitutes_for="300")
    assert o["status"] == "placed"
    # unofficial substitute needs a human
    o = _order(gateway, mandate, "300", 10, "key-sub-2", substitutes_for="100")
    assert o["status"] == "pending_approval"
    # 300 has reliability 0.8 < 0.9 -> approval as well
    o = _order(gateway, mandate, "300", 10, "key-rel-1")
    assert o["status"] == "pending_approval"
    assert any("reliability" in r for r in o["data"]["decision"]["reasons"])


def test_expired_mandate_rejected(gateway, mandate, sign):
    old = mandate.model_copy(update={"valid_until": datetime.now(TZ) - timedelta(hours=1)})
    o = _order(gateway, sign(old), "100", 10, "key-exp-1")
    assert o["status"] == "rejected"


def test_audit_chain(gateway, mandate):
    o = _order(gateway, mandate, "100", 1000, "key-audit-1")
    gateway.reject(o["order_id"], "chef@hotel", "too much")
    trail = gateway.audit(o["order_id"])
    assert trail["chain_valid"]
    events = [e["event"] for e in trail["entries"]]
    assert events == ["order.received", "policy.needs_approval", "approval.denied"]
    gateway.store.con.execute("UPDATE audit SET summary = 'edited' WHERE seq = 2")
    assert gateway.audit()["chain_valid"] is False
