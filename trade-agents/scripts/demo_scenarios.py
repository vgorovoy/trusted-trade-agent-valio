"""Run the demo scenarios against the live Valio Aimo mock API, in-process.

    uv run python scripts/demo_scenarios.py [--date 2026-11-16]

The "agent judgement" step (dropping coffee milk / cocoa drinks from candidates) is a
keyword stand-in here; in the real demo the LLM buyer agent makes that call.
"""

from __future__ import annotations

import argparse
import os
import re
import uuid
from datetime import date, datetime, timedelta

from trade_agents.config import TZ
from trade_agents.policy import Mandate
from trade_agents.service import Gateway, OrderLineIn

NOT_PLAIN_MILK = re.compile(r"kahvi|caf|kaakao|annos|rahka|jauhe|jäätelö|suklaa", re.I)


def hotel_mandate(gw: Gateway) -> Mandate:
    return Mandate(
        mandate_id="mandate-hotel-demo", buyer_id="hotel-chain-001",
        buyer_name="Demo Hotel Chain", agent_id="hotel-buyer-agent",
        valid_until=datetime.now(TZ) + timedelta(days=30),
        auto_order_limit_eur=1000, hard_limit_eur=20000,
        required_claims=["FREE_FROM_LACTOSE"], substitution_policy="approval",
        min_delivery_reliability=0.9, approvers=["chef@hotel.example"],
    ).sign(gw.buyers.demo_key("hotel-chain-001"))


