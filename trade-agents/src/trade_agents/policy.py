"""Buyer mandate and the deterministic guardrail engine.

The LLM agent proposes; this module decides. Every rule yields a `Check` with one of
three outcomes: `ok`, `approval` (a human must sign off) or `block` (cannot be ordered
as proposed). The order outcome is the strictest check.

The mandate is signed by the buyer organisation with its Ed25519 key (detached JWS over
the canonical JSON of every field except `signature`, see `signing.py`), so the gateway
can verify against the buyer key registry that the buyer, not the agent, set the limits.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, Field, field_validator

from .catalog import ALLERGEN_NAMES, AllergenFilter, allergen_code
from .offers import Offer
from .signing import TYP_MANDATE, SignatureError, sign_jws, verify_jws

Outcome = Literal["ok", "approval", "block"]
_RANK = {"ok": 0, "approval": 1, "block": 2}

# Rolling window over which the autonomous spend limit is applied cumulatively.
SPEND_WINDOW_HOURS = 24

AutonomyMode = Literal["advise", "confirm_cart", "autonomous"]
MODE_ORDER: dict[str, int] = {"advise": 0, "confirm_cart": 1, "autonomous": 2}
# Without a valid Agent Passport an agent may at most fill a cart for a human to confirm.
NO_PASSPORT_MAX_MODE: AutonomyMode = "confirm_cart"


def effective_mode(*modes: str | None) -> AutonomyMode:
    """The most restrictive of the given modes (advise < confirm_cart < autonomous)."""
    given = [m for m in modes if m]
    return min(given, key=MODE_ORDER.__getitem__) if given else NO_PASSPORT_MAX_MODE  # type: ignore[return-value]


class Mandate(BaseModel):
    """What a buyer organisation allows its purchasing agent to do on its own."""

    mandate_id: str
    buyer_id: str
    buyer_name: str
    agent_id: str
    valid_until: datetime
    auto_order_limit_eur: float = Field(
        description="Orders up to this total (excl. VAT) may be placed without a human."
    )
    hard_limit_eur: float | None = Field(
        default=None, description="Orders above this are refused even with approval."
    )
    excluded_allergens: list[str] = Field(
        default_factory=list, description="GS1 codes or names, e.g. 'AM' or 'milk'. Hard rule."
    )
    exclude_may_contain: bool = Field(
        default=False, description="Also refuse 'may contain' traces of excluded allergens."
    )
    required_claims: list[str] = Field(
        default_factory=list, description="e.g. FREE_FROM_LACTOSE; missing claim needs approval."
    )
    substitution_policy: Literal["never", "approval", "official_auto"] = Field(
        default="approval",
        description="official_auto: Valio's own substitution list may be used without a human "
        "if every other rule passes.",
    )
    max_substitute_price_increase_pct: float | None = Field(
        default=10, ge=0,
        description="A line that replaces another product (substitute, or a second product "
        "topping up a partial stock) may cost at most this many % more per kg / litre (per "
        "sales unit if neither has a comparison unit) than the original, else a human "
        "approves, whatever substitution_policy says. The original's price comes from Valio "
        "(a signed offer or the gateway's own quote), never from the agent. None: no check.",
    )
    min_delivery_reliability: float | None = None
    allow_late_delivery: bool = False
    approvers: list[str] = Field(default_factory=list)
    autonomy_mode: AutonomyMode = Field(
        default="autonomous",
        description="advise: the agent may only recommend (place_order returns a proposal); "
        "confirm_cart: every cart waits for the buyer's signed confirmation; autonomous: the "
        "agent orders on its own within the guardrails. Capped by the Agent Passport.",
    )
    signature: str | None = None

    @field_validator("excluded_allergens")
    @classmethod
    def _known_allergens(cls, v: list[str]) -> list[str]:
        for a in v:  # an unknown name would silently exclude nothing
            allergen_code(a)
        return v

    def payload(self) -> bytes:
        data = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()

    def sign(self, key: Ed25519PrivateKey) -> Mandate:
        """Sign with the buyer's private key: detached JWS (`header..signature`)."""
        sig = sign_jws(self.payload(), key, TYP_MANDATE, detached=True)
        return self.model_copy(update={"signature": sig})

    def verify(self, jwks: dict[str, Any] | None) -> bool:
        """True if `signature` verifies against the buyer's registered public keys."""
        if not self.signature or not jwks:
            return False
        try:
            verify_jws(self.signature, jwks, typ=TYP_MANDATE, payload=self.payload())
        except SignatureError:
            return False
        return True


@dataclass
class Check:
    rule: str
    outcome: Outcome
    detail: str
    line: int | None = None  # index of the order line, None for order-level checks


@dataclass
class PriceReference:
    """The original product's price per comparison unit, as Valio quoted it."""

    product_id: str
    price: float                     # EUR per `basis` unit, excl. VAT
    basis: str                       # "KG" | "L" | the sales unit
    source: str                      # e.g. "signed offer of_… in this order", "gateway quote"


