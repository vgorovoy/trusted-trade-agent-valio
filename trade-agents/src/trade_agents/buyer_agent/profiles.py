"""Demo buyer profiles: who is buying, under which signed mandate, with what preferences."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from ..clock import current_time
from ..config import TZ, get_settings
from ..policy import Mandate
from ..signing import BuyerRegistry, ValioSigner, issue_passport

log = logging.getLogger(__name__)

SELF_ISSUE_ENV = "TTA_DEMO_SELF_ISSUE"


def self_issue_enabled() -> bool:
    return os.getenv(SELF_ISSUE_ENV) == "1"


def sign_mandate(mandate: Mandate, buyers_dir: Path | None = None) -> Mandate:
    """The one place where the buyer organisation signs its mandate.

    Ed25519, detached JWS, with the demo buyer key from the registry in `data/buyers/`
    (created and registered on first use). This stands in for the buyer's own wallet
    (`tta mandate` does the same from the command line); the gateway must read the same
    registry, so a served gateway (TTA_GATEWAY_URL) has to share the data directory.
    """
    reg = BuyerRegistry(buyers_dir or get_settings().buyers_dir)
    return mandate.sign(reg.demo_key(mandate.buyer_id))


def demo_passport(agent_id: str, keys_dir: Path | None = None) -> str | None:
    """Agent Passport for this agent.

    Default: the JWS from `TTA_AGENT_PASSPORT_<AGENT_ID>` (upper case, `-` -> `_`) or
    `TTA_AGENT_PASSPORT`, issued by Valio with `tta passport issue --agent <agent_id>`.
    Only with `TTA_DEMO_SELF_ISSUE=1` does the agent mint one itself with Valio's local key
    (local demo only: an agent must never hold the issuer's key). Without a passport the
    gateway caps the agent at confirm_cart, so every cart waits for the buyer."""
    specific = "TTA_AGENT_PASSPORT_" + agent_id.upper().replace("-", "_").replace(".", "_")
    token = os.getenv(specific) or os.getenv("TTA_AGENT_PASSPORT")
    if token:
        return token.strip()
    if not self_issue_enabled():
        log.info("no Agent Passport for %s (set %s or TTA_AGENT_PASSPORT); the gateway will "
                 "cap it at confirm_cart", agent_id, specific)
        return None
    log.warning("%s=1: %s issues its own Agent Passport with Valio's local key. Demo only; "
                "in production Valio issues it (tta passport issue).", SELF_ISSUE_ENV, agent_id)
    signer = ValioSigner(keys_dir or get_settings().keys_dir)
    return issue_passport(signer, agent_id, model=os.getenv("TTA_LLM_MODEL") or "any",
                          harness="trade_agents.buyer_agent", max_mode="autonomous")


# How strictly a client type is handled (Valio mentors: schools and hospitals need a strict
# mode; a hotel can be lenient). The signed mandate carries the actual rules; this is the
# label the profiles are built from and the agent and UI show.
Strictness = Literal["lenient", "strict"]
STRICTNESS: dict[str, str] = {
    "lenient": "hotel / restaurant: allergen traces allowed unless the mandate says otherwise, "
               "a replacement may cost up to +15 % per kg / litre, higher autonomous limit",
    "strict": "school / hospital: every substitute (official or found by the agent) and every "
              "dearer replacement goes to a human, low autonomous limit, cumulative 24 h spend",
}


def next_weekday(weekday: int, today: date | None = None) -> date:
    """Next given weekday strictly after today (0 = Monday)."""
    today = today or current_time(datetime.now(TZ)).date()
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


@dataclass
class BuyerProfile:
    key: str
    kitchen: str                       # who is buying, in one line for the prompt
    mandate: Mandate
    preferences: list[str] = field(default_factory=list)
    default_delivery: date | None = None
    passport: str | None = None        # Agent Passport (JWS) sent with every order
    optimize: str = "price"            # plan_need objective: price|reliability|co2|balanced
    max_premium_pct: float | None = None  # max extra cost over the cheapest option, %
    strictness: Strictness = "lenient"

    def delivery_default(self, today: date | None = None) -> date:
        return self.default_delivery or next_weekday(0, today)

    def context(self, today: date | None = None) -> str:
        today = today or current_time(datetime.now(TZ)).date()
        m = self.mandate
        lines = [
            f"Kitchen: {self.kitchen}",
            f"Today: {today.isoformat()} ({today.strftime('%A')}), timezone Europe/Helsinki.",
            f"Default delivery date if the request names none: "
            f"{self.delivery_default(today).isoformat()}.",
            f"Mandate {m.mandate_id}: the agent may order on its own up to "
            f"{m.auto_order_limit_eur:g} EUR excl. VAT per order; above that a human approves; "
            f"hard limit {m.hard_limit_eur if m.hard_limit_eur is not None else 'none'} EUR.",
        ]
        if m.required_claims:
            lines.append(f"Required product claims: {', '.join(m.required_claims)}.")
        if m.excluded_allergens:
            lines.append(f"Excluded allergens (hard rule): {', '.join(m.excluded_allergens)}.")
        if m.excluded_allergens:
            lines.append("Allergen traces (may_contain): "
                         + ("excluded by the mandate." if m.exclude_may_contain
                            else "not excluded by the mandate; contains is still forbidden."))
        if m.min_delivery_reliability is not None:
            lines.append(f"Minimum delivery reliability for autonomous ordering: "
                         f"{m.min_delivery_reliability:g}. Below this requires human approval; "
                         "missing reliability is unknown, not proof of meeting the threshold.")
        lines.append(f"Substitution policy: {m.substitution_policy}.")
        if self.strictness == "strict":
            # Lenient profiles keep their prompt as it was (recorded bench replays).
            lines.append("Client type: strict (school / hospital). Every substitute, official "
                         "or found by you, goes to a human; a related-category substitute "
                         "needs the kitchen's check"
                         + (f"; a replacement dearer than the original by more than "
                            f"{m.max_substitute_price_increase_pct:g} % per kg / litre needs "
                            "approval" if m.max_substitute_price_increase_pct is not None
                            else "") + ".")
        lines.append(f"Option ranking: optimize={self.optimize}"
                     + (f", at most {self.max_premium_pct:g} % above the cheapest"
                        if self.max_premium_pct is not None else "") + ".")
        if self.preferences:
            lines.append("Preferences: " + "; ".join(self.preferences) + ".")
        return "\n".join(lines)


def _valid() -> datetime:
    return current_time(datetime.now(TZ)) + timedelta(days=30)


def hotel() -> BuyerProfile:
    return BuyerProfile(
        key="hotel",
        kitchen="Demo Hotel Chain, Helsinki: breakfast buffet and conference catering, "
                "about 400 covers a day. Buyer: head chef.",
        mandate=sign_mandate(Mandate(
            mandate_id="mandate-hotel-demo", buyer_id="hotel-chain-001",
            buyer_name="Demo Hotel Chain", agent_id="hotel-buyer-agent",
            valid_until=_valid(), auto_order_limit_eur=1000, hard_limit_eur=20000,
            required_claims=["FREE_FROM_LACTOSE"], substitution_policy="approval",
            max_substitute_price_increase_pct=15,
            min_delivery_reliability=0.9, approvers=["chef@hotel.example"])),
        passport=demo_passport("hotel-buyer-agent"),
        optimize="balanced", max_premium_pct=5, strictness="lenient",
        preferences=["lactose-free dairy for the buffet",
                     "plain products over flavoured, coffee or portion packs",
                     "Finnish origin when the price difference is small"],
    )


def school() -> BuyerProfile:
    return BuyerProfile(
        key="school",
        kitchen="Demo School Kitchen, Espoo: lunch for about 300 pupils, several with "
                "milk allergy; the whole menu is cooked milk-free. Buyer: kitchen manager.",
        mandate=sign_mandate(Mandate(
            mandate_id="mandate-school-demo", buyer_id="school-espoo-001",
            buyer_name="Demo School Kitchen", agent_id="school-buyer-agent",
            valid_until=_valid(), auto_order_limit_eur=500, hard_limit_eur=5000,
            excluded_allergens=["milk"], substitution_policy="approval",
            max_substitute_price_increase_pct=5,
            min_delivery_reliability=0.9, approvers=["manager@school.example"])),
        passport=demo_passport("school-buyer-agent"),
        strictness="strict",
        preferences=["milk-free alternatives (oat, soy) where a recipe calls for dairy",
                     "lowest price per portion"],
    )


def hospital() -> BuyerProfile:
    return BuyerProfile(
        key="hospital",
        kitchen="Demo Hospital Kitchen, Espoo: patient meals, about 600 a day, including "
                "gluten-free diets cooked in the same kitchen. Buyer: diet kitchen manager.",
        mandate=sign_mandate(Mandate(
            mandate_id="mandate-hospital-demo", buyer_id="hospital-espoo-001",
            buyer_name="Demo Hospital Kitchen", agent_id="hospital-buyer-agent",
            valid_until=_valid(), auto_order_limit_eur=300, hard_limit_eur=3000,
            excluded_allergens=["gluten"], exclude_may_contain=True,
            substitution_policy="approval", max_substitute_price_increase_pct=0,
            min_delivery_reliability=0.95, approvers=["dietkitchen@hospital.example"])),
        passport=demo_passport("hospital-buyer-agent"),
        strictness="strict",
        preferences=["exactly the product the dietitian specified; no silent swaps",
                     "no 'may contain' traces of excluded allergens",
                     "lowest price per portion within the diet rules"],
    )


def restaurant() -> BuyerProfile:
    """General restaurant demo: explicit buyer rules, no inferred dietary restriction."""
    return BuyerProfile(
        key="restaurant", kitchen="Demo Restaurant / Cafe: sous-chef prepares weekly orders and top-ups.",
        mandate=sign_mandate(Mandate(
            mandate_id="mandate-restaurant-demo", buyer_id="restaurant-demo-001",
            buyer_name="Demo Restaurant / Cafe", agent_id="restaurant-buyer-agent",
            valid_until=_valid(), auto_order_limit_eur=500, hard_limit_eur=5000,
            substitution_policy="approval", max_substitute_price_increase_pct=10,
            approvers=["chef@restaurant.example"])),
        passport=demo_passport("restaurant-buyer-agent"),
        preferences=["use supplied recipe cards and usable stock; do not invent demand",
                     "show pack-size surplus and ask before changing the requested basket"],
    )


PROFILES = {"restaurant": restaurant, "hotel": hotel, "school": school, "hospital": hospital}


def get_profile(key: str) -> BuyerProfile:
    try:
        return PROFILES[key]()
    except KeyError:
        raise ValueError(f"unknown buyer profile {key!r}; known: {', '.join(PROFILES)}") from None
