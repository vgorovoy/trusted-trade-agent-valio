"""REST interface of the gateway. FastAPI publishes it as OpenAPI 3 at /openapi.json."""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import protocol
from .policy import Mandate
from .service import Gateway, GatewayError, OrderLineIn

app = FastAPI(
    title="Valio Aimo Agent Gateway",
    version="0.1.0",
    description=(
        "Interface through which an external purchasing agent can find products, get "
        "price-locked offers, and place orders within a buyer-signed mandate. Decisions "
        "outside the mandate go to a human. Everything is written to a hash-chained audit log."
    ),
)


@lru_cache
def gateway() -> Gateway:
    return Gateway()


def _err(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


class QuoteIn(BaseModel):
    product_ids: list[str] = Field(
        min_length=1, description="Narrowed candidates; at most TTA_MAX_LIVE_PRODUCTS (40) per "
        "call, Valio asks not to query live prices/availability for many products.")
    requested_date: date
    amount: float | None = Field(default=None, description="Optional need, e.g. 1000")
    unit: str | None = Field(default=None, description="L, KG or the sales unit")
    agent_id: str = "anonymous"


class PlanIn(BaseModel):
    optimize: Literal["price", "reliability", "co2", "balanced"] = "price"
    max_premium_pct: float | None = Field(default=None, ge=0)
    query: str = Field(examples=["laktoositon maito"])
    amount: float = Field(examples=[1000])
    unit: str = Field(examples=["L"])
    requested_date: date
    require_claims: list[str] = Field(default_factory=list, examples=[["FREE_FROM_LACTOSE"]])
    exclude_allergens: list[str] = Field(default_factory=list)
    exclude_may_contain: bool = False
    product_ids: list[str] | None = Field(
        default=None, description="Candidates chosen by the agent; skips the text search."
    )
    agent_id: str = "anonymous"


class OrderIn(BaseModel):
    mandate: Mandate
    lines: list[OrderLineIn] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8)
    rationale: str = Field(default="", description="The agent's own explanation of the choice.")
    alternatives: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Options the agent considered but did not pick; shown to the buyer in the "
        "cart summary (confirm_cart / advise modes).",
    )
    agent_passport: str | None = Field(
        default=None, description="Valio-signed Agent Passport (JWS); caps the autonomy mode."
    )


class ApprovalIn(BaseModel):
    approver: str
    note: str = ""


class CartDecisionIn(BaseModel):
    confirmation: str = Field(
        description="JWS (typ valio.cart_confirmation.v1) signed with the buyer's key over "
        "{order_id, buyer_id, cart_hash, decision: confirm|decline, confirmed_by, note, iat}."
    )


class WalletSignIn(BaseModel):
    order_id: str
    confirmed_by: str
    decision: str = Field(default="confirm", pattern="^(confirm|decline)$")
    note: str = ""


class NoteIn(BaseModel):
    agent_id: str
    subject: str
    note: str
    order_id: str | None = Field(
        default=None, description="File the note under this order (shown by its audit trail)."
    )


@app.get("/v1/products/search", tags=["catalogue"])
def search(
    q: str,
    require_claims: list[str] = Query(default=[]),
    exclude_allergens: list[str] = Query(default=[]),
    exclude_may_contain: bool = False,
    require_labels: list[str] = Query(default=[]),
    limit: int = Query(default=20, le=100),
    gw: Gateway = Depends(gateway),
) -> list[dict[str, Any]]:
    try:
        return gw.search(q, require_claims=require_claims, exclude_allergens=exclude_allergens,
                         exclude_may_contain=exclude_may_contain, require_labels=require_labels,
                         limit=limit)
    except GatewayError as e:
        raise _err(e) from e