def show(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def order_best(gw: Gateway, mandate: Mandate, amount: float, day: date) -> dict | None:
    found = gw.search("maito", require_claims=["FREE_FROM_LACTOSE"], limit=40)
    picked = [p["product_id"] for p in found if not NOT_PLAIN_MILK.search(p["name"])]
    print(f"search: {len(found)} lactose-free hits, agent keeps {len(picked)} plain milks")
    plan = gw.plan("maito", amount, "L", day, product_ids=picked, agent_id=mandate.agent_id)
    print("plan:", plan["explanation"])
    for r in plan["ruled_out"][:4]:
        print(f"  ruled out {r['name']}: {r['reason']}")
    if not plan["options"]:
        return None
    best = plan["options"][0]
    lines = [OrderLineIn(offer_id=ln["offer_id"], quantity=ln["quantity"]) for ln in best["lines"]]
    order = gw.place_order(mandate, lines, uuid.uuid4().hex,
                           rationale=f"cheapest plain lactose-free milk covering {amount:g} L",
                           agent_passport=gw.issue_passport(
                               mandate.agent_id, "rule-based demo", "scripts/demo_scenarios.py",
                               "autonomous"))
    print(f"order {order['order_id']}: {order['status'].upper()}, {order['total_eur']:.2f} EUR")
    for reason in order["data"]["decision"]["reasons"]:
        print("  -", reason)
    return order


def passport(gw: Gateway, mandate: Mandate) -> str:
    return gw.issue_passport(mandate.agent_id, "rule-based demo", "scripts/demo_scenarios.py",
                             "autonomous")


def print_order(label: str, order: dict) -> None:
    print(f"{label}: {order['status'].upper()}, {order['total_eur']:.2f} EUR")
    for reason in order["data"]["decision"]["reasons"]:
        print("  -", reason)


# The kitchen's usual product for the strict-mode demo (found with `tta search maito
# --claim FREE_FROM_LACTOSE`): Valio kevytmaito 1 l laktoositon UHT.
USUAL_MILK = "464201"


def strict_mode(gw: Gateway, hotel: Mandate, day: date) -> None:
    """Valio mentors: Valio lists substitutes for only ~200 of 17.5k products, so the agent
    finds its own; a strict kitchen (school, hospital) must not get a dearer product without
    a human. Live price + availability for at most 5 products (the original + 4 options)."""
    show("4a. Agent-found substitute (not on Valio's list) -> human, even under official_auto")
    lenient = hotel.model_copy(update={"substitution_policy": "official_auto",
                                       "max_substitute_price_increase_pct": 15}).sign(
        gw.buyers.demo_key(hotel.buyer_id))
    res = gw.substitutes(USUAL_MILK, lenient, requested_date=day, amount=100, unit="L",
                         limit=4, agent_id=lenient.agent_id)
    print(f"{res['name']}: {res['options_found']} safe substitute(s) in the catalogue, "
          f"{len(res['options'])} priced live")
    for o in res["options"]:
        print(f"  {o['product_id']} {o['name'][:44]:<44} tier={o['tier']:<8} "
              f"orderable={o.get('orderable')}  {o['differences'][0]}")
    found = [o for o in res["options"] if o.get("orderable") and not o["official"]]
    if not found:
        print("no orderable agent-found substitute today; skipping")
        return
    pick = found[0]
    o = gw.place_order(lenient, [OrderLineIn(offer_id=pick["offer"]["offer_id"],
                                             quantity=pick["offer"]["quantity_needed"],
                                             substitutes_for=USUAL_MILK)],
                       uuid.uuid4().hex, f"{USUAL_MILK} replaced by {pick['product_id']}, "
                       f"found by the agent ({pick['tier']} tier)",
                       agent_passport=passport(gw, lenient))
    print_order(f"hotel orders 100 L of {pick['name']} instead", o)

    show("4b. Meat-style split for a hospital: half the usual product, half a dearer one")
    hospital = Mandate(
        mandate_id="mandate-hospital-demo", buyer_id="hospital-espoo-001",
        buyer_name="Demo Hospital Kitchen", agent_id="hospital-buyer-agent",
        valid_until=datetime.now(TZ) + timedelta(days=30), auto_order_limit_eur=300,
        hard_limit_eur=3000, excluded_allergens=["gluten"], exclude_may_contain=True,
        substitution_policy="approval", max_substitute_price_increase_pct=0,
        min_delivery_reliability=0.95, approvers=["dietkitchen@hospital.example"],
    ).sign(gw.buyers.demo_key("hospital-espoo-001"))
    usual = gw.quote([USUAL_MILK], day, 50, "L", agent_id=hospital.agent_id)[0]
    dearer = [o for o in found if (o["offer"].get("price_per_unit") or 0)
              > (usual.get("comparison_price") or 1e9)]
    if not dearer:
        print("no dearer orderable substitute today; skipping")
        return
    other = gw.quote([dearer[0]["product_id"]], day, 50, "L", agent_id=hospital.agent_id)[0]
    o = gw.place_order(hospital, [
        OrderLineIn(offer_id=usual["offer_id"], quantity=usual["quantity_needed"]),
        OrderLineIn(offer_id=other["offer_id"], quantity=other["quantity_needed"],
                    substitutes_for=USUAL_MILK)],
        uuid.uuid4().hex, "50 L of the usual milk, the other 50 L from a dearer product",
        agent_passport=passport(gw, hospital))
    print_order(f"hospital: 50 L {usual['name']} + 50 L {other['name']}", o)


def main() -> None:
    os.environ.setdefault("TTA_VALIO_REVIEW", "sync")  # review shows up in the audit below
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-11-16")
    day = date.fromisoformat(ap.parse_args().date)
    gw = Gateway()
    mandate = hotel_mandate(gw)

    show("1. Within mandate: 200 L lactose-free milk -> agent orders on its own")
    order_best(gw, mandate, 200, day)

    show("2. Over the autonomous limit: 1000 L -> human approval")
    o = order_best(gw, mandate, 1000, day)
    if o and o["status"] == "pending_approval":
        done = gw.approve(o["order_id"], "chef@hotel.example", "conference weekend, approved")
        print(f"chef approved -> {done['status'].upper()} as {done['order_number']}")

    show("3. Exceptions: ordering more than is available, and an allergen-restricted kitchen")
    q = gw.quote(["463380"], day)[0]  # Arla Cafe maito: limited stock
    o = gw.place_order(mandate, [OrderLineIn(offer_id=q["offer_id"], quantity=5000)],
                       uuid.uuid4().hex, "test: too much")
    print(f"5000 x {q['name']}: {o['status'].upper()}")
    for reason in o["data"]["decision"]["reasons"]:
        print("  -", reason)
    school = mandate.model_copy(update={
        "mandate_id": "mandate-school-demo", "buyer_name": "Demo School Kitchen",
        "excluded_allergens": ["milk"], "required_claims": []}).sign(
        gw.buyers.demo_key("hotel-chain-001"))
    q = gw.quote(["463335"], day)[0]
    o = gw.place_order(school, [OrderLineIn(offer_id=q["offer_id"], quantity=10)],
                       uuid.uuid4().hex, "test: allergen")
    print(f"school orders {q['name']}: {o['status'].upper()}")
    for reason in o["data"]["decision"]["reasons"]:
        print("  -", reason)

    strict_mode(gw, mandate, day)

    show("Audit trail (last entries, hash chain verified)")
    trail = gw.audit()
    print("chain valid:", trail["chain_valid"])
    for e in trail["entries"][-8:]:
        print(f"  {e['ts'][11:19]} {e['actor']:<20} {e['event']:<22} {e['summary'][:70]}")


if __name__ == "__main__":
    main()
