"""Open schemas of the trust protocol documents (JSON Schema draft 2020-12).

Every document the gateway signs or accepts has a pydantic model here (the mandate is
`policy.Mandate` itself). `schemas()` turns them into JSON Schema; `tta schemas` writes
them to `trade-agents/schemas/*.schema.json`, the API serves them at `GET /schemas/{name}`,
and a test checks real documents produced by the gateway against them.

Signed documents are JWS compact (RFC 7515), `alg: EdDSA` (RFC 8037); the schema describes
the JWS payload (the claims), and `x-jws-typ` names the JWS `typ` header it travels under.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .offers import DeliveryStatus
from .policy import AutonomyMode, Mandate
from .signing import TYP_CART, TYP_MANDATE, TYP_OFFER, TYP_PASSPORT, TYP_RECEIPT

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "urn:valio-aimo:trade-agents:schema:{name}:v1"
SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"

class _Claims(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Issued(_Claims):
    iss: Literal["valio-aimo"] = Field(description="Issuer: Valio Aimo.")
    iat: str = Field(description="Issued at, ISO 8601 date-time.")


class SignedOfferClaims(_Issued):
    """What Valio commits to for one product: price locked until `valid_until`."""

    offer_id: str = Field(pattern=r"^of_[0-9a-f]+$")
    valid_until: str = Field(description="Price lock end, ISO 8601 date-time.")
    product_id: str
    name: str
    sales_unit: str = Field(description="Unit the quantity and price refer to, e.g. ST, KI.")
    lot_size: float
    currency: Literal["EUR"]
    unit_price: float | None = Field(description="EUR excl. VAT per sales unit.")
    unit_price_with_tax: float | None
    vat_rate: float | None = Field(description="Percent, e.g. 13.5.")
    contract_price: bool = Field(description="A buyer-specific (net) price was applied.")
    comparison_price: float | None = Field(description="EUR per comparison unit excl. VAT.")
    comparison_unit: str | None = Field(description="L or KG.")
    requested_date: str = Field(description="Requested delivery date, ISO 8601 date.")
    delivery_status: DeliveryStatus
    available_amount: float | None = Field(description="In sales units; null = no limit.")
    earliest_date: str | None
    order_cutoff: str | None
    origin_country: str | None
    claims: list[str] = Field(description="GS1 nutritional claims, e.g. FREE_FROM_LACTOSE.")
    labels: list[str]
    contains_allergens: list[str] = Field(description="GS1 allergen codes, e.g. AM (milk).")


class CartConfirmationClaims(_Claims):
    """Signed by the buyer's key when a human confirms or declines a cart (AP2 Cart Mandate)."""

    order_id: str
    buyer_id: str
    cart_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$",
                           description="sha256 over the canonical cart lines and total.")
    decision: Literal["confirm", "decline"]
    confirmed_by: str = Field(description="Approver id, must be listed in the mandate.")
    note: str
    iat: str


class ReceiptLine(_Claims):
    offer_id: str
    product_id: str
    name: str
    quantity: float
    sales_unit: str
    unit_price_eur: float | None
    line_total_eur: float
    substitutes_for: str | None


class FiredRule(_Claims):
    rule: str = Field(description="Guardrail id, e.g. spend.auto_limit.")
    outcome: Literal["approval", "block"]
    detail: str


class AuditHead(_Claims):
    seq: int
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class OrderReceiptClaims(_Issued):
    """Valio's receipt for a placed order: what, why it was allowed, and the audit head."""

    order_id: str
    order_number: str
    buyer_id: str
    agent_id: str
    mandate_id: str
    lines: list[ReceiptLine]
    total_eur: float
    currency: Literal["EUR"]
    vat: Literal["excluded"]
    policy_outcome: Literal["auto", "needs_approval"]
    rules_fired: list[FiredRule]
    rules_checked: int
    autonomy_mode: AutonomyMode | None
    cart_confirmed_by: str | None
    approved_by: list[str]
    placed_at: str
    audit_head: AuditHead = Field(description="Hash-chained audit entry of the placement.")


class AgentPassportClaims(_Issued):
    """Valio vouches how autonomous a given agent build (model + harness) may be."""

    agent_id: str = Field(description="Must equal the mandate's agent_id.")
    model: str
    harness: str
    max_mode: AutonomyMode = Field(description="Cap on the mandate's autonomy_mode.")
    issued_at: str
    exp: int = Field(description="Expiry, RFC 7519 NumericDate (seconds since the epoch).")
    bench_score: float | None = None


DOCUMENTS: dict[str, tuple[type[BaseModel], str, str]] = {
    "buyer-mandate": (Mandate, TYP_MANDATE, "Buyer Mandate (AP2 Intent Mandate): what the "
                      "buyer organisation lets its agent do. Signed by the buyer with a "
                      "detached JWS in `signature` over the canonical JSON of all other fields."),
    "signed-offer": (SignedOfferClaims, TYP_OFFER, "Signed Offer: claims of the JWS in "
                     "`signature` on every quote result and plan line."),
    "cart-confirmation": (CartConfirmationClaims, TYP_CART, "Cart Confirmation (AP2 Cart "
                          "Mandate): claims of the JWS a buyer sends to /v1/carts/{id}/confirm."),
    "order-receipt": (OrderReceiptClaims, TYP_RECEIPT, "Order Receipt: claims of "
                      "`order.data.receipt.jws`."),
    "agent-passport": (AgentPassportClaims, TYP_PASSPORT, "Agent Passport: claims of the JWS "
                       "passed as `agent_passport` to place_order."),
}


def schema(name: str) -> dict[str, Any]:
    if name not in DOCUMENTS:
        raise KeyError(name)
    model, typ, description = DOCUMENTS[name]
    body = model.model_json_schema()
    return {"$schema": SCHEMA_DIALECT, "$id": SCHEMA_ID.format(name=name),
            "x-jws-typ": typ, **body, "description": description}


def schemas() -> dict[str, dict[str, Any]]:
    return {name: schema(name) for name in DOCUMENTS}


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write_schemas(out_dir: Path = SCHEMAS_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, doc in schemas().items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(render(doc))
        paths.append(path)
    return paths
