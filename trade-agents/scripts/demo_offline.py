"""Reproducible offline protocol demo; scripted orchestration, no LLM or live orders.

Run: uv run python scripts/demo_offline.py [--json]
All products, prices and API answers come from the synthetic benchmark dairy world.
The world uses temporary SQLite state and temporary signing keys, removed on exit.
"""
from __future__ import annotations

import argparse
import json
import os
from contextlib import contextmanager
from typing import Any

from trade_agents.bench.scenario import Scenario, load_world
from trade_agents.bench.world import World
from trade_agents.config import Settings
from trade_agents.service import OrderLineIn
from trade_agents.signing import verify_offer, verify_receipt


def _require(condition: bool, explanation: str) -> None:
    if not condition:
        raise RuntimeError(f"Offline demo verification failed: {explanation}")


@contextmanager
def _offline_review():
    """Disable optional provider hooks even if the developer's environment enables them."""
    overrides = {"TTA_VALIO_REVIEW": "off", "TTA_DEMO_SELF_ISSUE": "0"}
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _hotel() -> dict[str, Any]:
    scenario = Scenario(
        id="offline-demo-hotel-approval", category="demo", title="Hotel conference breakfast",
        buyer="hotel", request="400 litres of lactose-free milk for Monday's breakfast",
        world=load_world("dairy"), expect={},
        mandate={"auto_order_limit_eur": 500, "autonomy_mode": "confirm_cart"},
    )
    with World(scenario, settings=Settings(valio_api_key="offline-demo")) as world:
        gw = world.gateway
        profile = world.profile(model="scripted-offline-demo")
        mandate = profile.mandate
        valid_mandate = mandate.verify(gw.buyers.jwks(mandate.buyer_id))
        _require(valid_mandate, "buyer mandate signature")
        offer = gw.quote(["1001"], world.spec.delivery, amount=400, unit="L",
                         agent_id=mandate.agent_id)[0]
        offer_claims = verify_offer(offer, gw.jwks())
        _require(offer_claims["product_id"] == "1001", "signed offer product")
        _require(offer["quantity_needed"] == 400, "400 litres in one-litre sales units")
        order = gw.place_order(
            mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=400)],
            "offline-demo-hotel-approval", "Conference breakfast: scripted offline demonstration",
            agent_passport=profile.passport,
        )
        transitions = [order["status"]]
        _require(order["status"] == "awaiting_cart_confirmation", "cart must await human confirmation")
        _require(order["order_number"] is None, "nothing ordered before confirmation")
        _require(order["total_eur"] == 540, "400 L × 1.35 EUR = 540 EUR excl. VAT")
        _require(order["data"]["cart"]["after_confirmation"] == "pending_approval",
                 "confirming the cart must not bypass the spending limit")
        approver = "chef@hotel.example"
        confirmation = gw.demo_cart_confirmation(order["order_id"], approver,
                                                 note="Confirmed breakfast quantities")
        order = gw.confirm_cart(order["order_id"], confirmation)
        transitions.append(order["status"])
        _require(order["status"] == "pending_approval", "540 EUR exceeds the 500 EUR autonomous limit")
        _require(order["order_number"] is None, "nothing ordered before spend approval")
        order = gw.approve(order["order_id"], approver, "Approved the conference breakfast exception")
        transitions.append(order["status"])
        _require(order["status"] == "placed" and bool(order["order_number"]), "approved order placed")
        receipt = verify_receipt(order["data"]["receipt"], gw.jwks())
        _require(receipt["order_id"] == order["order_id"], "signed receipt matches the order")
        _require(receipt["total_eur"] == 540, "signed receipt amount")
        _require(receipt["cart_confirmed_by"] == approver, "receipt identifies cart confirmer")
        _require(receipt["approved_by"] == [approver], "receipt identifies spend approver")
        audit = gw.audit(order["order_id"])
        events = [entry["event"] for entry in audit["entries"]]
        _require(audit["chain_valid"], "SHA-256 audit chain")
        _require(events == ["order.received", "policy.needs_approval", "cart.awaiting_confirmation",
                            "cart.confirmed", "approval.required", "approval.granted",
                            "order.revalidated", "order.placed"],
                 "complete cart-confirmation and approval audit sequence")
        return {
            "clock": world.spec.now.isoformat(), "product_id": "1001", "quantity_litres": 400,
            "total_eur_excl_vat": order["total_eur"], "autonomous_limit_eur": 500,
            "transitions": transitions, "order_id": order["order_id"], "order_number": order["order_number"],
            "buyer_mandate_verified": valid_mandate, "signed_offer_verified": True,
            "signed_cart_confirmation_accepted": True, "signed_receipt_verified": True,
            "audit_chain_valid": audit["chain_valid"], "audit_head": receipt["audit_head"],
            "audit_events": events, "mock_api_calls": len(world.api_log.calls),
        }


