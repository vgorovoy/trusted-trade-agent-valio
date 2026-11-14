"""Valio's own review agent: advisory only, for Valio's account manager.

It never places, approves or confirms anything; humans and the deterministic policy
engine decide. For each order event it runs deterministic checks (`checks.py`), asks an
open-weight model on Verda (EU) to explain them in a few sentences, and records the
review in the store's `valio_reviews` table (outside `order.data`, so it never races with
approvals, cart confirmations or receipts) plus an audit entry `valio.reviewed`.

Settings (environment):
  TTA_VALIO_REVIEW         off | sync | async (default async: the REST server / UI never
                           waits; `tta agent` and the demo script use sync; bench and
                           tests force off)
  TTA_VALIO_LLM_PROVIDER   verda (default) | gemini | openai | nvidia | offline
  TTA_VALIO_LLM_MODEL      default kimi-k3-1-pro for verda
  TTA_VALIO_LLM_TIMEOUT    seconds per model call (default 60)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any

from ..store import now
from .checks import run_checks

log = logging.getLogger(__name__)

ACTOR = "valio-agent"
RISKS = ("low", "medium", "high")
DEFAULT_PROVIDER, DEFAULT_MODEL = "verda", "kimi-k3-1-pro"

SYSTEM = (
    "You are Valio Aimo's internal order-review assistant. You write for Valio's account "
    "manager, never for the buyer. You do NOT place, approve or reject orders; humans and "
    "deterministic rules do. Use ONLY the facts in the JSON you are given (order and check "
    "results); never invent products, quantities, prices, dates or reasons. If something is "
    "not in the input, do not mention it. Reply with one JSON object only: "
    '{"summary": "<2-4 short factual sentences: what was ordered and why it is fine or what '
    'to watch>", "risk": "low|medium|high", "flags": ["<short, each grounded in a check>"], '
    '"suggested_action": "<one short advisory sentence for the account manager>"}'
)


def mode() -> str:
    m = os.getenv("TTA_VALIO_REVIEW", "async").strip().lower()
    return m if m in ("off", "sync", "async") else "async"


def _provider() -> tuple[str, str | None]:
    p = (os.getenv("TTA_VALIO_LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    model = os.getenv("TTA_VALIO_LLM_MODEL") or (DEFAULT_MODEL if p == "verda" else None)
    return p, model


def deterministic_risk(checks: list[dict]) -> str:
    flags = sum(c["status"] == "flag" for c in checks)
    notes = sum(c["status"] == "note" for c in checks)
    return "high" if flags >= 2 else "medium" if flags or notes >= 3 else "low"


def _order_facts(order: dict[str, Any]) -> dict[str, Any]:
    """The part of the order the model may talk about; small to keep the prompt cheap."""
    d = order["data"]
    m = d.get("mandate") or {}
    return {
        "order_id": order["order_id"], "status": order["status"],
        "buyer": m.get("buyer_name") or order["buyer_id"], "agent_id": order["agent_id"],
        "total_eur_excl_vat": order["total_eur"], "order_number": order.get("order_number"),
        "lines": [{"product": s["offer"].get("name"), "product_id": s["offer"]["product_id"],
                   "quantity": s["quantity"], "sales_unit": s["offer"].get("sales_unit"),
                   "unit_price_eur": s["offer"].get("unit_price"),
                   "requested_date": s["offer"].get("requested_date"),
                   "substitutes_for": s.get("substitutes_for")}
                  for s in d.get("lines", [])],
        "agent_rationale": (d.get("rationale") or "")[:400],
    }


def _compact_checks(checks: list[dict]) -> list[dict]:
    return [{"check": c["check"], "status": c["status"], "evidence": c["evidence"]}
            for c in checks]


def template_explanation(order: dict[str, Any], checks: list[dict]) -> dict[str, Any]:
    """Offline fallback: the same shape as the model's answer, built from the checks."""
    facts = _order_facts(order)
    lines = ", ".join(f"{ln['quantity']:g} {ln['sales_unit']} {ln['product']}"
                      for ln in facts["lines"]) or "no lines"
    flags = [c["evidence"] for c in checks if c["status"] == "flag"]
    notes = [c["evidence"] for c in checks if c["status"] == "note"]
    risk = deterministic_risk(checks)
    summary = (f"{facts['buyer']} ordered {lines} for {facts['total_eur_excl_vat']:.2f} EUR "
               f"excl. VAT; status {facts['status']}. ")
    summary += (f"{len(flags)} flag(s) and {len(notes)} note(s) from the checks."
                if flags or notes else "All checks passed.")
    action = ("Contact the buyer before delivery about: " + flags[0] if flags else
              "Review the notes; no action needed otherwise." if notes else "No action needed.")
    return {"summary": summary, "risk": risk, "flags": flags + notes[:3],
            "suggested_action": action}


