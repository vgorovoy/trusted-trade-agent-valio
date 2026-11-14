"""Deterministic checks of one order, from Valio's side. No LLM, no API calls.

Each check is `{check, status: ok|note|flag, line, evidence, data}`; `line` is the index of
the order line or None for order-level checks. The checks only read what the gateway
already has: the order snapshot, the price-locked offers, this buyer's earlier orders and
the static catalogue.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any

from ..catalog import ALLERGEN_NAMES, AllergenFilter
from ..signing import SignatureError, verify_receipt
from ..store import now

# Thresholds against the buyer's own history (median of earlier lines of the product).
QTY_NOTE, QTY_FLAG = 1.5, 3.0
PRICE_NOTE, PRICE_FLAG = 0.10, 0.25
RELIABILITY_NOTE = 0.9
CUTOFF_FLAG_H, CUTOFF_NOTE_H = 2, 6
HISTORY_STATUSES = {"placed", "pending_approval", "awaiting_cart_confirmation"}


def _check(name: str, status: str, evidence: str, line: int | None = None,
           **data: Any) -> dict[str, Any]:
    return {"check": name, "status": status, "line": line, "evidence": evidence, "data": data}


def _label(i: int, offer: dict) -> str:
    return f"line {i + 1} {offer.get('name') or offer.get('product_id')}"


def _names(codes: set[str]) -> str:
    return ", ".join(sorted(ALLERGEN_NAMES.get(c, c) for c in codes))


def buyer_history(gw: Any, order: dict[str, Any]) -> dict[str, list[dict]]:
    """product_id -> earlier lines of this buyer (quantity, unit price), oldest first."""
    hist: dict[str, list[dict]] = {}
    earlier = [o for o in gw.store.list_orders(buyer_id=order["buyer_id"])
               if o["order_id"] != order["order_id"] and o["status"] in HISTORY_STATUSES
               and o["created_at"] < order["created_at"]]
    for o in sorted(earlier, key=lambda o: o["created_at"]):
        for s in o["data"].get("lines", []):
            off = s.get("offer") or {}
            hist.setdefault(off.get("product_id"), []).append(
                {"quantity": s.get("quantity"), "unit_price": off.get("unit_price"),
                 "order_id": o["order_id"], "at": o["created_at"][:10]})
    return hist


def history_checks(gw: Any, order: dict[str, Any]) -> list[dict[str, Any]]:
    hist = buyer_history(gw, order)
    out = []
    if not hist:
        out.append(_check("history.first_order", "note",
                          "first order from this buyer in the gateway: no history to compare"))
        return out
    for i, s in enumerate(order["data"].get("lines", [])):
        off = s["offer"]
        past = hist.get(off["product_id"])
        label = _label(i, off)
        if not past:
            out.append(_check("history.new_product", "note",
                              f"{label}: never ordered by this buyer before", i))
            continue
        qtys = [p["quantity"] for p in past if p["quantity"]]
        usual = median(qtys) if qtys else None
        q = s["quantity"]
        if usual:
            ratio = q / usual
            status = "flag" if ratio >= QTY_FLAG else "note" if ratio >= QTY_NOTE else "ok"
            out.append(_check("history.quantity", status,
                              f"{label}: {q:g} {off['sales_unit']} vs usual {usual:g} "
                              f"(median of {len(qtys)} earlier order line(s)), x{ratio:.1f}",
                              i, quantity=q, usual=usual, ratio=round(ratio, 2)))
        prices = [p["unit_price"] for p in past if p["unit_price"]]
        price = off.get("unit_price")
        if prices and price:
            last = prices[-1]
            change = price / last - 1
            status = ("flag" if abs(change) >= PRICE_FLAG else
                      "note" if abs(change) >= PRICE_NOTE else "ok")
            out.append(_check("history.unit_price", status,
                              f"{label}: {price:.2f} EUR/{off['sales_unit']} vs last paid "
                              f"{last:.2f} ({change:+.0%})", i,
                              unit_price=price, last_paid=last, change=round(change, 4)))
    return out


def safety_checks(gw: Any, order: dict[str, Any]) -> list[dict[str, Any]]:
    """Second look at allergens and substitutions, independent of the policy engine."""
    data = order["data"]
    m = data.get("mandate") or {}
    # The same predicate as search / plan / policy.evaluate (code families, lactose rule).
    banned = AllergenFilter.parse(m.get("excluded_allergens", []))
    required = set(m.get("required_claims", []))
    out = []
    for i, s in enumerate(data.get("lines", [])):
        off = s["offer"]
        label = _label(i, off)
        # The catalogue is the source of truth; the offer snapshot is what the buyer saw.
        product = gw.catalog.get(off["product_id"]) if gw.catalog else None
        contains = set(off.get("contains_allergens") or []) | set(
            product.contains if product else [])
        may = set(off.get("may_contain_allergens") or []) | set(
            product.may_contain if product else [])
        claims = set(off.get("claims") or []) | set(product.claims if product else [])
        if hit := banned.hits(contains, claims):
            out.append(_check("safety.allergen", "flag",
                              f"{label}: contains {_names(hit)}, excluded by "
                              "the buyer's mandate", i))
        elif hit := banned.hits(may, claims):
            status = "flag" if m.get("exclude_may_contain") else "note"
            out.append(_check("safety.may_contain", status,
                              f"{label}: may contain {_names(hit)} (mandate "
                              f"{'excludes' if status == 'flag' else 'allows'} traces)", i))
        elif banned:
            out.append(_check("safety.allergen", "ok",
                              f"{label}: none of the excluded allergens "
                              f"({_names(set(banned.codes))}), also not as traces", i))
        missing = required - set(off.get("claims") or [])
        if missing:
            out.append(_check("safety.claims", "flag",
                              f"{label}: lacks required claim(s) {', '.join(sorted(missing))}", i))
        elif required:
            out.append(_check("safety.claims", "ok",
                              f"{label}: has required claim(s) {', '.join(sorted(required))}", i))
        if s.get("substitutes_for"):
            official = off["product_id"] in gw.catalog.substitutes_of(s["substitutes_for"])
            out.append(_check("safety.substitution", "note" if official else "flag",
                              f"{label}: substitutes {s['substitutes_for']} "
                              f"({'on' if official else 'NOT on'} Valio's official substitution "
                              f"list; mandate policy {m.get('substitution_policy')})", i,
                              official=official))
    return out


def delivery_checks(gw: Any, order: dict[str, Any]) -> list[dict[str, Any]]:
    data = order["data"]
    min_rel = (data.get("mandate") or {}).get("min_delivery_reliability") or RELIABILITY_NOTE
    out = []
    t = now()
    for i, s in enumerate(data.get("lines", [])):
        off = s["offer"]
        d = off.get("delivery") or {}
        label = _label(i, off)
        st = d.get("status")
        out.append(_check("delivery.status", "ok" if st == "available" else "flag",
                          f"{label}: {st}, earliest {d.get('earliest_date')}"
                          + (f" ({'; '.join(d.get('notes') or [])})" if d.get("notes") else ""),
                          i, delivery_status=st))
        rel = off.get("delivery_reliability")
        if rel is not None:
            out.append(_check("delivery.reliability", "note" if rel < min_rel else "ok",
                              f"{label}: historical delivery reliability {rel:.0%} "
                              f"(threshold {min_rel:.0%})", i, reliability=rel))
        cutoff = d.get("order_cutoff")
        if cutoff and order["status"] in ("pending_approval", "awaiting_cart_confirmation"):
            try:
                left = datetime.fromisoformat(cutoff) - t
            except ValueError:
                continue
            hours = left / timedelta(hours=1)
            status = ("flag" if hours < CUTOFF_FLAG_H else
                      "note" if hours < CUTOFF_NOTE_H else "ok")
            out.append(_check("delivery.cutoff", status,
                              f"{label}: order cut-off {cutoff}, {hours:.1f} h left while the "
                              "order waits for a human", i, hours_left=round(hours, 1)))
    return out


def integrity_checks(gw: Any, order: dict[str, Any]) -> list[dict[str, Any]]:
    """The order still matches Valio's own records: each line equals the price-locked offer
    Valio stored when it signed it, totals add up, the Order Receipt verifies. (The JWS the
    agent received is not stored with the order; the agent verifies it, not this check.)"""
    data = order["data"]
    jwks = gw.signer.jwks()
    out = []
    total = 0.0
    for i, s in enumerate(data.get("lines", [])):
        off = s["offer"]
        label = _label(i, off)
        stored = gw.store.get_offer(s["offer_id"])
        if not stored:
            out.append(_check("integrity.locked_offer", "flag",
                              f"{label}: offer {s['offer_id']} is not in Valio's records", i))
            continue
        locked, _expires = stored
        problems = [k for k in ("product_id", "unit_price", "sales_unit", "name")
                    if locked.get(k) != off.get(k)]
        price = off.get("unit_price") or 0
        total += price * s["quantity"]
        out.append(_check("integrity.locked_offer", "flag" if problems else "ok",
                          f"{label}: " + (f"order line differs from the locked offer in "
                                          f"{', '.join(problems)}" if problems else
                                          f"order line == locked offer (price {price:.2f} "
                                          f"EUR/{off['sales_unit']})"), i))
    stated = order.get("total_eur") or 0
    out.append(_check("integrity.total", "ok" if abs(round(total, 2) - stated) <= 0.011 else "flag",
                      f"order total {stated:.2f} EUR, sum of lines {total:.2f} EUR"))
    if data.get("receipt"):
        try:
            verify_receipt(data["receipt"], jwks)
            out.append(_check("integrity.receipt", "ok",
                              "signed Order Receipt verifies against Valio's JWKS"))
        except (SignatureError, KeyError) as e:
            out.append(_check("integrity.receipt", "flag", f"Order Receipt does not verify: {e}"))
    return out


def mandate_checks(gw: Any, order: dict[str, Any]) -> list[dict[str, Any]]:
    """Facts about who decided what: mandate signature, autonomy, policy, humans."""
    data = order["data"]
    dec = data.get("decision") or {}
    checks = {c["rule"]: c for c in dec.get("checks", [])}
    out = []
    sig = checks.get("mandate.signature")
    if sig:
        out.append(_check("mandate.signature", "ok" if sig["outcome"] == "ok" else "flag",
                          sig["detail"]))
    a = data.get("autonomy") or {}
    out.append(_check("mandate.autonomy", "ok" if a.get("passport") or
                      a.get("effective") != "autonomous" else "note",
                      f"effective mode {a.get('effective')} ({a.get('reason')})",
                      effective=a.get("effective")))
    outcome = dec.get("outcome")
    status = {"auto": "ok", "needs_approval": "note", "rejected": "flag"}.get(outcome, "note")
    out.append(_check("mandate.policy", status,
                      f"policy outcome {outcome}"
                      + (": " + "; ".join(dec.get("reasons", [])) if dec.get("reasons") else ""),
                      outcome=outcome))
    humans = [f"{x['by']} {x['decision']}" for x in data.get("approvals", [])]
    if (cc := data.get("cart_confirmation")):
        humans.append(f"{cc['by']} {cc['decision']} the cart")
    if humans:
        out.append(_check("mandate.humans", "ok", "human decisions: " + "; ".join(humans)))
    return out


def run_checks(gw: Any, order: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fn in (integrity_checks, safety_checks, delivery_checks, history_checks, mandate_checks):
        try:
            out.extend(fn(gw, order))
        except Exception as e:  # one broken check must not hide the others
            out.append(_check(fn.__name__.replace("_checks", ".error"), "note",
                              f"check could not run: {type(e).__name__}: {e}"))
    return out