def _school() -> list[dict[str, Any]]:
    results = []
    for kind, product_id, patch in (
        ("contains", "1001", {}),
        ("may_contain", "1401", {"products": {"1401": {"may_contain": ["AM"]}}}),
    ):
        scenario = Scenario(
            id=f"offline-demo-school-{kind}", category="demo", title="Milk-free school safety",
            buyer="school", request="10 litres for a milk-free school kitchen",
            world=load_world("dairy", patch), expect={},
            mandate={"exclude_may_contain": True, "autonomy_mode": "autonomous"},
        )
        with World(scenario, settings=Settings(valio_api_key="offline-demo")) as world:
            gw = world.gateway
            profile = world.profile(model="scripted-offline-demo")
            offer = gw.quote([product_id], world.spec.delivery)[0]
            verify_offer(offer, gw.jwks())
            order = gw.place_order(
                profile.mandate, [OrderLineIn(offer_id=offer["offer_id"], quantity=10)],
                f"offline-demo-school-{kind}", "Demonstrate deterministic milk-allergen rejection",
                agent_passport=profile.passport,
            )
            _require(order["status"] == "rejected" and order["order_number"] is None,
                     f"school rejects milk {kind}")
            checks = order["data"]["decision"]["checks"]
            _require(any(c["rule"] == f"allergen.{kind}" and c["outcome"] == "block" for c in checks),
                     f"school rejection is caused by allergens ({kind})")
            audit = gw.audit(order["order_id"])
            _require(audit["chain_valid"], "school audit chain")
            results.append({"allergen_source": kind, "product_id": product_id, "status": order["status"],
                            "reasons": order["data"]["decision"]["reasons"],
                            "audit_chain_valid": audit["chain_valid"]})
    return results


def run_demo() -> dict[str, Any]:
    with _offline_review():
        return {"mode": "offline / scripted orchestration", "llm_used": False,
                "live_valio_api_used": False, "order_backend": "MockOrderBackend (temporary SQLite)",
                "hotel": _hotel(), "school": _school()}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print structured verification evidence")
    args = parser.parse_args(argv)
    result = run_demo()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    hotel = result["hotel"]
    print("OFFLINE DEMO — scripted orchestration; no LLM, no live API, no production orders")
    print(f"Frozen clock: {hotel['clock']}; synthetic dairy catalogue, temporary SQLite and keys")
    print("VERIFIED  Buyer-signed mandate → Ed25519 Signed Offer")
    print("HOTEL     400 L × 1.35 EUR = 540 EUR excl. VAT; autonomous limit 500 EUR")
    print("HUMAN     " + " → ".join(hotel["transitions"]))
    print("VERIFIED  Buyer-signed cart confirmation → human spend approval → Signed Receipt")
    print(f"AUDIT     SHA-256 chain valid; head {hotel['audit_head']['hash']}")
    for school in result["school"]:
        print(f"SCHOOL    Milk {school['allergen_source']}: REJECTED by deterministic allergen policy")
    print("All protocol and safety checks passed. Temporary demo data removed.")


if __name__ == "__main__":
    main()
