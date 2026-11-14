"""Stream M (Valio mentors' feedback): price delta on replacements, strict client types."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trade_agents.buyer_agent.profiles import PROFILES, STRICTNESS
from trade_agents.config import TZ
from trade_agents.offers import Delivery, Offer
from trade_agents.policy import LineProposal, PriceReference, evaluate
from trade_agents.service import OrderLineIn

MONDAY = date(2026, 11, 16)


def _offer(pid: str, price: float, comp_unit: str | None = "L") -> Offer:
    return Offer(
        product_id=pid, name=f"p{pid}", sales_unit="ST", lot_size=1.0,
        requested_date=MONDAY.isoformat(), unit_price=price, unit_price_with_tax=None,
        vat_rate=None, list_price=None, contract_price=False, price_valid_to=None,
        comparison_price=price if comp_unit else None, comparison_unit=comp_unit,
        content=None, delivery=Delivery("available", None, MONDAY.isoformat(), None, None),
        delivery_reliability=None, co2_kg_per_kg=None, contains_allergens=[],
        may_contain_allergens=[], claims=["FREE_FROM_LACTOSE"], labels=[],
        official_substitutes=[])


def _passport(gateway, mandate) -> str:
    return gateway.issue_passport(mandate.agent_id, "test", "pytest", "autonomous")


def _price_checks(mandate, line: LineProposal):
    d = evaluate(mandate, [line], now=datetime.now(TZ), signature_ok=True)
    return d, [c for c in d.checks if c.rule == "substitution.price"]


def test_price_delta_rule(mandate):
    ref = PriceReference("300", 1.20, "L", "test quote")
    # official substitute, official_auto, +16.7 % > +10 %: a human approves
    d, [c] = _price_checks(mandate, LineProposal(_offer("100", 1.40), 10, False, "300", True,
                                                 reference=ref))
    assert d.outcome == "needs_approval" and c.outcome == "approval" and "+16.7 %" in c.detail
    # within the limit: ok, official substitute placed without a human
    d, [c] = _price_checks(mandate, LineProposal(_offer("100", 1.25), 10, False, "300", True,
                                                 reference=ref))
    assert d.outcome == "auto" and c.outcome == "ok"
    # no verified price for the original: never trust the line, ask a human
    d, [c] = _price_checks(mandate, LineProposal(_offer("100", 1.25), 10, False, "300", True))
    assert d.outcome == "needs_approval" and "could not be verified" in c.detail
    # a reference for another product does not count
    other = PriceReference("999", 5.0, "L", "test quote")
    _, [c] = _price_checks(mandate, LineProposal(_offer("100", 1.25), 10, False, "300", True,
                                                 reference=other))
    assert c.outcome == "approval"
    # different bases (EUR/L vs EUR/ST) cannot be compared
    _, [c] = _price_checks(mandate, LineProposal(_offer("100", 1.25, None), 10, False, "300",
                                                 True, reference=ref))
    assert c.outcome == "approval" and "not comparable" in c.detail
    # the buyer switched the rule off
    off = mandate.model_copy(update={"max_substitute_price_increase_pct": None})
    _, checks = _price_checks(off, LineProposal(_offer("100", 9.0), 10, False, "300", True,
                                                reference=ref))
    assert checks == []
    # a plain line is never price-checked
    _, checks = _price_checks(mandate, LineProposal(_offer("100", 9.0), 10))
    assert checks == []


def test_split_with_dearer_top_up_needs_approval(gateway, mandate, sign):
    """The mentors' meat case in the test catalogue: only 300 of 500 L of product 300
    (1.20 EUR/L), the rest from 100 (1.40 EUR/L, +16.7 %)."""
    m = sign(mandate.model_copy(update={"min_delivery_reliability": None,
                                        "auto_order_limit_eur": 1000}))
    plan = gateway.plan("maito", 500, "L", MONDAY, product_ids=["100", "300"])
    split = next(o for o in plan["options"] if o["kind"].startswith("split"))
    assert [ln["product_id"] for ln in split["lines"]] == ["300", "100"]
    assert split["lines"][1]["fills_shortfall_of"] == "300"
    lines = [OrderLineIn(offer_id=ln["offer_id"], quantity=ln["quantity"])
             for ln in split["lines"]]
    o = gateway.place_order(m, lines, "key-split-1", "split",
                            agent_passport=_passport(gateway, m))
    assert o["status"] == "pending_approval"
    reasons = o["data"]["decision"]["reasons"]
    assert any("tops up 300" in r and "+16.7 %" in r and "signed offer" in r for r in reasons)
    assert o["data"]["lines"][1]["fills_shortfall_of"] == "300"
    # only the dearer top-up alone (without the original's line): the price the gateway
    # stored with the split still applies
    o = gateway.place_order(m, lines[1:], "key-split-2", "top-up only",
                            agent_passport=_passport(gateway, m))
    assert o["status"] == "pending_approval"
    assert any("same split plan" in r for r in o["data"]["decision"]["reasons"])
    # a buyer that tolerates +20 %: placed
    lenient = sign(m.model_copy(update={"max_substitute_price_increase_pct": 20}))
    assert gateway.place_order(lenient, lines, "key-split-3", "split",
                                agent_passport=_passport(gateway, m))["status"] == "placed"


def test_substitute_offer_carries_the_originals_price(gateway, mandate):
    res = gateway.substitutes("300", mandate, requested_date=MONDAY, amount=10, unit="L")
    opt = next(o for o in res["options"] if o["product_id"] == "100")
    assert opt["tier"] == "official" and not opt["needs_kitchen_check"]
    o = gateway.place_order(mandate, [OrderLineIn(offer_id=opt["offer"]["offer_id"],
                                                  quantity=10, substitutes_for="300")],
                            "key-sub-ref-1", "official substitute",
                            agent_passport=_passport(gateway, mandate))
    assert o["status"] == "pending_approval"
    assert any("quote of the original with these substitutes" in r
               for r in o["data"]["decision"]["reasons"])
    # without a stored reference the gateway quotes the original itself (one product)
    q = gateway.quote(["100"], MONDAY)[0]
    before = gateway.live_lookups
    o = gateway.place_order(mandate, [OrderLineIn(offer_id=q["offer_id"], quantity=10,
                                                  substitutes_for="300")],
                            "key-sub-ref-2", "official substitute",
                            agent_passport=_passport(gateway, mandate))
    assert gateway.live_lookups - before == 1
    assert any("live quote of the original" in r for r in o["data"]["decision"]["reasons"])


def test_strict_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("TTA_DATA_DIR", str(tmp_path))
    assert set(STRICTNESS) == {"lenient", "strict"}
    hotel, school, hospital = (PROFILES[k]() for k in ("hotel", "school", "hospital"))
    assert hotel.strictness == "lenient"
    assert school.strictness == hospital.strictness == "strict"
    m = hospital.mandate
    assert m.substitution_policy == "approval" and m.exclude_may_contain
    assert m.auto_order_limit_eur < school.mandate.auto_order_limit_eur
    assert m.max_substitute_price_increase_pct == 0
    assert hotel.mandate.max_substitute_price_increase_pct == 15
    assert "Client type: strict" in hospital.context()
    assert "Client type" not in hotel.context()       # recorded bench replays stay valid
    assert m.valid_until > datetime.now(TZ) + timedelta(days=1)


def _sub_offer(gateway, mandate) -> str:
    res = gateway.substitutes("300", mandate, requested_date=MONDAY, amount=10, unit="L")
    return next(o for o in res["options"] if o["product_id"] == "100")["offer"]["offer_id"]


def test_undeclared_substitute_is_checked_by_provenance(gateway, mandate, sign):
    """An offer found by find_substitutes stays a substitute even without substitutes_for."""
    o = gateway.place_order(mandate, [OrderLineIn(offer_id=_sub_offer(gateway, mandate),
                                                  quantity=10)],
                            "key-prov-1", "no declaration",
                            agent_passport=_passport(gateway, mandate))
    rules = {c["rule"]: c for c in o["data"]["decision"]["checks"]}
    assert o["status"] == "pending_approval"
    assert "not declared" in rules["substitution"]["detail"]
    assert rules["substitution.price"]["outcome"] == "approval"
    assert "quote of the original with these substitutes" in rules["substitution.price"]["detail"]
    assert o["data"]["lines"][0]["substitutes_for"] == "300"
    assert o["data"]["lines"][0]["substitutes_for_inferred"] is True
    # never: the undeclared substitute is blocked like a declared one
    never = sign(mandate.model_copy(update={"substitution_policy": "never"}))
    o = gateway.place_order(never, [OrderLineIn(offer_id=_sub_offer(gateway, mandate),
                                                quantity=10)],
                            "key-prov-2", "no declaration",
                            agent_passport=_passport(gateway, never))
    assert o["status"] == "rejected"
    assert any("substitutions are not allowed" in r for r in o["data"]["decision"]["reasons"])
    # a plainly quoted product ordered without a declaration is a normal order
    q = gateway.quote(["100"], MONDAY)[0]
    o = gateway.place_order(mandate, [OrderLineIn(offer_id=q["offer_id"], quantity=10)],
                            "key-prov-3", "plain", agent_passport=_passport(gateway, mandate))
    assert not [c for c in o["data"]["decision"]["checks"]
                if c["rule"].startswith("substitution")]
    assert o["data"]["lines"][0]["substitutes_for"] is None


def test_declared_original_must_match_provenance(gateway, mandate):
    o = gateway.place_order(mandate, [OrderLineIn(offer_id=_sub_offer(gateway, mandate),
                                                  quantity=10, substitutes_for="200")],
                            "key-prov-4", "wrong original",
                            agent_passport=_passport(gateway, mandate))
    assert o["status"] == "pending_approval"
    assert any("declared original 200 differs from the one this offer was found for (300)"
               in r for r in o["data"]["decision"]["reasons"])
    # the matching declaration raises no provenance check
    o = gateway.place_order(mandate, [OrderLineIn(offer_id=_sub_offer(gateway, mandate),
                                                  quantity=10, substitutes_for="300")],
                            "key-prov-5", "right original",
                            agent_passport=_passport(gateway, mandate))
    assert not [c for c in o["data"]["decision"]["checks"]
                if c["rule"] == "substitution.provenance"]
