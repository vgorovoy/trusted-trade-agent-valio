"""Gateway use cases shared by the REST API and the MCP server.

Flow: search (static catalogue) -> quote / plan (live price + availability, price lock)
-> place_order (mandate check + guardrails) -> placed | pending_approval | rejected
-> approve / reject by a human. Every step lands in the audit trail.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .catalog import AllergenFilter, Catalog, Product, UnknownAllergen
from .config import Settings, get_settings
from .offers import Content, Delivery, Offer, assess_delivery, build_offer
from .policy import (
    MODE_ORDER,
    NO_PASSPORT_MAX_MODE,
    SPEND_WINDOW_HOURS,
    LineProposal,
    Mandate,
    PriceReference,
    effective_mode,
    evaluate,
    price_reference,
)
from .signing import (
    PASSPORT_DEFAULT_DAYS,
    TYP_CART,
    TYP_OFFER,
    TYP_PASSPORT,
    TYP_RECEIPT,
    BuyerRegistry,
    SignatureError,
    ValioSigner,
    cart_hash,
    decode_unverified,
    issue_passport,
    offer_claims,
    passport_problem,
    sign_cart_confirmation,
    verify_jws,
)
from .store import StateStore, now, open_store
from .valio_client import ValioClient

BLOCKING = {"out_of_stock", "sales_blocked", "unknown", "cutoff_passed"}
log = logging.getLogger(__name__)


class OrderLineIn(BaseModel):
    offer_id: str
    quantity: float = Field(description="Quantity in the product's sales unit.")
    substitutes_for: str | None = Field(
        default=None, description="Product id this line replaces, if it is a substitution."
    )
    note: str | None = None


class OrderBackend(Protocol):
    """Where a committed order goes. The mock API has no order endpoint yet."""

    def submit(self, order: dict[str, Any]) -> str: ...


class MockOrderBackend:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def submit(self, order: dict[str, Any]) -> str:
        return self.store.next_order_number()


class GatewayError(ValueError):
    pass


def allergen_filter(names: Iterable[str]) -> AllergenFilter:
    """The allergen predicate for codes or names; an unknown name is an error, not a no-op."""
    try:
        return AllergenFilter.parse(names)
    except UnknownAllergen as e:
        raise GatewayError(str(e)) from None


# Gateway annotations stored next to a price-locked offer (not part of the Offer itself):
# the original's price when the offer is a priced substitute, and the split-plan primary
# when the offer tops up a partial stock. Both come from Valio's own quotes.
OFFER_ANNOTATIONS = ("price_reference", "fills_shortfall_of")
# Substitutes: below this many safe options in the category, also look in the category
# family (tier "family", needs_kitchen_check).
MIN_SAFE_SUBSTITUTES = 3


def offer_from_dict(d: dict[str, Any]) -> Offer:
    d = {k: v for k, v in d.items() if k not in OFFER_ANNOTATIONS}
    d["delivery"] = Delivery(**d["delivery"])
    d["content"] = Content(**d["content"]) if d.get("content") else None
    return Offer(**d)


class Gateway:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        catalog: Catalog | None = None,
        client: ValioClient | None = None,
        store: StateStore | None = None,
        backend: OrderBackend | None = None,
        signer: ValioSigner | None = None,
        buyers: BuyerRegistry | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.catalog = catalog or Catalog(self.settings.catalog_db)
        self.client = client or ValioClient(self.settings)
        self.store = store or open_store(self.settings.state_db)
        self.backend = backend or MockOrderBackend(self.store)
        self.signer = signer or ValioSigner(self.settings.keys_dir)
        self.buyers = buyers or BuyerRegistry(self.settings.buyers_dir)
        # Product lookups sent to the live price + availability API (before its cache).
        self.live_lookups = 0

    # -- discovery -----------------------------------------------------------

    def search(self, query: str, *, require_claims: Iterable[str] = (),
               exclude_allergens: Iterable[str] = (), exclude_may_contain: bool = False,
               require_labels: Iterable[str] = (), limit: int = 20) -> list[dict[str, Any]]:
        exclude_allergens = list(exclude_allergens)
        allergen_filter(exclude_allergens)
        found = self.catalog.search(
            query, require_claims=require_claims, exclude_allergens=exclude_allergens,
            exclude_may_contain=exclude_may_contain, require_labels=require_labels, limit=limit,
        )
        return [p.summary() for p in found]

    def product(self, product_id: str) -> dict[str, Any]:
        p = self.catalog.get(product_id)
        if not p:
            raise GatewayError(f"unknown product {product_id}")
        raw = self.catalog.raw(product_id) or {}
        sd = raw.get("synkkaData", {})
        ingredients = [
            next((n["value"] for n in i.get("names", []) if n.get("language") == "en"), None)
            for i in sd.get("ingredients", [])
        ]
        return {**p.summary(), "vendor": p.vendor, "ingredients": [i for i in ingredients if i],
                "nutrition_per_100": {n["id"]: f'{n["value"]} {n["unit"]}'
                                      for n in sd.get("nutritionalContent", [])}}

    def substitutes(self, product_id: str, constraints: Mandate | dict | None = None, *,
                    requested_date: date | None = None, amount: float | None = None,
                    unit: str | None = None, limit: int = 10,
                    agent_id: str = "anonymous") -> dict:
        """Allergen-safe substitutes for a product: official ones (Valio's list) first, then
        the most similar in its category, each with the differences spelled out.

        constraints: a Mandate or a dict with excluded_allergens / exclude_allergens,
        exclude_may_contain, required_claims / require_claims, substitution_policy.
        With requested_date the top `limit` options also get a live, price-locked offer
        (with quantity_needed for amount + unit). Discovery only: recipe suitability still
        needs the kitchen's confirmation, and ordering a substitute goes through the mandate.
        """
        original = self.catalog.get(product_id)
        if original is None:
            raise GatewayError(f"unknown product {product_id}")
        c = constraints.model_dump() if isinstance(constraints, Mandate) else constraints or {}
        banned = allergen_filter(c.get("excluded_allergens") or c.get("exclude_allergens") or [])
        if c.get("substitution_policy") == "never":
            return {"product_id": product_id, "name": original.name, "options": [],
                    "options_found": 0, "ruled_out": [],
                    "reason": "the mandate forbids substitutions (substitution_policy=never)"}
        required = set(c.get("required_claims") or c.get("require_claims") or [])
        limit = max(1, min(int(limit), self.settings.max_live_products - 1))
        ids = list(original.substitutes)
        ids += [r[0] for r in self.catalog.con.execute(
            "SELECT id FROM products WHERE category = ? ORDER BY id", (original.category,))]
        # With fewer than MIN_SAFE_SUBSTITUTES safe options in the category (e.g. milk is
        # excluded and the category is milk), look one level up: same category family (e.g.
        # 104xx dairy and plant drinks). Such options are tier "family", marked "related
        # category" and `needs_kitchen_check`: the kitchen confirms the recipe still works.
        family = [r[0] for r in self.catalog.con.execute(
            "SELECT id FROM products WHERE substr(category, 1, 3) = ? AND category != ? "
            "ORDER BY id", (original.category[:3], original.category))]
        options: list[dict] = []
        rejected: list[dict] = []
        source_tokens = self._ingredient_tokens(product_id)
        seen = {product_id}
        for tier in (ids, family):
            if len(options) >= MIN_SAFE_SUBSTITUTES:
                break
            for pid in dict.fromkeys(tier):
                if pid in seen:
                    continue
                seen.add(pid)
                option = self._substitute_option(original, pid, banned, required, c,
                                                 source_tokens, rejected)
                if option:
                    options.append(option)
        options.sort(key=lambda o: (TIERS.index(o["tier"]), -o["similarity"], o["product_id"]))
        found = len(options)
        options = options[:limit]
        if requested_date is not None and options:
            options = self._price_substitutes(original, options, requested_date, amount, unit)
        self.store.audit(agent_id, "substitutes.listed", product_id,
                         f"{found} safe substitute(s) for {product_id}, "
                         f"{len(rejected)} ruled out",
                         {"options": [o["product_id"] for o in options],
                          "excluded_allergens": sorted(banned.codes)})
        return {"product_id": product_id, "name": original.name, "options": options,
                "options_found": found, "ruled_out": rejected[:RULED_OUT_SHOWN],
                "ruled_out_count": len(rejected),
                "reason": None if options else
                "no safe substitute with the required claims in the catalogue"}

    def _substitute_option(self, original: Product, pid: str, banned: AllergenFilter,
                           required: set[str], c: dict, source_tokens: set[str],
                           rejected: list[dict]) -> dict | None:
        candidate = self.catalog.get(pid)
        if candidate is None:
            return None
        official = pid in original.substitutes
        have = _effective_claims(candidate)
        lost = (set(original.claims) & DIETARY_CLAIMS) - have
        reason = None
        allergen = banned.reason(candidate.contains, candidate.may_contain, candidate.claims,
                                 bool(c.get("exclude_may_contain")))
        if allergen:
            reason = f"excluded allergen: {allergen}"
        elif not required <= set(candidate.claims):
            reason = "missing a required claim"
        elif not official and lost:
            reason = f"does not keep the original's dietary claims ({', '.join(sorted(lost))})"
        if reason:
            rejected.append({"product_id": pid, "reason": reason})
            return None
        tokens = self._ingredient_tokens(pid)
        overlap = (len(tokens & source_tokens) / len(tokens | source_tokens)
                   if tokens | source_tokens else 0)
        same_unit = (candidate.sales_unit, candidate.name_size_unit) == (
            original.sales_unit, original.name_size_unit)
        source_size = (original.name_size * original.base_per_sales
                       if original.name_size else None)
        target_size = (candidate.name_size * candidate.base_per_sales
                       if candidate.name_size else None)
        size_gap = (abs(target_size - source_size) / source_size
                    if target_size and source_size
                    and original.name_size_unit == candidate.name_size_unit else None)
        score = ((candidate.category == original.category) + same_unit + overlap
                 + (1 / (1 + size_gap) if size_gap is not None else 0))
        differences = [
            f"category {original.category} → {candidate.category}"
            + ("" if candidate.category == original.category
               else " (related category: nothing safe in the original category; check with "
                    "the kitchen)"),
            f"sales unit {original.sales_unit} → {candidate.sales_unit}",
            f"package {_size(source_size, original.name_size_unit)} → "
            f"{_size(target_size, candidate.name_size_unit)}",
            f"key ingredients overlap {overlap:.0%}: {_words(source_tokens)} → {_words(tokens)}"]
        implied = (set(original.claims) & DIETARY_CLAIMS & have) - set(candidate.claims)
        if implied:
            differences.append(f"{', '.join(sorted(implied))} implied by VEGAN "
                               "(no milk or lactose listed)")
        dropped = set(original.claims) - have
        if dropped:
            differences.append(f"claims not kept: {', '.join(sorted(dropped))}")
        differences.append("recipe suitability and nutrition need the kitchen's check")
        tier = ("official" if official else
                "category" if candidate.category == original.category else "family")
        return {**candidate.summary(), "official": official, "tier": tier,
                "needs_kitchen_check": tier == "family", "similarity": round(score, 4),
                "differences": differences}


    def _price_substitutes(self, original: Product, options: list[dict], requested: date,
                           amount: float | None, unit: str | None) -> list[dict]:
        """Live, price-locked offers for the listed substitutes (the original for comparison)."""
        products = [original] + [p for o in options if (p := self.catalog.get(o["product_id"]))]
        offers = {o.product_id: (o, av) for o, av in self._offers(products, requested)}
        base = offers[original.id][0] if original.id in offers else None
        ref = (price_reference(base, "the gateway's quote of the original with these "
                                     "substitutes") if base else None)
        out = []
        for opt in options:
            offer, avail = offers.get(opt["product_id"], (None, None))
            if offer is None:
                out.append({**opt, "orderable": False})
                continue
            units = offer.units_for(amount, unit.upper()) if amount and unit else None
            if units is not None:
                offer.delivery = assess_delivery(avail, requested, units)
            st = offer.delivery.status
            orderable = offer.unit_price is not None and st not in BLOCKING and st != "late"
            lock = self._lock(offer, {"price_reference": asdict(ref)} if ref else None)
            diffs = list(opt["differences"])
            if base and base.comparison_price and offer.comparison_price:
                pct = (offer.comparison_price / base.comparison_price - 1) * 100
                diffs.insert(0, f"price per unit {base.comparison_price:.2f} → "
                                f"{offer.comparison_price:.2f} EUR ({pct:+.1f} %)")
            total = (round(offer.unit_price * units, 2)
                     if units is not None and offer.unit_price is not None else None)
            out.append({**opt, "differences": diffs, "orderable": orderable, "offer": {
                "offer_id": lock["offer_id"], "valid_until": lock["valid_until"],
                "signature": lock["signature"], "unit_price_eur": offer.unit_price,
                "price_per_unit": offer.comparison_price, "quantity_needed": units,
                "line_total_eur": total, "delivery": st,
                "earliest_date": offer.delivery.earliest_date, "notes": offer.delivery.notes}})
        out.sort(key=lambda o: not o["orderable"])
        return out

    def _ingredient_tokens(self, product_id: str) -> set[str]:
        import re

        row = self.catalog.con.execute("SELECT key_ingredients FROM products WHERE id = ?",
                                       (product_id,)).fetchone()
        return set(re.findall(r"\w+", (row[0] or "").casefold())) if row else set()

    # -- live offers -----------------------------------------------------------

    def _offers(self, products: list[Product], requested: date) -> list[tuple[Offer, dict | None]]:
        ids = list(dict.fromkeys(p.id for p in products))
        cap = self.settings.max_live_products
        if len(ids) > cap:
            raise GatewayError(
                f"{len(ids)} products asked for live price/availability, the cap is {cap}: "
                "narrow candidates with search_products first; Valio asks not to query "
                "prices/availability for many products")
        self.live_lookups += 2 * len(ids)
        log.info("live price+availability for %d product(s), %d lookups by this gateway so far",
                 len(ids), self.live_lookups)
        prices = self.client.prices(ids, requested)
        avail = self.client.availability(ids, requested)
        return [(build_offer(p, prices.get(p.id), avail.get(p.id), requested), avail.get(p.id))
                for p in products]

    def _lock(self, offer: Offer, annotations: dict[str, Any] | None = None) -> dict[str, Any]:
        """Price-lock an offer and sign it: {offer_id, valid_until, signature}.
        `annotations` (OFFER_ANNOTATIONS) are stored with the lock, not signed."""
        expires = now() + timedelta(minutes=self.settings.offer_ttl_minutes)
        offer_id = self.store.save_offer(offer.product_id,
                                         {**offer.to_dict(), **(annotations or {})}, expires)
        valid_until = expires.isoformat(timespec="seconds")
        signature = self.signer.sign(offer_claims(offer, offer_id, valid_until), TYP_OFFER)
        return {"offer_id": offer_id, "valid_until": valid_until, "signature": signature}

    def quote(self, product_ids: list[str], requested_date: date,
              amount: float | None = None, unit: str | None = None,
              agent_id: str = "anonymous") -> list[dict[str, Any]]:
        """Price + availability for specific products, each with a price-locked offer_id."""
        products = list(self.catalog.get_many(product_ids).values())
        if not products:
            raise GatewayError("none of the product ids are in the catalogue")
        out = []
        for offer, avail in self._offers(products, requested_date):
            units = offer.units_for(amount, unit) if amount and unit else None
            if units is not None:
                offer.delivery = assess_delivery(avail, requested_date, units)
            lock = self._lock(offer)
            out.append({"offer_id": lock["offer_id"], "quantity_needed": units,
                        **offer.to_dict(), "valid_until": lock["valid_until"],
                        "signature": lock["signature"]})
        self.store.audit(agent_id, "offer.quoted", ",".join(product_ids),
                         f"quoted {len(out)} product(s) for {requested_date.isoformat()}",
                         {"offers": [o["offer_id"] for o in out]})
        return out

    def plan(self, query: str, amount: float, unit: str, requested_date: date, *,
             require_claims: Iterable[str] = (), exclude_allergens: Iterable[str] = (),
             exclude_may_contain: bool = False, candidates: int = 25,
             product_ids: list[str] | None = None,
             agent_id: str = "anonymous", optimize: str = "price",
             max_premium_pct: float | None = None) -> dict[str, Any]:
        """Find ways to cover a need ('1000 L lactose-free milk by Monday').

        Returns up to three objective winners within the premium cap, each a
        list of price-locked lines, plus every candidate that was ruled out and why.
        `product_ids` lets the agent pass the candidates it judged relevant (e.g. drop
        coffee milk when the kitchen asked for milk); otherwise the search result is used.
        """
        if optimize not in {"price", "reliability", "co2", "balanced"}:
            raise GatewayError("optimize must be price, reliability, co2 or balanced")
        if (not math.isfinite(amount) or amount <= 0 or (max_premium_pct is not None
                and (not math.isfinite(max_premium_pct) or max_premium_pct < 0))):
            raise GatewayError("amount must be positive and premium non-negative")
        require_claims, exclude_allergens = list(require_claims), list(exclude_allergens)
        banned = allergen_filter(exclude_allergens)
        unit = unit.upper()
        if product_ids:
            products = list(self.catalog.get_many(dict.fromkeys(product_ids)).values())
        else:
            products = self.catalog.search(
                query, require_claims=require_claims, exclude_allergens=exclude_allergens,
                exclude_may_contain=exclude_may_contain,
                limit=min(candidates, self.settings.max_live_products),
            )
        singles: list[dict] = []
        full: list[Offer] = []
        partials: list[tuple[Offer, float]] = []
        ruled_out: list[dict] = []
        for offer, avail in (self._offers(products, requested_date) if products else []):
            allergen = banned.reason(offer.contains_allergens, offer.may_contain_allergens,
                                     offer.claims, exclude_may_contain)
            missing = set(require_claims) - set(offer.claims)
            if allergen or missing:
                ruled_out.append(_why(offer, "allergen or required claim constraint: "
                                      + (allergen or "missing " + ", ".join(sorted(missing)))))
                continue
            units = offer.units_for(amount, unit)
            if units is None:
                ruled_out.append(_why(offer, f"cannot express {unit} in {offer.sales_unit}"))
                continue
            if offer.unit_price is None:
                ruled_out.append(_why(offer, "no price"))
                continue
            offer.delivery = assess_delivery(avail, requested_date, units)
            st = offer.delivery.status
            if st in BLOCKING or st == "late":
                ruled_out.append(_why(offer, "; ".join(offer.delivery.notes) or st))
                continue
            if st == "partial":
                partials.append((offer, offer.delivery.available_amount or 0.0))
                continue
            full.append(offer)
            singles.append(self._option([(offer, units)], amount, unit, "single product"))

        options = singles
        if partials:
            pool = partials + [(o, None) for o in full]
            for objective in ("price", "reliability", "co2"):
                combo = self._combine(pool, amount, unit, objective)
                if combo:
                    options.append(combo)
        ruled_out += over_premium(options, max_premium_pct)
        options = rank_options(options, optimize, max_premium_pct)
        result = {
            "need": {"query": query, "amount": amount, "unit": unit,
                     "requested_date": requested_date.isoformat(),
                     "optimize": optimize, "max_premium_pct": max_premium_pct,
                     "require_claims": list(require_claims),
                     "exclude_allergens": list(exclude_allergens)},
            "candidates_considered": len(products),
            "options": options,
            "partial_only": [_why(o, "; ".join(o.delivery.notes)) for o, _ in partials],
            "ruled_out": ruled_out,
            "explanation": _explain(options, len(products), ruled_out, partials),
            "live_api": {"products_queried": len(products),
                         "cap": self.settings.max_live_products},
        }
        self.store.audit(agent_id, "need.planned", query,
                         f"planned {amount:g} {unit} of '{query}' for "
                         f"{requested_date.isoformat()}: {len(options)} option(s)",
                         {"options": [[ln["offer_id"] for ln in o["lines"]] for o in options],
                          "ruled_out": len(ruled_out)})
        return result

    def _option(self, picks: list[tuple[Offer, float]], amount: float, unit: str,
                kind: str, split: bool = False) -> dict[str, Any]:
        """split: picks after the first top up the first one's partial stock; their locks
        carry the first pick's price, so a dearer top-up is price-checked at order time."""
        lines, total, covered = [], 0.0, 0.0
        primary = (price_reference(picks[0][0], "the gateway's quote in the same split plan")
                   if split else None)
        for i, (offer, units) in enumerate(picks):
            line_total = round((offer.unit_price or 0) * units, 2)
            content = (offer.content.per_sales_unit * units
                       if offer.content and offer.content.unit == unit else units)
            fills = i > 0 and primary is not None and primary.product_id != offer.product_id
            lines.append({
                **self._lock(offer, {"fills_shortfall_of": asdict(primary)}
                             if fills and primary else None),
                **({"fills_shortfall_of": primary.product_id} if fills and primary else {}),
                "product_id": offer.product_id,
                "name": offer.name, "quantity": units, "sales_unit": offer.sales_unit,
                "unit_price_eur": offer.unit_price, "line_total_eur": line_total,
                "covers": round(content, 3), "covers_unit": unit,
                "price_per_unit": offer.comparison_price, "contract_price": offer.contract_price,
                "delivery": offer.delivery.status, "delivery_notes": offer.delivery.notes,
                "earliest_date": offer.delivery.earliest_date,
                "order_cutoff": offer.delivery.order_cutoff,
                "delivery_reliability": offer.delivery_reliability,
                "co2_kg_per_kg": offer.co2_kg_per_kg,
                "claims": offer.claims, "labels": offer.labels,
            })
            total += line_total
            covered += content
        reliability = [o.delivery_reliability for o, _ in picks]
        carbon = [o.co2_kg_per_kg for o, _ in picks]
        # Conservative weakest-line reliability. Carbon remains a category intensity,
        # weighted only for KG needs; litres cannot be converted to mass without density.
        rel = min(reliability) if all(x is not None for x in reliability) else None
        co2 = (sum(o.co2_kg_per_kg * ln["covers"] for (o, _), ln in zip(picks, lines)) / covered
               if unit == "KG" and covered and all(x is not None for x in carbon)
               else carbon[0] if len(picks) == 1 else None)
        return {"delivery_reliability": rel, "co2_kg_per_kg": co2,
                "kind": kind, "total_eur": round(total, 2), "covers": round(covered, 3),
                "requested": amount, "unit": unit, "lines": lines}

    def _combine(self, pool: list[tuple[Offer, float | None]], amount: float,
                 unit: str, optimize: str = "price") -> dict[str, Any] | None:
        """Greedy split across products, cheapest per unit first (None = no limit)."""
        def per_unit(o: Offer) -> float:
            return o.comparison_price or (o.unit_price or 1e9)
        picks, remaining = [], amount
        def priority(item):
            o = item[0]
            metric = o.delivery_reliability if optimize == "reliability" else o.co2_kg_per_kg
            return ((metric is None, -metric if optimize == "reliability" and metric is not None
                     else metric or 0, per_unit(o)) if optimize != "price" else (per_unit(o),))
        for offer, avail_units in sorted(pool, key=priority):
            if remaining <= 1e-6:
                break
            if not offer.content or offer.content.unit != unit:
                continue
            need_units = offer.units_for(remaining, unit) or 0
            lot = offer.lot_size or 1.0
            cap = need_units if avail_units is None else (avail_units // lot) * lot
            take = min(need_units, cap)
            if take <= 0:
                continue
            picks.append((offer, take))
            remaining -= take * offer.content.per_sales_unit
        if len(picks) < 2 or remaining > 1e-6:
            return None
        return self._option(picks, amount, unit, "split across products (partial availability)",
                            split=True)

    # -- ordering --------------------------------------------------------------

    def place_order(self, mandate: Mandate, lines: list[OrderLineIn], idempotency_key: str,
                    rationale: str = "", *, alternatives: list[dict[str, Any]] | None = None,
                    agent_passport: str | None = None) -> dict[str, Any]:
        """Check a proposed order against the mandate and act according to the autonomy mode.

        Guardrails run first and are the same in every mode; a block is always `rejected`.
        Otherwise the effective autonomy mode decides what happens:
        advise -> `proposal` (nothing is committed), confirm_cart -> `awaiting_cart_confirmation`
        (the buyer confirms the cart, then guardrail approvals still apply), autonomous ->
        `placed` or `pending_approval`.
        """
        existing = self.store.order_by_key(mandate.buyer_id, idempotency_key)
        if existing:
            return existing
        ts = now()
        proposals: list[LineProposal] = []
        snapshots: list[dict] = []
        stored_refs: list[dict[str, Any]] = []
        for ln in lines:
            stored = self.store.get_offer(ln.offer_id)
            if not stored:
                raise GatewayError(f"unknown offer {ln.offer_id}")
            data, expires = stored
            offer = offer_from_dict(data)
            # Re-check availability right before committing; the price stays locked.
            try:
                rec = self.client.availability([offer.product_id],
                                               date.fromisoformat(offer.requested_date),
                                               force_refresh=True)
                offer.delivery = assess_delivery(rec.get(offer.product_id),
                                                 date.fromisoformat(offer.requested_date),
                                                 ln.quantity)
            except Exception:
                # A quote does not reserve stock. Never submit stale availability or
                # expose provider diagnostics in a proposed order, response or audit.
                raise GatewayError("current availability could not be verified; "
                                   "retry with fresh offers") from None
            # Provenance: an offer locked with the original's price (find_substitutes) is a
            # substitute whether or not the agent says so; a plain quote() offer is not.
            ref_pid = (data.get("price_reference") or {}).get("product_id")
            found_for = ref_pid if ref_pid and ref_pid != offer.product_id else None
            subs_for = ln.substitutes_for or found_for
            official = bool(subs_for and
                            offer.product_id in self.catalog.substitutes_of(subs_for))
            fills = {} if subs_for else (data.get("fills_shortfall_of") or {})
            proposals.append(LineProposal(
                offer, ln.quantity, ts > expires, subs_for, official,
                fills_shortfall_of=fills.get("product_id"),
                needs_kitchen_check=self._related_category(subs_for, offer.product_id),
                found_for=found_for, substitute_inferred=not ln.substitutes_for and bool(subs_for)))
            stored_refs.append(data.get("price_reference") or fills)
            snapshots.append({"offer_id": ln.offer_id, "quantity": ln.quantity,
                              "substitutes_for": subs_for, "note": ln.note,
                              "offer": offer.to_dict()})
            if subs_for and not ln.substitutes_for:
                snapshots[-1]["substitutes_for_inferred"] = True
        if mandate.max_substitute_price_increase_pct is not None:
            self._price_references(proposals, lines, stored_refs)
        for p, s in zip(proposals, snapshots):
            if p.fills_shortfall_of:
                s["fills_shortfall_of"] = p.fills_shortfall_of
            if p.needs_kitchen_check:
                s["needs_kitchen_check"] = True
            if p.reference:
                s["price_reference"] = asdict(p.reference)

        signature_ok = self._mandate_signature_ok(mandate)
        autonomy = self._autonomy(mandate, agent_passport)
        # Reading the cumulative spend, deciding and recording the order happen under one
        # lock, so two parallel orders cannot both fit into the same remaining budget.
        with self.store.lock:
            existing = self.store.order_by_key(mandate.buyer_id, idempotency_key)
            if existing:
                return existing
            prior = self.store.autonomous_spend_since(mandate.buyer_id, mandate.mandate_id,
                                                      ts - timedelta(hours=SPEND_WINDOW_HOURS))
            decision = evaluate(mandate, proposals, now=ts,
                                signature_ok=signature_ok,
                                prior_spend_eur=prior)
            mode = autonomy["effective"]
            if decision.outcome == "rejected":
                status = "rejected"
            elif mode == "advise":
                status = "proposal"
            elif mode == "confirm_cart":
                status = "awaiting_cart_confirmation"
            else:
                status = {"auto": "placed", "needs_approval": "pending_approval"}[decision.outcome]
            holds = status in ("pending_approval", "awaiting_cart_confirmation")
            data: dict[str, Any] = {
                "mandate": mandate.model_dump(mode="json", exclude={"signature"}),
                "lines": snapshots, "decision": decision.to_dict(), "autonomy": autonomy,
                "rationale": rationale, "alternatives": alternatives or [], "approvals": [],
            }
            if status in ("proposal", "awaiting_cart_confirmation"):
                data["cart"] = _cart(snapshots, decision, rationale, alternatives or [])
            record = {
                "idempotency_key": idempotency_key, "buyer_id": mandate.buyer_id,
                "agent_id": mandate.agent_id, "mandate_id": mandate.mandate_id,
                "status": status, "total_eur": decision.total_eur,
                "hold_until": ((ts + timedelta(hours=self.settings.approval_hold_hours)).isoformat()
                               if holds else None),
                "data": data,
            }
            order = self.store.create_order(record)
        oid = order["order_id"]
        self.store.audit(mandate.agent_id, "order.received", oid,
                         f"{mandate.buyer_name}'s agent proposed {len(lines)} line(s), "
                         f"{decision.total_eur:.2f} EUR excl. VAT",
                         {"rationale": rationale, "lines": [s["offer_id"] for s in snapshots]})
        self.store.audit("policy-engine", f"policy.{decision.outcome}", oid,
                         _decision_summary(decision), decision.to_dict())
        if status == "proposal":
            self.store.audit("policy-engine", "order.proposal", oid,
                             f"autonomy mode advise ({autonomy['reason']}): kept as a "
                             "recommendation, nothing ordered", autonomy)
        elif status == "awaiting_cart_confirmation":
            self.store.audit("policy-engine", "cart.awaiting_confirmation", oid,
                             f"autonomy mode confirm_cart ({autonomy['reason']}): the buyer "
                             "must confirm the cart before anything is ordered", autonomy)
        elif status == "placed":
            order = self._commit(order, actor="policy-engine")
        self._valio_review(order, "order.created")
        return order

    def _related_category(self, original_id: str | None, product_id: str) -> bool:
        """A substitute from another category than the original's (tier "family")."""
        if not original_id:
            return False
        a, b = self.catalog.get(original_id), self.catalog.get(product_id)
        return bool(a and b and a.category != b.category)

    def _price_references(self, proposals: list[LineProposal], lines: list[OrderLineIn],
                          stored: list[dict[str, Any]]) -> None:
        """The original's price for every replacing line, from Valio only, in this order:
        a signed offer for the original in the same order; the price the gateway quoted for
        the original when it priced this substitute or built this split; the gateway's own
        live quote now (one product). Never a number from the agent."""
        live: dict[tuple[str, str], PriceReference | None] = {}
        for p, ref_d in zip(proposals, stored):
            target = p.substitutes_for or p.fills_shortfall_of
            if not target:
                continue
            same = next(((q, ln) for q, ln in zip(proposals, lines)
                         if q.offer.product_id == target), None)
            if same:
                p.reference = price_reference(same[0].offer,
                                              f"signed offer {same[1].offer_id} in this order")
            elif ref_d.get("product_id") == target:
                p.reference = PriceReference(**ref_d)
            else:
                key = (target, p.offer.requested_date)
                if key not in live:
                    live[key] = self._quote_reference(target,
                                                      date.fromisoformat(p.offer.requested_date))
                p.reference = live[key]

    def _quote_reference(self, product_id: str, requested: date) -> PriceReference | None:
        """Price only (one product) for the original of a substitute line."""
        product = self.catalog.get(product_id)
        if product is None:
            return None
        try:
            self.live_lookups += 1
            price = self.client.prices([product_id], requested).get(product_id)
        except Exception as e:  # no verified price: the policy asks a human instead
            log.warning("could not quote original %s for a price check: %s", product_id, e)
            return None
        return price_reference(build_offer(product, price, None, requested),
                               "the gateway's live quote of the original at order time")

    def _mandate_signature_ok(self, mandate: Mandate) -> bool:
        return mandate.verify(self.buyers.jwks(mandate.buyer_id))

    def _autonomy(self, mandate: Mandate, agent_passport: str | None) -> dict[str, Any]:
        """effective = min(mandate.autonomy_mode, passport.max_mode); no valid passport for
        this agent caps it at confirm_cart."""
        requested = mandate.autonomy_mode
        passport: dict[str, Any] | None = None
        problem = "no Agent Passport"
        if agent_passport:
            try:
                claims = self.signer.verify(agent_passport, TYP_PASSPORT)
                if claims.get("agent_id") != mandate.agent_id:
                    problem = (f"passport is for {claims.get('agent_id')}, "
                               f"not {mandate.agent_id}")
                elif claims.get("max_mode") not in MODE_ORDER:
                    problem = "passport has no valid max_mode"
                elif expired := passport_problem(claims):
                    problem = expired
                else:
                    passport = claims
            except SignatureError as e:
                problem = f"passport not accepted: {e}"
        cap = passport["max_mode"] if passport else NO_PASSPORT_MAX_MODE
        effective = effective_mode(requested, cap)
        if passport:
            why = f"mandate {requested}, passport max {cap}"
        else:
            why = f"mandate {requested}, {problem} -> max {cap}"
        return {"requested": requested, "passport_max": cap, "effective": effective,
                "passport": ({k: passport.get(k) for k in ("agent_id", "model", "harness",
                                                           "max_mode", "bench_score",
                                                           "issued_at", "exp")}
                             if passport else None),
                "reason": why}

    def _revalidate_held_order(self, order: dict[str, Any], actor: str) -> dict[str, Any]:
        """Human approval holds the quoted price, not supplier stock or delivery cutoffs.

        The signed mandate was verified when this held order was created. Its persisted
        snapshot deliberately omits the signature: do not feed that snapshot back through
        signature evaluation or expire the original 60-minute offer during a 24-hour hold.
        """
        data = order["data"]
        checks, deliveries = [], []
        expired = False

        def block(rule: str, detail: str, line: int | None = None) -> None:
            checks.append({"rule": rule, "outcome": "block", "detail": detail, "line": line})

        def check_windows() -> None:
            nonlocal expired
            at = now()
            if at > datetime.fromisoformat(data["mandate"]["valid_until"]):
                expired = True
                block("commit.mandate.validity", "mandate expired while waiting for human approval")
            if order["hold_until"] and at > datetime.fromisoformat(order["hold_until"]):
                expired = True
                block("commit.hold.validity", "human approval price hold expired; request new offers")

        check_windows()
        if not checks:
            by_date: dict[str, list[str]] = {}
            for line in data["lines"]:
                offer = line["offer"]
                by_date.setdefault(offer["requested_date"], []).append(offer["product_id"])
            current: dict[tuple[str, str], Any] = {}
            for requested, ids in by_date.items():
                ids = list(dict.fromkeys(ids))
                try:
                    records = self.client.availability(ids, date.fromisoformat(requested), force_refresh=True)
                    for pid in ids:
                        current[(requested, pid)] = records.get(pid)
                except Exception:
                    # An SDK/HTTP diagnostic can contain sensitive request information.
                    block("commit.availability.unverified",
                          "current availability could not be verified; request fresh offers before ordering")
            if not checks:
                for i, line in enumerate(data["lines"]):
                    offer = line["offer"]
                    try:
                        fresh = assess_delivery(current[(offer["requested_date"], offer["product_id"])],
                                                date.fromisoformat(offer["requested_date"]),
                                                line["quantity"], now=now())
                    except Exception:
                        block("commit.availability.unverified", "current delivery data is invalid", i)
                        continue
                    deliveries.append({"product_id": offer["product_id"], "quantity": line["quantity"],
                                       "requested_date": offer["requested_date"], "delivery": asdict(fresh)})
                    if fresh.status in BLOCKING or fresh.status == "partial":
                        block("commit.delivery." + fresh.status,
                              f"{offer['product_id']}: current delivery is {fresh.status}; "
                              "the approved basket cannot be fulfilled", i)
                    elif fresh.available_amount is not None and not math.isfinite(fresh.available_amount):
                        block("commit.availability.unverified", "current stock amount is invalid", i)
                    elif fresh.available_amount is not None and line["quantity"] > fresh.available_amount:
                        # assess_delivery classifies an unavailable requested date as late
                        # before considering quantity. An approved late date never waives stock.
                        block("commit.delivery.partial", f"{offer['product_id']}: only "
                              f"{fresh.available_amount:g} available of {line['quantity']:g} requested", i)
                    elif fresh.status == "late" and (
                            offer["delivery"]["status"] != "late"
                            or fresh.earliest_date != offer["delivery"]["earliest_date"]):
                        block("commit.delivery.changed", f"{offer['product_id']}: delivery moved to "
                              f"{fresh.earliest_date}; request a new proposal instead of "
                              "silently rescheduling", i)
            check_windows()  # Network latency can cross the cutoff/mandate/hold boundary.
        data["commit_revalidation"] = {"at": now().isoformat(),
                                       "status": "blocked" if checks else "verified",
                                       "checks": checks, "deliveries": deliveries}
        if checks:
            reasons = [c["detail"] for c in checks]
            data["decision"] = {**data["decision"], "outcome": "rejected", "reasons": reasons,
                                "checks": [*data["decision"]["checks"], *checks]}
            order = self.store.update_order(order["order_id"], status="expired" if expired else "rejected",
                                            data=data, hold_until=None)
            self.store.audit("policy-engine", "order.commit_blocked", order["order_id"],
                             "; ".join(reasons), data["commit_revalidation"])
        else:
            order = self.store.update_order(order["order_id"], data=data)
            self.store.audit(actor, "order.revalidated", order["order_id"],
                             "fresh stock and delivery cutoff checked before committing held basket",
                             data["commit_revalidation"])
        return order

    def _commit(self, order: dict[str, Any], actor: str) -> dict[str, Any]:
        with self.store.lock:  # read-modify-write of order.data
            # Never submit a stale copy twice after a concurrent/repeated callback.
            order = self.order(order["order_id"])
            if order.get("order_number") or order["data"].get("receipt"):
                return order
            if order["status"] in ("pending_approval", "awaiting_cart_confirmation"):
                order = self._revalidate_held_order(order, actor)
                if order["status"] in ("rejected", "expired"):
                    return order
            elif order["status"] != "placed":
                raise GatewayError(f"order {order['order_id']} is not eligible for commitment")
            number = self.backend.submit(order)
            order = self.store.update_order(order["order_id"], status="placed",
                                            order_number=number, hold_until=None)
            entry = self.store.audit(actor, "order.placed", order["order_id"],
                                     f"order {number} placed with Valio Aimo, "
                                     f"{order['total_eur']:.2f} EUR excl. VAT",
                                     {"order_number": number})
            data = order["data"]
            jws = self.signer.sign(_receipt_claims(order, entry), TYP_RECEIPT)
            data["receipt"] = {"claims": decode_unverified(jws), "jws": jws}
            return self.store.update_order(order["order_id"], data=data)

    # -- cart confirmation (confirm_cart mode, humans only) ----------------------

    def _cart_decision(self, order_id: str, confirmation: str, decision: str) -> tuple[dict, dict]:
        """Verify a buyer-signed cart confirmation; returns (order, its claims)."""
        order = self.store.get_order(order_id)
        if not order:
            raise GatewayError(f"unknown order {order_id}")
        try:
            claims = verify_jws(confirmation, self.buyers.jwks(order["buyer_id"]) or {},
                                typ=TYP_CART)
        except SignatureError as e:
            raise GatewayError(f"cart confirmation not accepted: {e}") from e
        if claims.get("order_id") != order_id or claims.get("buyer_id") != order["buyer_id"]:
            raise GatewayError("cart confirmation is for a different order or buyer")
        if claims.get("decision") != decision:
            raise GatewayError(f"cart confirmation says {claims.get('decision')}, not {decision}")
        cart = order["data"].get("cart")
        if not cart or claims.get("cart_hash") != cart_hash(cart):
            raise GatewayError("cart confirmation does not match the cart contents")
        approver = str(claims.get("confirmed_by") or "")
        order = self._held(order_id, approver, "awaiting_cart_confirmation")
        return order, claims

    def confirm_cart(self, order_id: str, confirmation: str) -> dict[str, Any]:
        """The buyer confirms the cart the agent filled, with a JWS signed by the buyer key
        over the cart hash. Guardrails still apply afterwards: an order within the mandate is
        placed, one with exceptions (e.g. over the autonomous spend limit) moves on to
        `pending_approval` and needs an approver like any other order."""
        with self.store.lock:
            order = self._confirm_cart(order_id, confirmation)
        self._valio_review(order, "cart.confirmed")  # never under the lock
        return order

    def _confirm_cart(self, order_id: str, confirmation: str) -> dict[str, Any]:
        order, claims = self._cart_decision(order_id, confirmation, "confirm")
        approver, note = claims["confirmed_by"], claims.get("note") or ""
        data = order["data"]
        data["cart_confirmation"] = {"by": approver, "decision": "confirmed", "note": note,
                                     "at": now().isoformat(), "cart_hash": claims["cart_hash"],
                                     "jws": confirmation}
        self.store.audit(approver, "cart.confirmed", order_id,
                         f"{approver} confirmed the cart: {note or 'no comment'}",
                         {"cart_total_eur": data["cart"]["total_eur"]})
        if data["decision"]["outcome"] == "needs_approval":
            hold = (now() + timedelta(hours=self.settings.approval_hold_hours)).isoformat()
            order = self.store.update_order(order_id, status="pending_approval", data=data,
                                            hold_until=hold)
            self.store.audit("policy-engine", "approval.required", order_id,
                             "cart confirmed, guardrails still need a sign-off: "
                             + "; ".join(data["decision"]["reasons"]), {})
            return order
        order = self.store.update_order(order_id, data=data)
        return self._commit(order, actor=approver)

    def decline_cart(self, order_id: str, confirmation: str) -> dict[str, Any]:
        with self.store.lock:
            return self._decline_cart(order_id, confirmation)

    def _decline_cart(self, order_id: str, confirmation: str) -> dict[str, Any]:
        order, claims = self._cart_decision(order_id, confirmation, "decline")
        approver, note = claims["confirmed_by"], claims.get("note") or ""
        data = order["data"]
        data["cart_confirmation"] = {"by": approver, "decision": "declined", "note": note,
                                     "at": now().isoformat(), "cart_hash": claims["cart_hash"],
                                     "jws": confirmation}
        order = self.store.update_order(order_id, status="cart_declined", data=data,
                                        hold_until=None)
        self.store.audit(approver, "cart.declined", order_id,
                         f"{approver} declined the cart: {note or 'no comment'}", {})
        return order

    def pending_carts(self, buyer_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_orders("awaiting_cart_confirmation", buyer_id)

    def demo_cart_confirmation(self, order_id: str, confirmed_by: str,
                               decision: str = "confirm", note: str = "") -> str:
        """Stand-in for the buyer's wallet: signs a cart confirmation with the demo buyer key
        kept in data/buyers/. A real buyer signs on its own device; never expose to agents."""
        order = self.order(order_id)
        if "cart" not in order["data"]:
            raise GatewayError(f"order {order_id} has no cart to confirm")
        return sign_cart_confirmation(self.buyers.demo_key(order["buyer_id"]), order,
                                      confirmed_by, decision, note)

    def issue_passport(self, agent_id: str, model: str, harness: str, max_mode: str,
                       bench_score: float | None = None, *,
                       valid_days: float = PASSPORT_DEFAULT_DAYS) -> str:
        """Valio issues an Agent Passport (JWS). Issuance process itself is out of scope."""
        passport = issue_passport(self.signer, agent_id, model, harness, max_mode, bench_score,
                                  valid_days=valid_days)
        exp = decode_unverified(passport)["exp"]
        self.store.audit("valio", "passport.issued", agent_id,
                         f"Agent Passport for {agent_id} ({model} / {harness}): max {max_mode}, "
                         f"valid {valid_days:g} day(s)",
                         {"model": model, "harness": harness, "max_mode": max_mode,
                          "bench_score": bench_score, "exp": exp})
        return passport

    def jwks(self) -> dict[str, Any]:
        """Valio's public signing keys (offers, receipts, passports)."""
        return self.signer.jwks()

    # -- approvals (humans only) ---------------------------------------------------

    def approve(self, order_id: str, approver: str, note: str = "") -> dict[str, Any]:
        with self.store.lock:
            order = self._approve(order_id, approver, note)
        event = "order.placed" if order["status"] == "placed" else "order.commit_blocked"
        self._valio_review(order, event)  # never under the lock
        return order

    def _approve(self, order_id: str, approver: str, note: str) -> dict[str, Any]:
        order = self._held(order_id, approver, "pending_approval")
        data = order["data"]
        data["approvals"].append({"by": approver, "decision": "approved", "note": note,
                                  "at": now().isoformat()})
        order = self.store.update_order(order_id, data=data)
        self.store.audit(approver, "approval.granted", order_id,
                         f"{approver} approved: {note or 'no comment'}",
                         {"reasons_reviewed": data["decision"]["reasons"]})
        return self._commit(order, actor=approver)

    def reject(self, order_id: str, approver: str, note: str = "") -> dict[str, Any]:
        with self.store.lock:
            return self._reject(order_id, approver, note)

    def _reject(self, order_id: str, approver: str, note: str) -> dict[str, Any]:
        order = self._held(order_id, approver, "pending_approval")
        data = order["data"]
        data["approvals"].append({"by": approver, "decision": "rejected", "note": note,
                                  "at": now().isoformat()})
        order = self.store.update_order(order_id, status="rejected", data=data, hold_until=None)
        self.store.audit(approver, "approval.denied", order_id,
                         f"{approver} rejected: {note or 'no comment'}", {})
        return order

    def _held(self, order_id: str, approver: str, status: str) -> dict[str, Any]:
        order = self.store.get_order(order_id)
        if not order:
            raise GatewayError(f"unknown order {order_id}")
        if order["status"] != status:
            raise GatewayError(f"order {order_id} is {order['status']}, not {status}")
        allowed = order["data"]["mandate"].get("approvers") or []
        if allowed and approver not in allowed:
            raise GatewayError(f"{approver} is not an approver for this buyer")
        if order["hold_until"] and now() > datetime.fromisoformat(order["hold_until"]):
            self.store.update_order(order_id, status="expired")
            self.store.audit("gateway", "order.expired", order_id,
                             "price lock expired before a human answered; request new offers", {})
            raise GatewayError("confirmation window expired; request new offers")
        return order

    # -- reads -----------------------------------------------------------------

    def order(self, order_id: str) -> dict[str, Any]:
        o = self.store.get_order(order_id)
        if not o:
            raise GatewayError(f"unknown order {order_id}")
        return o

    def pending_approvals(self, buyer_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_orders("pending_approval", buyer_id)

    def audit(self, subject: str | None = None) -> dict[str, Any]:
        ok, broken = self.store.verify_audit()
        return {"chain_valid": ok, "first_broken_seq": broken,
                "entries": self.store.audit_log(subject)}

    def record_note(self, agent_id: str, subject: str, note: str,
                    order_id: str | None = None) -> dict[str, Any]:
        """Let the buying agent put its own reasoning into the shared trail.

        With `order_id` the note is filed under that order (so explain_order shows it) and
        `subject` (e.g. the product it is about) is kept in the entry's data."""
        if order_id:
            order = self.store.get_order(order_id)
            if not order:
                raise GatewayError(f"unknown order {order_id}")
            if order["agent_id"] != agent_id:
                raise GatewayError(f"order {order_id} was placed by another agent; "
                                   "an agent may only annotate its own orders")
            return self.store.audit(agent_id, "agent.note", order_id,
                                    note if subject in ("", order_id) else f"[{subject}] {note}",
                                    {"about": subject})
        return self.store.audit(agent_id, "agent.note", subject, note)

    # -- Valio's review agent (advisory; never decides) ---------------------------

    def _valio_review(self, order: dict[str, Any], trigger: str) -> None:
        """TTA_VALIO_REVIEW=off|sync|async. Errors are logged, never raised to the buyer."""
        try:
            from . import valio_agent

            valio_agent.hook(self, order, trigger)
        except Exception:  # pragma: no cover - the hook itself already catches
            pass


# Claims a non-official substitute must keep (diet and intolerance); marketing claims such as
# FREE_FROM_ADDITIVES or LOW_SODIUM_SALT may be lost, and the differences say so.
DIETARY_CLAIMS = {"FREE_FROM_LACTOSE", "LOW_LACTOSE", "FREE_FROM_GLUTEN", "FREE_FROM_SOY",
                  "FREE_FROM_MILK", "FREE_FROM_EGGS", "FREE_FROM_WHEAT", "VEGAN",
                  "VEGETARIAN"}
TIERS = ("official", "category", "family")  # substitutes: Valio's list, same, related category
RULED_OUT_SHOWN = 20   # substitutes: first ruled-out candidates returned, plus the count


def _effective_claims(p: Product) -> set[str]:
    """Claims plus what they imply: a vegan product without milk or lactose among its
    allergens (contains or may contain) is also lactose-free, low-lactose and vegetarian.
    FREE_FROM_MILK is never derived: it needs the producer's own claim."""
    have = set(p.claims)
    if "FREE_FROM_LACTOSE" in have:
        have.add("LOW_LACTOSE")
    if "VEGAN" in have and not {"AM", "ML"} & (set(p.contains) | set(p.may_contain)):
        have |= {"FREE_FROM_LACTOSE", "LOW_LACTOSE", "VEGETARIAN"}
    return have


def _words(tokens: set[str], n: int = 6) -> str:
    words = sorted(t for t in tokens if len(t) > 2 and not t.isdigit()
                   and t not in {"and", "och", "with", "contain", "may"})
    return (", ".join(words[:n]) + (f" +{len(words) - n} more" if len(words) > n else "")
            if words else "no data")


def _size(size: float | None, unit: str | None) -> str:
    return f"{size:g} {unit}" if size else "no data"


def _why(offer: Offer, reason: str) -> dict[str, Any]:
    return {"product_id": offer.product_id, "name": offer.name, "reason": reason,
            "delivery": asdict(offer.delivery)}


def _cart(snapshots: list[dict], decision, rationale: str,
          alternatives: list[dict]) -> dict[str, Any]:
    """What the buyer sees before confirming: lines, totals, why, and what else was possible."""
    lines = []
    for s in snapshots:
        o = s["offer"]
        price = o.get("unit_price")
        lines.append({"offer_id": s["offer_id"], "product_id": o["product_id"],
                      "name": o["name"], "quantity": s["quantity"],
                      "sales_unit": o["sales_unit"], "unit_price_eur": price,
                      "line_total_eur": round((price or 0) * s["quantity"], 2),
                      "earliest_date": o["delivery"]["earliest_date"],
                      "substitutes_for": s.get("substitutes_for"), "note": s.get("note")})
    after = {"auto": "placed", "needs_approval": "pending_approval",
             "rejected": "rejected"}[decision.outcome]
    cart = {"lines": lines, "total_eur": decision.total_eur, "currency": "EUR",
            "vat": "excluded", "rationale": rationale, "alternatives": alternatives,
            "after_confirmation": after,
            "exceptions": decision.reasons if decision.outcome == "needs_approval" else []}
    cart["cart_hash"] = cart_hash(cart)
    return cart


def _receipt_claims(order: dict[str, Any], audit_entry: dict[str, Any]) -> dict[str, Any]:
    """Order Receipt: what was ordered, why it was allowed, and the audit head at that point."""
    data = order["data"]
    lines = []
    for s in data["lines"]:
        o = s["offer"]
        price = o.get("unit_price")
        lines.append({"offer_id": s["offer_id"], "product_id": o["product_id"], "name": o["name"],
                      "quantity": s["quantity"], "sales_unit": o["sales_unit"],
                      "unit_price_eur": price,
                      "line_total_eur": round((price or 0) * s["quantity"], 2),
                      "substitutes_for": s.get("substitutes_for")})
    checks = data["decision"]["checks"]
    fired = [{"rule": c["rule"], "outcome": c["outcome"], "detail": c["detail"]}
             for c in checks if c["outcome"] != "ok"]
    conf = data.get("cart_confirmation")
    return {
        "order_id": order["order_id"], "order_number": order["order_number"],
        "buyer_id": order["buyer_id"], "agent_id": order["agent_id"],
        "mandate_id": order["mandate_id"], "lines": lines,
        "total_eur": order["total_eur"], "currency": "EUR", "vat": "excluded",
        "policy_outcome": data["decision"]["outcome"], "rules_fired": fired,
        "rules_checked": len(checks),
        "autonomy_mode": (data.get("autonomy") or {}).get("effective"),
        "cart_confirmed_by": conf["by"] if conf else None,
        "approved_by": [a["by"] for a in data.get("approvals", [])
                        if a["decision"] == "approved"],
        "placed_at": audit_entry["ts"],
        "audit_head": {"seq": audit_entry["seq"], "hash": audit_entry["hash"]},
    }


def _decision_summary(decision) -> str:
    if decision.outcome == "auto":
        return "all guardrails passed; agent may order on its own"
    head = "needs human approval" if decision.outcome == "needs_approval" else "rejected"
    return f"{head}: " + "; ".join(decision.reasons)


def _explain(options: list[dict], considered: int, ruled_out: list[dict],
             partials: list) -> str:
    if not options:
        return (f"No way to cover the need: {considered} candidate(s) checked, "
                f"{len(ruled_out)} ruled out, {len(partials)} only partially available.")
    best = options[0]
    names = ", ".join(f'{ln["quantity"]:g} {ln["sales_unit"]} {ln["name"]}' for ln in best["lines"])
    return (f"Selected way to cover {best['requested']:g} {best['unit']}: {names} for "
            f"{best['total_eur']:.2f} EUR excl. VAT ({best['kind']}). {considered} candidate(s) "
            f"checked, {len(ruled_out)} ruled out (see ruled_out for reasons)."
            + "".join(f" Alternative ({o['rank']}): "
                      + ", ".join(ln["name"] for ln in o["lines"])
                      + f" — {o.get('trade_off', '')}." for o in options[1:]))


def _identity(option: dict) -> tuple:
    return tuple(sorted((ln["product_id"], ln["quantity"]) for ln in option["lines"]))


def _within(option: dict, base: float, max_premium_pct: float | None) -> bool:
    return max_premium_pct is None or option["total_eur"] <= base * (1 + max_premium_pct / 100) + 1e-8


def _metric(label: str, a: float | None, b: float | None, unit: str, same: bool) -> str:
    if b is not None and (same or a is not None):
        return f"{label} {b:.2f}{unit}" if same else f"{label} {a:.2f} → {b:.2f}{unit}"
    if b is not None:
        return f"{label} {b:.2f}{unit}, cheapest option: no data"
    if a is not None and not same:
        return f"{label} no data (cheapest option: {a:.2f}{unit})"
    return f"{label} no data"


def trade_off(base: dict, option: dict) -> str:
    """What this option costs and gives compared with the cheapest one, in absolute values:
    '+4.0 % price (202.00 → 210.08 EUR); reliability 0.92 → 0.99; CO₂ 1.20 → 0.95 kg CO₂e/kg
    (category avg)'. Missing data is said, never guessed."""
    same = _identity(option) == _identity(base)
    if same:
        price = f"cheapest option ({base['total_eur']:.2f} EUR)"
    else:
        pct = (option["total_eur"] / base["total_eur"] - 1) * 100 if base["total_eur"] else 0.0
        price = f"{pct:+.1f} % price ({base['total_eur']:.2f} → {option['total_eur']:.2f} EUR)"
    return "; ".join([
        price,
        _metric("reliability", base["delivery_reliability"], option["delivery_reliability"], "",
                same),
        _metric("CO₂", base["co2_kg_per_kg"], option["co2_kg_per_kg"],
                " kg CO₂e/kg (category avg)", same),
    ])


def over_premium(options: list[dict], max_premium_pct: float | None) -> list[dict]:
    """Options cut by the buyer's premium cap, as ruled_out entries with the reason."""
    if max_premium_pct is None or not options:
        return []
    base = min(o["total_eur"] for o in options)
    out, seen = [], set()
    for o in sorted(options, key=lambda o: o["total_eur"]):
        if _within(o, base, max_premium_pct) or _identity(o) in seen:
            continue
        seen.add(_identity(o))
        pct = (o["total_eur"] / base - 1) * 100 if base else 0.0
        out.append({
            "product_id": "+".join(ln["product_id"] for ln in o["lines"]),
            "name": " + ".join(ln.get("name") or ln["product_id"] for ln in o["lines"]),
            "reason": f"over max_premium_pct {max_premium_pct:g} %: {o['total_eur']:.2f} EUR "
                      f"is {pct:+.1f} % vs the cheapest {base:.2f} EUR",
            "total_eur": o["total_eur"], "delivery": None})
    return out


def rank_options(options: list[dict], optimize: str = "price",
                 max_premium_pct: float | None = None) -> list[dict]:
    """Good / better / best: up to three distinct options within the premium cap.

    First the objective winners (best_price, most_reliable, lowest_co2, plus balanced for
    optimize=balanced); one option may win several objectives (`objectives` lists them all).
    Free places are filled with the next cheapest options (`rank` runner_up / third,
    `objectives` []). Options above `max_premium_pct` are never shown; `over_premium` lists
    them for ruled_out. Unknown metrics never win an objective. Every option carries a
    `trade_off` against the cheapest one with absolute values.
    """
    if not options:
        return []
    ordered = sorted(options, key=lambda o: o["total_eur"])
    cheapest = ordered[0]
    base = cheapest["total_eur"]
    pool = [o for o in ordered if _within(o, base, max_premium_pct)]

    def winner(metric, reverse=False):
        known = [o for o in pool if o[metric] is not None]
        return (min(known, key=lambda o: ((-o[metric] if reverse else o[metric]), o["total_eur"]))
                if known else None)
    winners = [("best_price", cheapest)]
    for objective, metric, reverse in (("most_reliable", "delivery_reliability", True),
                                       ("lowest_co2", "co2_kg_per_kg", False)):
        candidate = winner(metric, reverse)
        if candidate is not None:
            winners.append((objective, candidate))
    unavailable = optimize in {"co2", "reliability"} and not any(
        rank == {"co2": "lowest_co2", "reliability": "most_reliable"}[optimize]
        for rank, _ in winners)
    preferred = {"price": "best_price", "reliability": "most_reliable", "co2": "lowest_co2"}
    if optimize == "balanced":
        def score(o):
            terms = [o["total_eur"] / base if base else 1]
            for metric, reverse in (("delivery_reliability", True), ("co2_kg_per_kg", False)):
                vals = [p[metric] for p in pool if p[metric] is not None]
                v = o[metric]
                terms.append(1 if v is None or not vals else
                             (max(vals) - v if reverse else v - min(vals)) /
                             (max(vals) - min(vals) or 1))
            return sum(terms)
        balanced = min(pool, key=lambda o: (score(o), o["total_eur"]))
        winners.sort(key=lambda pair: (score(pair[1]), pair[1]["total_eur"]))
        winners.insert(0, ("balanced", balanced))
    else:
        winners.sort(key=lambda pair: pair[0] != preferred[optimize])

    chosen: list[tuple[str, dict, list[str]]] = []
    seen: set[tuple] = set()
    for rank, original in winners:
        identity = _identity(original)
        if identity in seen:
            continue
        seen.add(identity)
        chosen.append((rank, original,
                       [r for r, c in winners if _identity(c) == identity]))
    for original in pool:                 # next cheapest fill the free places
        if len(chosen) >= 3:
            break
        if _identity(original) in seen:
            continue
        seen.add(_identity(original))
        chosen.append(("runner_up" if len(chosen) == 1 else "third", original, []))

    out = []
    for rank, original, objectives in chosen[:3]:
        o = dict(original)
        o["rank"] = rank
        o["objectives"] = objectives
        o["trade_off"] = trade_off(cheapest, o)
        if unavailable:
            o["optimization_note"] = (
                f"no {optimize} data within the allowed premium; ranked by price instead")
        out.append(o)
    return out
