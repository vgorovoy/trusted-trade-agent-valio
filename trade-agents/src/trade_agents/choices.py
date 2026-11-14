"""Durable human choice over signed baskets, without creating an order in advance.

Choice documents reuse the store's opaque offer-document persistence; their type is
checked before reading. Resolution is append-only in the existing audit chain. The
same store lock and idempotency key protect retries just like gateway ordering.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import Any

from .buyer_agent.profiles import BuyerProfile
from .offers import assess_delivery
from .policy import SPEND_WINDOW_HOURS, LineProposal, Mandate, evaluate
from .service import Gateway, GatewayError, OrderLineIn, offer_from_dict
from .signing import SignatureError, offer_claims, verify_offer
from .store import now

CHOICE_TYPE = "human-choice-v1"


class ChoiceService:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.store = gateway.store

    @staticmethod
    def _index(buyer_id: str, key: str) -> str:
        return "choice-index:" + hashlib.sha256(f"{buyer_id}\0{key}".encode()).hexdigest()

    def create(self, profile: BuyerProfile, options: list[dict[str, Any]],
               idempotency_key: str) -> dict[str, Any]:
        if profile.mandate.autonomy_mode == "advise":
            raise GatewayError("advice-only mandate cannot create an actionable human choice")
        if not idempotency_key or not 2 <= len(options) <= 3:
            raise GatewayError("a choice needs an idempotency key and two or three baskets")
        index = self._index(profile.mandate.buyer_id, idempotency_key)
        with self.store.lock:
            for event in self.store.audit_log(index):
                if event["event"] in ("choice.created", "choice.indexed"):
                    return self.get(event["data"]["choice_id"])
            eligible = []
            for option in options:
                try:
                    eligible.append(self._validate(profile.mandate, option))
                except (GatewayError, SignatureError, ValueError, KeyError):
                    continue  # never present a blocked/unverified basket as an approval option
            if len(eligible) < 2:
                raise GatewayError("fewer than two currently eligible signed baskets")
            keys = [tuple((ln["offer_id"], ln["quantity"]) for ln in o["lines"])
                    for o in eligible]
            if len(set(keys)) != len(keys):
                raise GatewayError("choice baskets must differ")
            expires = min(now() + timedelta(hours=self.gateway.settings.approval_hold_hours),
                          profile.mandate.valid_until,
                          *(self.store.get_offer(ln["offer_id"])[1]
                            for o in eligible for ln in o["lines"]))
            document = {"type": CHOICE_TYPE, "index": index,
                        "buyer_id": profile.mandate.buyer_id,
                        "agent_id": profile.mandate.agent_id, "options": eligible,
                        "approvers": list(profile.mandate.approvers),
                        "mandate": profile.mandate.model_dump(mode="json"),
                        "passport": profile.passport, "created_at": now().isoformat(),
                        "expires_at": expires.isoformat(), "premium_limit_pct": profile.max_premium_pct,
                        "min_delivery_reliability": profile.mandate.min_delivery_reliability,
                        "explanation": "Choose a basket explicitly, or decline both. "
                                       "Price and delivery reliability require a human trade-off."}
            choice_id = self.store.save_offer("__human_choice__", document, expires)
            # This single committed event both publishes and indexes the choice. A crash
            # before it leaves an inaccessible document, never a second actionable choice.
            self.store.audit(profile.mandate.agent_id, "choice.created", index,
                             "human choice requested; no order submitted",
                             {"buyer_id": document["buyer_id"], "choice_id": choice_id})
            return self.get(choice_id)

    def _document(self, choice_id: str) -> tuple[dict[str, Any], Any]:
        saved = self.store.get_offer(choice_id)
        if not saved or saved[0].get("type") != CHOICE_TYPE:
            raise GatewayError("unknown human choice")
        index = saved[0].get("index")
        if index and not any(e["event"] == "choice.created"
                             and e["data"].get("choice_id") == choice_id
                             for e in self.store.audit_log(index)):
            raise GatewayError("human choice was not published")
        return saved

    def get(self, choice_id: str) -> dict[str, Any]:
        document, expires = self._document(choice_id)
        public = {k: v for k, v in document.items() if k not in ("mandate", "passport", "type", "index")}
        public.update(choice_id=choice_id, status="awaiting_choice", order=None, selection=None)
        for event in self.store.audit_log(choice_id):
            if event["event"] == "choice.resolved":
                public.update(event["data"])
                if public.get("order_id"):
                    public["order"] = self.gateway.order(public["order_id"])
        if public["status"] == "awaiting_choice" and now() > expires:
            public["status"] = "expired"
        return public

    def list(self, buyer_id: str | None = None) -> list[dict[str, Any]]:
        result = []
        for event in self.store.audit_log(limit=1_000_000):
            if event["event"] == "choice.created":
                choice = self.get(event["data"].get("choice_id", event["subject"]))
                if buyer_id is None or choice["buyer_id"] == buyer_id:
                    result.append(choice)
        return result

    def decide(self, choice_id: str, approver: str, selection: int | None,
               note: str = "") -> dict[str, Any]:
        with self.store.lock:
            document, expires = self._document(choice_id)
            mandate = Mandate.model_validate(document["mandate"])
            if not approver or not mandate.approvers or approver not in mandate.approvers:
                raise GatewayError("not an approver for this buyer")
            if selection is not None and (isinstance(selection, bool) or not isinstance(selection, int)
                                          or not 0 <= selection < len(document["options"])):
                raise GatewayError("invalid basket selection")
            result = self.get(choice_id)
            if result["status"] in ("selected", "declined"):
                if selection == result["selection"]:
                    return result
                raise GatewayError("human choice already resolved")
            intents = [e for e in self.store.audit_log(choice_id)
                       if e["event"] == "choice.selection_requested"]
            if intents and selection != intents[0]["data"]["selection"]:
                raise GatewayError("a different selection is already being processed")
            existing = self.store.order_by_key(mandate.buyer_id, f"human-choice:{choice_id}")
            # A crash after committing but before the resolution audit must remain retriable,
            # even after the old offer expires. Never submit a second order in that case.
            if now() > expires and not (intents and existing and existing["status"] == "placed"):
                raise GatewayError("human choice expired; request fresh offers")
            if selection is None:
                self.store.audit(approver, "choice.resolved", choice_id,
                                 "human declined both baskets; no order submitted",
                                 {"status": "declined", "selection": None, "note": note})
                return self.get(choice_id)
            option = document["options"][selection]
            if not (existing and existing["status"] == "placed"):
                self._validate(mandate, option)  # fail closed on stale/unavailable/blocked offers
            if not intents:
                self.store.audit(approver, "choice.selection_requested", choice_id,
                                 "human selected a basket", {"selection": selection, "note": note})
            order = self.gateway.place_order(
                mandate, [OrderLineIn.model_validate(ln) for ln in option["lines"]],
                idempotency_key=f"human-choice:{choice_id}",
                rationale=f"Human {approver} selected basket {selection + 1}. {note}",
                alternatives=[o for i, o in enumerate(document["options"]) if i != selection],
                agent_passport=document["passport"])
            if order["status"] == "pending_approval":
                order = self.gateway.approve(order["order_id"], approver,
                                             f"Explicit basket choice. {note}")
            # A confirm_cart passport/mode remains awaiting the buyer's signed confirmation.
            self.store.audit(approver, "choice.resolved", choice_id,
                             "human choice resolved through gateway policy",
                             {"status": "selected", "selection": selection,
                              "order_id": order["order_id"], "note": note})
            return self.get(choice_id)

    def _validate(self, mandate: Mandate, option: dict[str, Any]) -> dict[str, Any]:
        proposals, canonical, incoming, references = [], [], [], []
        for line in option.get("lines", []):
            quantity = line["quantity"]
            if (isinstance(quantity, bool) or not isinstance(quantity, int | float)
                    or not math.isfinite(quantity)):
                raise GatewayError("invalid basket quantity")
            saved = self.store.get_offer(line["offer_id"])
            if not saved:
                raise GatewayError("unknown basket offer")
            data, expires = saved
            offer = offer_from_dict(data)
            claims = verify_offer(line, self.gateway.jwks())
            expected = offer_claims(offer, line["offer_id"], expires.isoformat(timespec="seconds"))
            if any(claims.get(key) != value for key, value in expected.items()):
                raise GatewayError("basket offer differs from its price lock")
            requested = date.fromisoformat(offer.requested_date)
            try:
                records = self.gateway.client.availability([offer.product_id], requested, force_refresh=True)
            except Exception as exc:
                raise GatewayError("cannot verify current availability; retry later") from exc
            offer.delivery = assess_delivery(records.get(offer.product_id), requested, quantity)
            ref = data.get("price_reference") or {}
            found_for = ref.get("product_id") if ref.get("product_id") != offer.product_id else None
            subs = line.get("substitutes_for") or found_for
            fills = {} if subs else data.get("fills_shortfall_of") or {}
            proposals.append(LineProposal(
                offer, quantity, now() > expires, subs,
                bool(subs and offer.product_id in self.gateway.catalog.substitutes_of(subs)),
                fills_shortfall_of=fills.get("product_id"),
                needs_kitchen_check=self.gateway._related_category(subs, offer.product_id),
                found_for=found_for, substitute_inferred=not line.get("substitutes_for") and bool(subs)))
            incoming.append(OrderLineIn(offer_id=line["offer_id"], quantity=quantity, substitutes_for=subs))
            references.append(ref or fills)
            canonical.append({**line, "product_id": offer.product_id, "name": offer.name,
                              "unit_price_eur": offer.unit_price, "quantity": quantity,
                              "sales_unit": offer.sales_unit,
                              "line_total_eur": round((offer.unit_price or 0) * quantity, 2),
                              "requested_date": offer.requested_date,
                              "delivery_reliability": offer.delivery_reliability,
                              "contains_allergens": offer.contains_allergens,
                              "may_contain_allergens": offer.may_contain_allergens})
        if mandate.max_substitute_price_increase_pct is not None:
            self.gateway._price_references(proposals, incoming, references)
        prior = self.store.autonomous_spend_since(mandate.buyer_id, mandate.mandate_id,
                                                  now() - timedelta(hours=SPEND_WINDOW_HOURS))
        decision = evaluate(mandate, proposals, now=now(),
                            signature_ok=self.gateway._mandate_signature_ok(mandate),
                            prior_spend_eur=prior)
        if decision.outcome == "rejected":
            raise GatewayError("basket blocked: " + "; ".join(decision.reasons))
        rel = [p.offer.delivery_reliability for p in proposals]
        return {"lines": canonical, "total_eur": decision.total_eur,
                "delivery_reliability": min(rel) if rel and all(x is not None for x in rel) else None,
                "approval_reasons": decision.reasons,
                "trade_off": option.get("trade_off", ""), "kind": option.get("kind", "option")}