def unit_basis(offer: Offer) -> tuple[float, str] | None:
    """Price per kg / litre (the comparison unit), else per sales unit; None without a price."""
    if offer.comparison_price and offer.comparison_unit:
        return offer.comparison_price, offer.comparison_unit.upper()
    if offer.unit_price is not None and offer.content and offer.content.per_sales_unit:
        return offer.unit_price / offer.content.per_sales_unit, offer.content.unit.upper()
    if offer.unit_price is not None:
        return offer.unit_price, offer.sales_unit.upper()
    return None


def price_reference(offer: Offer, source: str) -> PriceReference | None:
    b = unit_basis(offer)
    return PriceReference(offer.product_id, round(b[0], 4), b[1], source) if b else None


@dataclass
class LineProposal:
    offer: Offer
    quantity: float                  # in sales units
    offer_expired: bool = False
    substitutes_for: str | None = None
    official_substitute: bool = False
    # Split plans: the gateway marked this line as topping up a partial stock of another
    # product (the plan's first pick); price-checked like a substitute, no substitution rule.
    fills_shortfall_of: str | None = None
    # The original's price (substitutes_for or fills_shortfall_of), from Valio only.
    reference: PriceReference | None = None
    # A substitute from a related category (not the original's): the kitchen must check it.
    needs_kitchen_check: bool = False
    # Provenance: the original the gateway priced this offer against (find_substitutes
    # stores it with the price lock). Without a declaration the line is treated as replacing
    # it (`substitute_inferred`); a different declared original needs a human.
    found_for: str | None = None
    substitute_inferred: bool = False


@dataclass
class Decision:
    outcome: Literal["auto", "needs_approval", "rejected"]
    total_eur: float
    checks: list[Check]

    @property
    def reasons(self) -> list[str]:
        worst = {"auto": "ok", "needs_approval": "approval", "rejected": "block"}[self.outcome]
        return [c.detail for c in self.checks if c.outcome == worst and worst != "ok"]

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "total_eur": self.total_eur,
                "reasons": self.reasons, "checks": [asdict(c) for c in self.checks]}


def _names(codes: set[str]) -> str:
    return ", ".join(sorted(ALLERGEN_NAMES.get(c, c) for c in codes))


def _price_delta(limit_pct: float, ln: LineProposal, replaced: str, label: str,
                 i: int) -> Check:
    """A replacement may not quietly change the kitchen's cost: per kg / litre it may cost at
    most `limit_pct` % more than the product it replaces (Valio mentors: 50 kg of the asked-
    for meat plus 50 kg of a dearer one breaks a school's or restaurant's budget)."""
    how = "tops up" if not ln.substitutes_for else "replaces"
    ref, mine = ln.reference, unit_basis(ln.offer)
    if ref is None or ref.product_id != replaced or mine is None:
        return Check("substitution.price", "approval",
                     f"{label}: {how} {replaced}, but the original's price could not be "
                     "verified with Valio; a human must compare the cost", i)
    price, basis = mine
    if basis != ref.basis:
        return Check("substitution.price", "approval",
                     f"{label}: {how} {replaced}, prices not comparable "
                     f"(EUR/{basis} vs EUR/{ref.basis}); a human must compare the cost", i)
    pct = (price / ref.price - 1) * 100 if ref.price > 0 else math.inf
    detail = (f"{label}: {how} {replaced}, price per {basis} {ref.price:.2f} → {price:.2f} EUR "
              f"({pct:+.1f} %, original's price from {ref.source})")
    if pct > limit_pct + 1e-9:
        return Check("substitution.price", "approval",
                     f"{detail} is above the mandate's +{limit_pct:g} % for a replacement; "
                     "a dearer product changes the kitchen's cost, a human must approve", i)
    return Check("substitution.price", "ok", f"{detail}, within +{limit_pct:g} %", i)


