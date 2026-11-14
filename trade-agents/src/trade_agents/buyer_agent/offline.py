"""Rule-based stand-in for the LLM (`TTA_LLM_PROVIDER=offline`).

Speaks the same tool-calling protocol as a real model, so the UI, the bench and demos
run without any API key. Its judgement is keyword rules (English / Finnish requests,
one product per request); the real agent is an LLM.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from ..llm import LLMResponse, ToolCall, ToolSpec, last_tool_result

PRODUCTS = [  # (request words, Finnish search query)
    (r"oat drink|oat milk|kaurajuoma", "kaurajuoma"),
    (r"coffee milk|kahvimaito", "kahvimaito"),
    (r"sour cream|smetana|ranskankerma", "smetana"),
    (r"whipping cream|cream|kerma", "kerma"),
    (r"butter|voi\b", "voi"),
    (r"cheese|juusto", "juusto"),
    (r"yogh?urt|jogurtti", "jogurtti"),
    (r"quark|rahka", "rahka"),
    (r"eggs?|kananmun", "kananmuna"),
    (r"minced beef|beef mince|ground beef|jauheliha", "jauheliha"),
    (r"milk|maito", "maito"),
]
NOT_PLAIN = re.compile(r"kahvi|caf[eé]|barista|kaakao|cocoa|annos|portion|rahka|jauhe(?!liha)|powder|"
                       r"jäätelö|ice cream|suklaa|chocolate|proteiini|protein|mansikka|vanilja",
                       re.I)
UNITS = {"l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L", "kg": "KG",
         "kilo": "KG", "kilos": "KG", "kilograms": "KG", "pcs": "ST", "pieces": "ST",
         "kpl": "ST"}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FI_DAYS = ["maanantai", "tiistai", "keskiviikko", "torstai", "perjantai", "lauantai", "sunnuntai"]


def parse_request(text: str) -> dict[str, Any]:
    """Pull the need out of the first user message (profile context + request)."""
    req = text.split("Request from the kitchen:", 1)[-1]
    low = req.lower()
    need: dict[str, Any] = {"constraints": []}
    for pat, query in PRODUCTS:
        if re.search(pat, low):
            need["product"], need["query"] = re.search(pat, low).group(0), query
            break
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(UNITS, key=len, reverse=True))
                  + r")\b", low)
    if m:
        need["amount"] = float(m.group(1).replace(",", "."))
        need["unit"] = UNITS[m.group(2)]
    today = re.search(r"Today: (\d{4}-\d{2}-\d{2})", text)
    default = re.search(r"Default delivery date[^:]*: (\d{4}-\d{2}-\d{2})", text)
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", req)
    day = next((i for i, d in enumerate(DAYS) if d in low), None)
    if day is None:
        day = next((i for i, d in enumerate(FI_DAYS) if d in low), None)
    if iso:
        need["delivery_date"] = iso.group(1)
    elif day is not None and today:
        t = date.fromisoformat(today.group(1))
        need["delivery_date"] = (t + timedelta(days=(day - t.weekday()) % 7 or 7)).isoformat()
    elif default:
        need["delivery_date"] = default.group(1)
    pid = re.search(r"\bproduct\s+(\d+)\b", low)
    if pid:  # the kitchen named the exact product: plan only it, substitute if it fails
        need["product_id"] = pid.group(1)
    if re.search(r"lactose[- ]free|laktoositon", low):
        need["constraints"].append("FREE_FROM_LACTOSE")
    claims = re.search(r"Required product claims: ([^.]+)\.", text)
    need["require_claims"] = sorted(set(need["constraints"]) |
                                    set(claims.group(1).split(", ") if claims else []))
    allergens = re.search(r"Excluded allergens \(hard rule\): ([^.]+)\.", text)
    need["exclude_allergens"] = allergens.group(1).split(", ") if allergens else []
    return need


class OfflineBuyerLLM:
    name = "offline:rules"
    vision = False

    def chat(self, messages, tools: Sequence[ToolSpec] = (), *, system="",
             json_output=False) -> LLMResponse:
        need = parse_request(messages[0]["content"])
        last = next((m for m in reversed(messages) if m["role"] == "tool"), None)
        call = lambda name, **a: LLMResponse(tool_calls=[ToolCall(name, a)])  # noqa: E731

        if last is None:
            if not need.get("query") or not need.get("amount"):
                missing = [k for k in ("product", "amount") if not need.get(k)]
                return call("finish", status="needs_clarification", need=need,
                            explanation_for_chef="I could not tell the "
                            + " and ".join(missing) + " from the request.")
            return call("search_products", query=need["query"],
                        require_claims=need["require_claims"],
                        exclude_allergens=need["exclude_allergens"], limit=40)

        res = last_tool_result(messages)
        if last["name"] == "search_products":
            flagged = set()
            warn = res.get("untrusted_text_warning", "")
            for p in res.get("products", []):
                if p["product_id"] in warn:
                    flagged.add(p["product_id"])
            self.dropped = [
                {"name": p["name"], "reason": "contains instruction-like text"
                 if p["product_id"] in flagged else "not the plain product asked for"}
                for p in res.get("products", [])
                if p["product_id"] in flagged
                or (need["query"] != "kahvimaito" and NOT_PLAIN.search(p["name"]))]
            dropped = {d["name"] for d in self.dropped}
            keep = [p["product_id"] for p in res.get("products", []) if p["name"] not in dropped]
            if need.get("product_id"):
                keep = [need["product_id"]]
            if not keep:
                return call("finish", status="no_order", need=need, dropped_candidates=self.dropped,
                            explanation_for_chef=f"Found no suitable {need['product']} in the "
                                                 "catalogue under this mandate.")
            return call("plan_need", query=need["query"], amount=need["amount"],
                        unit=need["unit"], requested_date=need["delivery_date"],
                        product_ids=keep)

        if last["name"] == "plan_need":
            if not res.get("options") and need.get("product_id"):
                # The named product cannot be ordered: ask the gateway for a substitute.
                return call("find_substitutes", product_id=need["product_id"])
            if not res.get("options"):
                return call("finish", status="no_order", need=need,
                            dropped_candidates=getattr(self, "dropped", []),
                            explanation_for_chef=res.get("explanation", "No option found."))
            best = res["options"][0]
            self.plan = res
            self.chosen = best["lines"]
            return call("place_order",
                        lines=[{"offer_id": ln["offer_id"], "quantity": ln["quantity"]}
                               for ln in best["lines"]],
                        rationale=f"cheapest option covering {need['amount']:g} {need['unit']} "
                                  f"on {need['delivery_date']}")

        if last["name"] == "find_substitutes":
            opts = res.get("options", [])
            if not opts:
                return call("finish", status="no_order", need=need,
                            explanation_for_chef=f"Product {need['product_id']} cannot be "
                            "ordered and there is no allowed substitute: "
                            + str(res.get("reason") or "none found") + ".")
            self.substitute = opts[0]    # official first, then the most similar
            return call("get_offers", product_ids=[opts[0]["product_id"]],
                        requested_date=need["delivery_date"], amount=need["amount"],
                        unit=need["unit"])

        if last["name"] == "get_offers":
            offer = (res.get("offers") or [{}])[0]
            sub = getattr(self, "substitute", {})
            if not offer.get("quantity_needed") or offer.get("delivery") not in (
                    "available", "late"):
                return call("finish", status="no_order", need=need,
                            explanation_for_chef=f"The substitute {sub.get('name')} cannot "
                            f"cover the need ({offer.get('delivery')}).")
            self.chosen = [{**offer, "quantity": offer["quantity_needed"]}]
            kind = ("Valio-listed" if sub.get("official") else
                    "related category, check with the kitchen" if sub.get("needs_kitchen_check")
                    else "found by the agent, not on Valio's list")
            return call("place_order",
                        lines=[{"offer_id": offer["offer_id"],
                                "quantity": offer["quantity_needed"],
                                "substitutes_for": need["product_id"]}],
                        rationale=f"product {need['product_id']} cannot be ordered; substitute "
                                  f"{offer['product_id']} ({kind})")

        if last["name"] == "place_order":
            opts = getattr(self, "plan", {}).get("options", [])
            chosen = getattr(self, "chosen", [])
            best = {"lines": chosen, "total_eur": res.get("total_eur"),
                    "product_ids": [ln["product_id"] for ln in chosen]}
            names = ", ".join(f"{ln['quantity']:g} {ln['sales_unit']} {ln['name']}"
                              for ln in chosen)
            status = res.get("status", "no_order")
            text = {"placed": f"Ordered {names} for {res.get('total_eur', 0):.2f} EUR.",
                    "pending_approval": f"Prepared {names} for {res.get('total_eur', 0):.2f} EUR; "
                                        "it needs your approval: " + "; ".join(res.get("reasons", [])),
                    "awaiting_cart_confirmation": f"Filled the cart with {names} for "
                                                  f"{res.get('total_eur', 0):.2f} EUR; please "
                                                  "confirm it.",
                    "proposal": f"Recommend {names} for {res.get('total_eur', 0):.2f} EUR; "
                                "nothing was ordered.",
                    "rejected": "The gateway refused the order: " + "; ".join(res.get("reasons", []))
                    }.get(status, str(res.get("error", "No order.")))
            if len(opts) > 1:
                text += f" {len(opts) - 1} alternative(s) considered: " + "; ".join(
                    f"{o['total_eur']:.2f} EUR ({o.get('rank')})" for o in opts[1:]) + "."
            return call("finish", status=status, need=need,
                        chosen={"summary": names, "total_eur": best.get("total_eur"),
                                "product_ids": [ln["product_id"] for ln in best.get("lines", [])]},
                        alternatives=[{"summary": ", ".join(ln["name"] for ln in o["lines"]),
                                       "total_eur": o["total_eur"],
                                       "why_not": o.get("trade_off") or "more expensive"}
                                      for o in opts[1:]],
                        dropped_candidates=getattr(self, "dropped", []),
                        explanation_for_chef=text)

        return call("finish", status="no_order", need=need,
                    explanation_for_chef="Stopped after an unexpected tool result.")