@app.get("/v1/products/{product_id}", tags=["catalogue"])
def product(product_id: str, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    try:
        return gw.product(product_id)
    except GatewayError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/v1/products/{product_id}/substitutes", tags=["catalogue"])
def substitutes(
    product_id: str,
    requested_date: date | None = Query(
        default=None, description="With a date, each listed option gets a live price-locked "
        "offer (offer_id) and delivery status; without, static catalogue data only."),
    amount: float | None = Query(default=None, gt=0, description="e.g. 200"),
    unit: str | None = Query(default=None, description="L, KG or the sales unit"),
    exclude_allergens: list[str] = Query(default=[], description="GS1 codes or names, e.g. "
                                         "milk, AM, gluten, nuts; an unknown name is a 400"),
    exclude_may_contain: bool = False,
    require_claims: list[str] = Query(default=[]),
    limit: int = Query(default=10, ge=1, le=39),
    agent_id: str = "anonymous",
    gw: Gateway = Depends(gateway),
) -> dict[str, Any]:
    """Allergen-safe substitutes: Valio's official list first, then the most similar products
    in the category, each with `differences` (price per unit, package, ingredients...).
    Ordering one still goes through the mandate (`substitutes_for` on the order line)."""
    try:
        return gw.substitutes(
            product_id, {"exclude_allergens": exclude_allergens,
                         "exclude_may_contain": exclude_may_contain,
                         "require_claims": require_claims},
            requested_date=requested_date, amount=amount, unit=unit, limit=limit,
            agent_id=agent_id)
    except GatewayError as e:
        raise HTTPException(404 if str(e).startswith("unknown product") else 400, str(e)) from e


@app.post("/v1/offers", tags=["offers"])
def quote(body: QuoteIn, gw: Gateway = Depends(gateway)) -> list[dict[str, Any]]:
    try:
        return gw.quote(body.product_ids, body.requested_date, body.amount, body.unit,
                        agent_id=body.agent_id)
    except GatewayError as e:
        raise _err(e) from e


@app.post("/v1/plans", tags=["offers"])
def plan(body: PlanIn, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    try:
        return gw.plan(body.query, body.amount, body.unit, body.requested_date,
                       require_claims=body.require_claims,
                       exclude_allergens=body.exclude_allergens,
                       exclude_may_contain=body.exclude_may_contain,
                       product_ids=body.product_ids, agent_id=body.agent_id,
                       optimize=body.optimize, max_premium_pct=body.max_premium_pct)
    except GatewayError as e:
        raise _err(e) from e


@app.post("/v1/orders", tags=["orders"])
def place_order(body: OrderIn, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    try:
        return gw.place_order(body.mandate, body.lines, body.idempotency_key, body.rationale,
                              alternatives=body.alternatives,
                              agent_passport=body.agent_passport)
    except GatewayError as e:
        raise _err(e) from e


@app.get("/v1/orders/{order_id}", tags=["orders"])
def get_order(order_id: str, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    try:
        return gw.order(order_id)
    except GatewayError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/v1/carts", tags=["carts"])
def carts(buyer_id: str | None = None, gw: Gateway = Depends(gateway)) -> list[dict]:
    """Carts filled by agents in confirm_cart mode, waiting for the buyer."""
    return gw.pending_carts(buyer_id)


@app.post("/v1/carts/{order_id}/confirm", tags=["carts"])
def confirm_cart(order_id: str, body: CartDecisionIn,
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Human only. Confirms the cart with a buyer-signed confirmation. Afterwards the order
    is `placed`, or `pending_approval` if guardrails still need a sign-off."""
    try:
        return gw.confirm_cart(order_id, body.confirmation)
    except GatewayError as e:
        raise _err(e) from e


@app.post("/v1/carts/{order_id}/decline", tags=["carts"])
def decline_cart(order_id: str, body: CartDecisionIn,
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Human only. Declines the cart (signed, decision=decline); nothing is ordered."""
    try:
        return gw.decline_cart(order_id, body.confirmation)
    except GatewayError as e:
        raise _err(e) from e


@app.get("/.well-known/valio-agent-keys", tags=["trust"])
def valio_keys(gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Valio's public keys (JWKS, Ed25519) for Signed Offers, Order Receipts and Passports."""
    return gw.jwks()


@app.get("/schemas", tags=["trust"])
def schema_index() -> dict[str, Any]:
    """JSON Schemas (draft 2020-12) of the protocol documents: name -> URL and JWS typ."""
    return {name: {"url": f"/schemas/{name}", "$id": doc["$id"], "jws_typ": doc["x-jws-typ"],
                   "title": doc.get("title")}
            for name, doc in protocol.schemas().items()}


@app.get("/schemas/{name}", tags=["trust"])
def schema(name: str) -> JSONResponse:
    """One protocol schema, e.g. /schemas/buyer-mandate (`.schema.json` suffix optional)."""
    try:
        doc = protocol.schema(name.removesuffix(".json").removesuffix(".schema"))
    except KeyError:
        raise HTTPException(404, f"unknown schema {name}; see /schemas") from None
    return JSONResponse(doc, media_type="application/schema+json")


@app.post("/demo/wallet/cart-confirmations", tags=["demo"])
def demo_wallet(body: WalletSignIn, gw: Gateway = Depends(gateway)) -> dict[str, str]:
    """Demo only (TTA_DEMO_WALLET=1): stands in for the buyer's own wallet and signs a cart
    confirmation with the demo buyer key. A real buyer signs on its own device."""
    if os.getenv("TTA_DEMO_WALLET") != "1":
        raise HTTPException(404, "demo wallet is disabled (set TTA_DEMO_WALLET=1)")
    try:
        return {"confirmation": gw.demo_cart_confirmation(body.order_id, body.confirmed_by,
                                                          body.decision, body.note)}
    except GatewayError as e:
        raise _err(e) from e


@app.get("/v1/approvals", tags=["approvals"])
def approvals(buyer_id: str | None = None, gw: Gateway = Depends(gateway)) -> list[dict]:
    return gw.pending_approvals(buyer_id)


@app.post("/v1/approvals/{order_id}/approve", tags=["approvals"])
def approve(order_id: str, body: ApprovalIn, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    try:
        return gw.approve(order_id, body.approver, body.note)
    except GatewayError as e:
        raise _err(e) from e


@app.post("/v1/approvals/{order_id}/reject", tags=["approvals"])
def reject(order_id: str, body: ApprovalIn, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    try:
        return gw.reject(order_id, body.approver, body.note)
    except GatewayError as e:
        raise _err(e) from e


@app.get("/v1/audit", tags=["audit"])
def audit(subject: str | None = None, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return gw.audit(subject)


@app.post("/v1/audit/notes", tags=["audit"])
def note(body: NoteIn, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    try:
        return gw.record_note(body.agent_id, body.subject, body.note, body.order_id)
    except GatewayError as e:
        raise _err(e) from e


# -- Valio's internal view (account managers; not part of the buyer-agent interface) --------


@app.get("/v1/valio/orders", tags=["valio"])
def valio_orders(status: str | None = None,
                 risk: Literal["low", "medium", "high"] | None = None,
                 gw: Gateway = Depends(gateway)) -> list[dict[str, Any]]:
    """Orders with the Valio review agent's advisory review, newest first."""
    from .valio_agent import list_reviews

    return list_reviews(gw, status, risk)


@app.post("/v1/valio/orders/{order_id}/review", tags=["valio"])
def valio_review(order_id: str, gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Re-run the review (deterministic checks + model explanation) for one order."""
    from .valio_agent import review_order

    try:
        return review_order(gw, order_id, trigger="manual")
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