def evaluate(
    mandate: Mandate,
    lines: list[LineProposal],
    *,
    now: datetime,
    signature_ok: bool,
    prior_spend_eur: float = 0.0,
) -> Decision:
    """`prior_spend_eur`: what this mandate already committed or holds in the last
    SPEND_WINDOW_HOURS without a human (placed, not approved, not cart-confirmed)."""
    checks: list[Check] = []
    add = checks.append

    add(Check("mandate.signature", "ok" if signature_ok else "block",
              "mandate signature valid" if signature_ok else "mandate signature invalid or missing"))
    expired = now > mandate.valid_until
    add(Check("mandate.validity", "block" if expired else "ok",
              f"mandate expired at {mandate.valid_until.isoformat()}" if expired
              else "mandate is valid"))
    if not lines:
        add(Check("order.lines", "block", "order has no lines"))

    banned = AllergenFilter.parse(mandate.excluded_allergens)
    total = 0.0
    for i, ln in enumerate(lines):
        o = ln.offer
        label = f"line {i + 1} ({o.name})"
        if ln.offer_expired:
            add(Check("offer.expired", "block", f"{label}: price lock expired, request a new offer", i))
        if o.unit_price is None:
            add(Check("offer.price", "block", f"{label}: no price available", i))
        else:
            total += o.unit_price * ln.quantity

        lot = o.lot_size or 1.0
        steps = ln.quantity / lot
        if ln.quantity <= 0 or abs(steps - round(steps)) > 1e-6:
            add(Check("quantity.lot", "block",
                      f"{label}: quantity {ln.quantity:g} must be a positive multiple of {lot:g}", i))

        d = o.delivery
        if d.status in ("out_of_stock", "sales_blocked", "unknown", "cutoff_passed"):
            add(Check("delivery." + d.status, "block",
                      f"{label}: {d.status.replace('_', ' ')}" + (f" ({d.notes[0]})" if d.notes else ""), i))
        elif d.available_amount is not None and ln.quantity > d.available_amount:
            add(Check("delivery.partial", "block",
                      f"{label}: {ln.quantity:g} {o.sales_unit} requested, only "
                      f"{d.available_amount:g} available", i))
        elif d.status == "late":
            add(Check("delivery.late", "ok" if mandate.allow_late_delivery else "approval",
                      f"{label}: earliest delivery {d.earliest_date} is after requested "
                      f"{o.requested_date}", i))
        else:
            add(Check("delivery", "ok", f"{label}: deliverable on {d.earliest_date}", i))

        hit = banned.hits(o.contains_allergens, o.claims)
        if hit:
            add(Check("allergen.contains", "block", f"{label}: contains {_names(hit)}", i))
        if mandate.exclude_may_contain:
            trace = banned.hits(o.may_contain_allergens, o.claims)
            if trace:
                add(Check("allergen.may_contain", "block", f"{label}: may contain {_names(trace)}", i))

        missing = set(mandate.required_claims) - set(o.claims)
        if missing:
            add(Check("claims.required", "approval",
                      f"{label}: missing required claim(s) {', '.join(sorted(missing))}", i))

        if (mandate.min_delivery_reliability is not None and o.delivery_reliability is not None
                and o.delivery_reliability < mandate.min_delivery_reliability):
            add(Check("supplier.reliability", "approval",
                      f"{label}: delivery reliability {o.delivery_reliability:.2f} below "
                      f"{mandate.min_delivery_reliability:.2f}", i))

        if ln.found_for and ln.substitutes_for and ln.found_for != ln.substitutes_for:
            add(Check("substitution.provenance", "approval",
                      f"{label}: declared original {ln.substitutes_for} differs from the one "
                      f"this offer was found for ({ln.found_for}); a human must check", i))
        if ln.substitutes_for:
            pol = mandate.substitution_policy
            undeclared = (" (not declared by the agent; the offer was found as its substitute)"
                          if ln.substitute_inferred else "")
            if pol == "never":
                add(Check("substitution", "block",
                          f"{label}: substitutions are not allowed{undeclared}", i))
            elif pol == "official_auto" and ln.official_substitute:
                add(Check("substitution", "ok",
                          f"{label}: Valio-listed substitute for {ln.substitutes_for}"
                          f"{undeclared}", i))
            else:
                why = ("not on Valio's substitution list" if pol == "official_auto"
                       else "mandate requires sign-off for substitutions")
                if ln.needs_kitchen_check:
                    why += "; related category, check with the kitchen"
                add(Check("substitution", "approval",
                          f"{label}: replaces {ln.substitutes_for}{undeclared}, {why}", i))
        replaced = ln.substitutes_for or ln.fills_shortfall_of
        if replaced and mandate.max_substitute_price_increase_pct is not None:
            add(_price_delta(mandate.max_substitute_price_increase_pct, ln, replaced, label, i))

    total = round(total, 2)
    if mandate.hard_limit_eur is not None and total > mandate.hard_limit_eur:
        add(Check("spend.hard_limit", "block",
                  f"total {total:.2f} EUR exceeds hard limit {mandate.hard_limit_eur:.2f} EUR"))
    elif total > mandate.auto_order_limit_eur:
        add(Check("spend.auto_limit", "approval",
                  f"total {total:.2f} EUR exceeds autonomous limit "
                  f"{mandate.auto_order_limit_eur:.2f} EUR"))
    else:
        add(Check("spend.auto_limit", "ok",
                  f"total {total:.2f} EUR within autonomous limit "
                  f"{mandate.auto_order_limit_eur:.2f} EUR"))
        # Split-order protection: the autonomous limit holds for the rolling window too.
        running = round(prior_spend_eur + total, 2)
        if prior_spend_eur > 0 and running > mandate.auto_order_limit_eur:
            add(Check("spend.cumulative", "approval",
                      f"{prior_spend_eur:.2f} EUR already ordered autonomously under this "
                      f"mandate in the last {SPEND_WINDOW_HOURS} h; with this order "
                      f"{running:.2f} EUR exceeds "
                      f"autonomous limit {mandate.auto_order_limit_eur:.2f} EUR"))

    worst = max((c.outcome for c in checks), key=_RANK.__getitem__, default="ok")
    outcome = {"ok": "auto", "approval": "needs_approval", "block": "rejected"}[worst]
    return Decision(outcome, total, checks)
