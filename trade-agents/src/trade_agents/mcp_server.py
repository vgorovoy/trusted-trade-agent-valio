"""MCP interface of the gateway: the same use cases as the REST API, as agent tools.

Approval and cart-confirmation tools are deliberately absent: a purchasing agent can
propose and explain, but only a human (through the REST API / UI) can confirm a cart,
approve or reject.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer

from .policy import Mandate
from .service import Gateway, OrderLineIn

mcp = MCPServer(
    name="valio-aimo-gateway",
    instructions=(
        "You are buying for a professional kitchen from Valio Aimo. Typical flow: "
        "plan_need (or search_products + get_offers) -> choose an option -> place_order with "
        "the buyer's signed mandate and a short rationale. place_order returns status "
        "'placed', 'pending_approval' (a human must sign off), 'awaiting_cart_confirmation' "
        "(the buyer must confirm the cart first), 'proposal' (you may only recommend; nothing "
        "was ordered) or 'rejected' with reasons. Never "
        "try to work around a rejection, explain it to the buyer instead. Quantities are in "
        "the product's sales unit; plan_need converts litres/kg for you. plan_need returns "
        "up to three options (good / better / best) with a trade_off each; if the product "
        "is unavailable or not allowed, find_substitutes lists safe alternatives, and a "
        "substitute line carries substitutes_for (the mandate decides if a human must "
        "approve). Ask for live prices only for narrowed candidates (max 40 per call)."
    ),
)


@lru_cache
def gw() -> Gateway:
    return Gateway()


@mcp.tool()
def search_products(query: str, require_claims: list[str] | None = None,
                    exclude_allergens: list[str] | None = None,
                    exclude_may_contain: bool = False, limit: int = 15) -> list[dict[str, Any]]:
    """Search the static catalogue (Finnish/English/Swedish names, brand, ingredients).

    require_claims: e.g. ["FREE_FROM_LACTOSE"], ["FREE_FROM_GLUTEN"], ["VEGAN"].
    exclude_allergens: GS1 codes or names, e.g. ["milk", "AW"].
    """
    return gw().search(query, require_claims=require_claims or [],
                       exclude_allergens=exclude_allergens or [],
                       exclude_may_contain=exclude_may_contain, limit=limit)


@mcp.tool()
def get_product(product_id: str) -> dict[str, Any]:
    """Full detail for one product: ingredients, nutrition, allergens, labels, substitutes."""
    return gw().product(product_id)


@mcp.tool()
def get_offers(product_ids: list[str], requested_date: str, amount: float | None = None,
               unit: str | None = None, agent_id: str = "buyer-agent") -> list[dict[str, Any]]:
    """Live price and availability for specific products, each with a price-locked offer_id.

    requested_date: ISO date of desired delivery. amount + unit (L, KG or sales unit)
    are optional; with them the answer includes quantity_needed in sales units.
    """
    return gw().quote(product_ids, date.fromisoformat(requested_date), amount, unit, agent_id)


@mcp.tool()
def plan_need(query: str, amount: float, unit: str, requested_date: str,
              require_claims: list[str] | None = None,
              exclude_allergens: list[str] | None = None,
              product_ids: list[str] | None = None,
              agent_id: str = "buyer-agent", optimize: str = "price",
              max_premium_pct: float | None = None) -> dict[str, Any]:
    """Find up to three ranked ways to cover a need, e.g. 1000 L of lactose-free milk.

    optimize: price | reliability | co2 | balanced; max_premium_pct caps the extra cost over
    the cheapest (options above it are listed in ruled_out). Each option has rank,
    objectives and a trade_off against the cheapest in absolute values.

    Handles unit conversion, order cut-offs, partial availability (split across products)
    and explains every ruled-out candidate. Lines carry offer_ids for place_order.
    Pass product_ids to plan only over candidates you judged relevant (recommended:
    search_products first and drop near-misses such as coffee milk or cocoa drinks).
    """
    return gw().plan(query, amount, unit, date.fromisoformat(requested_date),
                     require_claims=require_claims or [],
                     exclude_allergens=exclude_allergens or [],
                     product_ids=product_ids, agent_id=agent_id,
                     optimize=optimize, max_premium_pct=max_premium_pct)


@mcp.tool()
def find_substitutes(product_id: str, requested_date: str | None = None,
                     amount: float | None = None, unit: str | None = None,
                     exclude_allergens: list[str] | None = None,
                     exclude_may_contain: bool = False,
                     require_claims: list[str] | None = None, limit: int = 10,
                     agent_id: str = "buyer-agent") -> dict[str, Any]:
    """Allergen-safe substitutes for a product (official Valio substitutes first), each with
    `differences` from the original. With requested_date (ISO) the options also carry a live
    price-locked offer (offer_id, quantity_needed for amount + unit, delivery). Unknown
    allergen names are an error. Order a substitute with substitutes_for=<product_id>.
    """
    return gw().substitutes(
        product_id, {"exclude_allergens": exclude_allergens or [],
                     "exclude_may_contain": exclude_may_contain,
                     "require_claims": require_claims or []},
        requested_date=date.fromisoformat(requested_date) if requested_date else None,
        amount=amount, unit=unit, limit=limit, agent_id=agent_id)


@mcp.tool()
def place_order(mandate: dict[str, Any], lines: list[dict[str, Any]], idempotency_key: str,
                rationale: str, alternatives: list[dict[str, Any]] | None = None,
                agent_passport: str | None = None) -> dict[str, Any]:
    """Commit an order under the buyer's signed mandate.

    lines: [{"offer_id": ..., "quantity": <sales units>, "substitutes_for": <product_id|null>}]
    alternatives: options you considered but did not pick (shown to the buyer with the cart).
    agent_passport: your Valio-signed passport (JWS); it caps how autonomous you may be.
    Returns the order with status placed | pending_approval | awaiting_cart_confirmation |
    proposal | rejected and the reasons. Reusing an idempotency_key returns the original
    order instead of ordering twice.
    """
    return gw().place_order(Mandate.model_validate(mandate),
                            [OrderLineIn.model_validate(x) for x in lines],
                            idempotency_key, rationale, alternatives=alternatives,
                            agent_passport=agent_passport)


@mcp.tool()
def get_order(order_id: str) -> dict[str, Any]:
    """Current state of an order, including approvals and the policy decision."""
    return gw().order(order_id)


@mcp.tool()
def explain_order(order_id: str) -> dict[str, Any]:
    """Human-readable audit trail for one order (hash-chained, verifiable)."""
    return gw().audit(order_id)


@mcp.tool()
def get_valio_keys() -> dict[str, Any]:
    """Valio's public signing keys (JWKS, Ed25519). Every offer carries a `signature` (JWS,
    typ valio.offer.v1) and every placed order a signed `receipt`; verify them with
    trade_agents.signing.verify_offer / verify_receipt or any JOSE library (alg EdDSA)."""
    return gw().jwks()


@mcp.tool()
def add_note(subject: str, note: str, agent_id: str = "buyer-agent",
             order_id: str | None = None) -> dict[str, Any]:
    """Record the agent's own reasoning in the shared audit trail.

    subject: what the note is about (product id, query...). Pass order_id to file the note
    under an order so explain_order(order_id) shows it, e.g. a flagged suspicious text in a
    product description that you ignored."""
    return gw().record_note(agent_id, subject, note, order_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