def _parse(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(m.group(0))
    out = {"summary": str(obj.get("summary") or "").strip(),
           "risk": str(obj.get("risk") or "").strip().lower(),
           "flags": [str(f) for f in (obj.get("flags") or []) if str(f).strip()][:8],
           "suggested_action": str(obj.get("suggested_action") or "").strip()}
    if not out["summary"] or out["risk"] not in RISKS:
        raise ValueError("reply misses summary or a valid risk")
    return out


def explain(order: dict[str, Any], checks: list[dict], llm: Any = None) -> dict[str, Any]:
    """{summary, risk, flags, suggested_action, llm: {...}}; never raises."""
    provider, model = _provider()
    if llm is None and provider == "offline":
        return {**template_explanation(order, checks), "llm": {"status": "offline"}}
    try:
        if llm is None:
            from ..llm import get_llm

            llm = get_llm(provider, model)
            client = getattr(llm, "client", None)
            if client is not None and hasattr(client, "with_options"):
                timeout = float(os.getenv("TTA_VALIO_LLM_TIMEOUT", "60"))
                llm.client = client.with_options(timeout=timeout, max_retries=1)
        prompt = json.dumps({"order": _order_facts(order), "checks": _compact_checks(checks),
                             "deterministic_risk": deterministic_risk(checks)},
                            ensure_ascii=False, separators=(",", ":"), default=str)
        resp = llm.chat([{"role": "user", "content": prompt}], system=SYSTEM, json_output=True)
        out = _parse(resp.text)
        # The model may lower its tone, never the facts: risk is at least the rules' risk.
        floor = deterministic_risk(checks)
        if RISKS.index(out["risk"]) < RISKS.index(floor):
            out["risk"] = floor
        return {**out, "llm": {"status": "ok", "model": getattr(llm, "name", provider),
                               "input_tokens": resp.usage.input_tokens,
                               "output_tokens": resp.usage.output_tokens}}
    except Exception as e:  # provider down, bad JSON, no key: keep the deterministic review
        log.warning("valio-agent explanation unavailable: %s", type(e).__name__)
        return {**template_explanation(order, checks),
                "llm": {"status": "unavailable", "error": f"{type(e).__name__}: {str(e)[:160]}"}}


def suggestion(gw: Any, order: dict[str, Any]) -> dict[str, Any] | None:
    """Proactive selling, at most one: an official substitute that keeps the mandate's
    rules and has a lower CO2 intensity or better delivery reliability. Catalogue only
    (no API call); a suggestion for the account manager, never ordered automatically."""
    from ..catalog import AllergenFilter

    m = order["data"].get("mandate") or {}
    banned = AllergenFilter.parse(m.get("excluded_allergens", []))
    required = set(m.get("required_claims", []))
    for s in order["data"].get("lines", []):
        src = gw.catalog.get(s["offer"]["product_id"])
        if not src:
            continue
        for pid in gw.catalog.substitutes_of(src.id):
            c = gw.catalog.get(pid)
            if (not c or not required <= set(c.claims)
                    or banned.reason(c.contains, c.may_contain, c.claims,
                                     bool(m.get("exclude_may_contain")))):
                continue
            greener = c.co2_per_kg is not None and src.co2_per_kg is not None \
                and c.co2_per_kg < src.co2_per_kg
            reliable = c.reliability is not None and src.reliability is not None \
                and c.reliability > src.reliability
            if greener or reliable:
                why = ("lower CO2 intensity" if greener else "better delivery reliability")
                return {"kind": "suggestion", "auto_order": False, "product_id": c.id,
                        "name": c.name, "instead_of": src.id,
                        "why": f"official substitute with {why}; keeps the mandate's "
                               "allergen and claim rules. Price and availability not checked."}
    return None


def review_order(gw: Any, order_id: str, *, llm: Any = None, use_llm: bool = True,
                 trigger: str = "manual") -> dict[str, Any]:
    """Review one order, store it in order.data.valio_review and audit it. Returns it."""
    order = gw.store.get_order(order_id)
    if not order:
        raise ValueError(f"unknown order {order_id}")
    checks = run_checks(gw, order)
    review: dict[str, Any] = {
        "reviewed_at": now().isoformat(timespec="seconds"), "trigger": trigger,
        "order_status": order["status"], "advisory_only": True,
        "checks": checks,
        "counts": {s: sum(c["status"] == s for c in checks) for s in ("ok", "note", "flag")},
    }
    if use_llm:
        review.update(explain(order, checks, llm))
    else:
        review.update({**template_explanation(order, checks), "llm": {"status": "skipped"}})
    try:
        review["suggestion"] = suggestion(gw, order)
    except Exception:  # optional extra
        review["suggestion"] = None
    # Own table, not order.data: no read-modify-write race with the buyer flow, no lock.
    gw.store.save_valio_review(order_id, review)
    gw.store.audit(ACTOR, "valio.reviewed", order_id,
                   f"Valio review ({review['risk']} risk, {review['counts']['flag']} flag(s)): "
                   f"{review['summary'][:200]}",
                   {"risk": review["risk"], "counts": review["counts"],
                    "llm": review["llm"].get("status"), "trigger": trigger})
    return review


def hook(gw: Any, order: dict[str, Any], trigger: str) -> None:
    """Called by the gateway after an order event. Never raises, never blocks in async."""
    m = mode()
    if m == "off" or not order:
        return

    def run() -> None:
        try:
            review_order(gw, order["order_id"], trigger=trigger)
        except Exception:
            log.exception("valio-agent review failed for %s", order.get("order_id"))

    if m == "async":
        threading.Thread(target=run, name="valio-review", daemon=True).start()
    else:
        run()


def list_reviews(gw: Any, status: str | None = None, risk: str | None = None) -> list[dict]:
    """Valio's view: orders with their reviews, newest first."""
    out = []
    for o in gw.store.list_orders(status):
        rv = gw.store.get_valio_review(o["order_id"])
        if risk and (not rv or rv.get("risk") != risk):
            continue
        out.append({"order_id": o["order_id"], "status": o["status"], "buyer_id": o["buyer_id"],
                    "agent_id": o["agent_id"], "total_eur": o["total_eur"],
                    "order_number": o["order_number"], "created_at": o["created_at"],
                    "valio_review": rv})
    return out
