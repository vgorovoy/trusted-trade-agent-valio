"""Review-only menu extraction. Product ordering remains an explicit separate action."""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_agents.buyer_agent.profiles import PROFILES, get_profile
from trade_agents.llm import get_llm

router = APIRouter()
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class MenuDraftRequest(BaseModel):
    text: str = Field(default="", max_length=12_000)
    guests: int = Field(ge=1, le=10_000, strict=True)
    buyer: str = "school"
    image_base64: str | None = Field(default=None, max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3))
    mime: str | None = None


def _image(body: MenuDraftRequest) -> bytes | None:
    if body.image_base64 is None:
        if body.mime is not None:
            raise HTTPException(400, "Attach an image when specifying its type")
        return None
    if body.mime not in ("image/jpeg", "image/png"):
        raise HTTPException(400, "Use a JPEG or PNG image, at most 5 MB")
    try:
        data = base64.b64decode(body.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "Image must be valid base64") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "Use a JPEG or PNG image, at most 5 MB")
    signature = b"\xff\xd8\xff" if body.mime == "image/jpeg" else b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise HTTPException(400, "Image content does not match its JPEG/PNG type")
    return data


@router.post("/ui/menu/draft")
async def menu_draft(body: MenuDraftRequest) -> dict:
    from trade_agents.buyer_agent.menu_draft import MenuDraftError, draft_menu

    if body.buyer not in PROFILES:
        raise HTTPException(400, "Unknown kitchen profile")
    image = _image(body)
    if not body.text.strip() and image is None:
        raise HTTPException(400, "Describe a dish/menu or attach its image")
    try:
        llm = get_llm()
        if image is not None and not getattr(llm, "vision", False):
            raise HTTPException(400, "This model cannot read images. Use a vision-capable provider "
                                "or enter the recipe as text; offline mode does not support image drafts.")
        profile = get_profile(body.buyer)
        return await run_in_threadpool(draft_menu, llm, body.text.strip(), body.guests,
                                       profile, image, body.mime)
    except HTTPException:
        raise
    except MenuDraftError as exc:
        raise HTTPException(400, "Could not produce a valid menu draft. Add a clearer recipe, "
                            "portion count and any dietary requirements, then try again.") from exc
    except Exception as exc:
        # SDK errors may include request details or credentials. Never forward their text.
        raise HTTPException(502, "Menu model is unavailable. No order was created; "
                            "check provider configuration or try later.") from exc


_EXAMPLES = Path(__file__).resolve().parents[1] / "bench" / "menu"


@router.get("/ui/menu/handwritten-example")
def handwritten_example() -> dict:
    case = json.loads((_EXAMPLES / "handwritten-v1/01-kitchen-note.json").read_text())
    return {"text": case["text"], "guests": 1, "buyer": "restaurant",
            "image_url": "/ui/menu/handwritten-example/image",
            "image_name": "synthetic-kitchen-note.png", "synthetic": True}


@router.get("/ui/menu/handwritten-example/image")
def handwritten_example_image() -> FileResponse:
    return FileResponse(_EXAMPLES / "handwritten-v1/kitchen-note.png", media_type="image/png")


@router.get("/ui/menu/example")
def recipe_example() -> dict:
    folder = _EXAMPLES / "techcards-v1"
    case = json.loads((folder / "01-three-techcards.json").read_text())
    documents = [{"kind": kind, "name": filename,
                  "content": (folder / "inputs" / filename).read_text()}
                 for kind, filename in (("menu", "menu.json"),
                                        ("recipe", "recipe-cards.json"),
                                        ("stock", "inventory.json"))]
    return {"text": case["text"].split(" INPUTS:", 1)[0], "guests": 1,
            "buyer": "restaurant", "documents": documents, "synthetic": True}


# Combining reviewed needs is deterministic: this route never invokes a model.

class BasketNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ingredient: str = Field(min_length=1, max_length=200)
    search_hint_fi: str = Field(default="", max_length=100)
    amount: float = Field(gt=0, allow_inf_nan=False, strict=True)
    unit: Literal["KG", "L", "ST"]
    date: str = Field(min_length=10, max_length=10)
    constraints: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        default_factory=list, max_length=12)

    @field_validator("ingredient")
    @classmethod
    def nonempty_ingredient(cls, value):
        if not value.strip():
            raise ValueError("Ingredient must not be blank")
        return value.strip()

    @field_validator("date")
    @classmethod
    def iso_date(cls, value):
        from datetime import date
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("Use an ISO calendar date")
        return value


class MenuBasketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    buyer: str = "restaurant"
    reviewed: bool = Field(strict=True)
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    needs: list[BasketNeed] = Field(min_length=1, max_length=20)


def _basket_filters(constraints: list[str], mandate):
    """Only explicitly supported restrictions are interpreted; never drop unknown ones."""
    import re

    claims, excluded = set(mandate.required_claims), set(mandate.excluded_allergens)
    traces = mandate.exclude_may_contain
    supported = {
        "milk-free": (None, "milk"), "dairy-free": (None, "milk"),
        "no dairy ingredients": (None, "milk"), "strictly no dairy ingredients": (None, "milk"),
        "no milk": (None, "milk"), "maidoton": (None, "milk"),
        "lactose-free": ("FREE_FROM_LACTOSE", None), "laktoositon": ("FREE_FROM_LACTOSE", None),
        "gluten-free": ("FREE_FROM_GLUTEN", None), "gluteeniton": ("FREE_FROM_GLUTEN", None),
        "vegan": ("VEGAN", None), "vegaaninen": ("VEGAN", None),
        "egg-free": (None, "eggs"), "no eggs": (None, "eggs"),
        "soy-free": (None, "soy"), "no soy": (None, "soy"),
    }
    unknown = []
    for item in constraints:
        for part in re.split(r"[,;]", item):
            normalized = " ".join(part.lower().replace("–", "-").split()).strip()
            if not normalized:
                continue
            if normalized in {"exclude may contain", "exclude_may_contain", "no allergen traces"}:
                traces = True
                continue
            restriction = supported.get(normalized)
            if restriction is None:
                unknown.append(part.strip())
            else:
                claim, allergen = restriction
                if claim:
                    claims.add(claim)
                if allergen:
                    excluded.add(allergen)
    return sorted(claims), sorted(excluded), traces, unknown



def _basket_policy_feedback(order: dict, needs: list[BasketNeed]) -> dict:
    if order["status"] != "rejected":
        return {}
    reasons = "; ".join(order["data"].get("decision", {}).get("reasons", []))
    return {"complete": False,
            "explanation": "The whole basket was rejected; no purchase was placed. " + reasons,
            "line_errors": [{"index": index, "ingredient": need.ingredient,
                             "code": "whole_basket_rejected", "message": reasons or "Review basket policy"}
                            for index, need in enumerate(needs)]}

def _combined_basket(body: MenuBasketRequest) -> dict:
    import hashlib
    import math
    from datetime import date, datetime

    from trade_agents.api import gateway
    from trade_agents.buyer_agent.profiles import sign_mandate
    from trade_agents.clock import current_time
    from trade_agents.config import TZ
    from trade_agents.service import GatewayError, OrderLineIn

    if body.buyer not in PROFILES:
        raise HTTPException(400, "Unknown kitchen profile")
    if not body.reviewed:
        raise HTTPException(400, "Review the complete ingredient draft first")
    profile = get_profile(body.buyer)
    # Server-owned canonical restrictions remain; the client supplies no mandate.
    mandate = sign_mandate(profile.mandate.model_copy(update={"autonomy_mode": "confirm_cart",
                                                            "signature": None}))
    gw = gateway()
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json", exclude={"idempotency_key"}),
                                            sort_keys=True).encode()).hexdigest()
    rationale = "Reviewed complete menu basket; request-sha256:" + fingerprint
    key = "menu-basket-" + body.idempotency_key
    existing = gw.store.order_by_key(mandate.buyer_id, key)
    if existing:
        if existing["data"].get("rationale") != rationale:
            raise HTTPException(409, "This request key belongs to a different draft; review and retry anew")
        return {"status": existing["status"], "complete": True, "order": existing,
                "lines": [{**row["offer"], "quantity": row["quantity"],
                           "unit": row["offer"]["sales_unit"]} for row in existing["data"]["lines"]],
                "requirements": [need.model_dump() for need in body.needs], "line_errors": [],
                "requires_cart_confirmation": existing["status"] == "awaiting_cart_confirmation",
                "no_partial_orders": True, "idempotent_replay": True,
                **_basket_policy_feedback(existing, body.needs)}
    errors, plans = [], []
    today = current_time(datetime.now(TZ)).date()

    def issue(index, code, message):
        errors.append({"index": index, "ingredient": body.needs[index].ingredient,
                       "code": code, "message": message})

    dates = {need.date for need in body.needs}
    if len(dates) > 1:
        for index in range(len(body.needs)):
            issue(index, "multiple_delivery_dates", "One basket needs one delivery date. Choose a common "
                  "date explicitly or review separate delivery batches; no partial baskets were created.")
    for index, need in enumerate(body.needs):
        if date.fromisoformat(need.date) < today:
            issue(index, "past_delivery_date", "Choose a delivery date today or later")
        _, _, _, unknown = _basket_filters(need.constraints, mandate)
        if unknown:
            issue(index, "unsupported_requirement", "Clarify unsupported requirements: " + "; ".join(unknown))
    if errors:
        return {"status": "needs_review", "complete": False, "order": None, "line_errors": errors,
                "planned_lines": [], "no_partial_orders": True}
    # Consolidate identical reviewed requirements before rounding to supplier packs.
    groups = {}
    for index, need in enumerate(body.needs):
        group_key = (need.ingredient.casefold(), need.search_hint_fi.casefold(), need.unit, need.date,
                     tuple(sorted(need.constraints)))
        group = groups.setdefault(group_key, {"need": need, "amount": 0.0, "indices": []})
        group["amount"] += need.amount
        group["indices"].append(index)
    for group in groups.values():
        need, amount = group["need"], group["amount"]
        claims, excluded, traces, _ = _basket_filters(need.constraints, mandate)
        if not math.isfinite(amount):
            for index in group["indices"]:
                issue(index, "invalid_total", "Combined quantity must remain finite")
            continue
        try:
            query = need.search_hint_fi.strip() or need.ingredient
            candidates = gw.search(query, require_claims=claims, exclude_allergens=excluded,
                                   exclude_may_contain=traces, limit=3)
            if not candidates:
                for index in group["indices"]:
                    issue(index, "no_safe_candidate", "No catalogue match satisfies these restrictions. "
                          "Clarify the product or choose a safe match before requesting the whole basket.")
                continue
            plan = gw.plan(query, amount, need.unit, date.fromisoformat(need.date),
                           product_ids=[row["product_id"] for row in candidates],
                           require_claims=claims, exclude_allergens=excluded,
                           exclude_may_contain=traces, agent_id=mandate.agent_id,
                           optimize=profile.optimize, max_premium_pct=profile.max_premium_pct)
            options = [option for option in plan.get("options", []) if len(option.get("lines", [])) == 1
                       and option.get("unit") == need.unit and option.get("covers", 0) + 0.001 >= amount]
            if not options:
                for index in group["indices"]:
                    issue(index, "unresolved_supply", "No complete single-product offer covers this need "
                          "on the selected date. Check availability, cutoff, pack/unit and restrictions.")
                continue
            selected = options[0]
            plans.append({"indices": group["indices"], "ingredient": need.ingredient,
                          "requested": amount, "requested_unit": need.unit, "date": need.date,
                          "covers": selected["covers"], "line": selected["lines"][0]})
        except Exception:
            # Includes provider diagnostics: keep sensitive request details server-side.
            for index in group["indices"]:
                issue(index, "planning_unavailable", "Price/availability could not be verified. Retry later; "
                      "no part of this menu was ordered.")
    if errors:
        return {"status": "needs_review", "complete": False, "order": None, "line_errors": errors,
                "planned_lines": plans, "no_partial_orders": True}
    # Merge quantities for a shared SKU so final fresh-stock policy evaluates its total,
    # not several independent rows each appearing to fit the same availability.
    combined = {}
    for plan in plans:
        line = plan["line"]
        sku = (line["product_id"], plan["date"])
        row = combined.setdefault(sku, {**line, "quantity": 0.0, "unit": line["sales_unit"],
                                       "requested_date": plan["date"], "requirements": []})
        row["quantity"] += line["quantity"]
        row["requirements"].append({key: value for key, value in plan.items() if key != "line"})
    lines = list(combined.values())
    if any(not math.isfinite(row["quantity"]) for row in lines):
        return {"status": "needs_review", "complete": False, "order": None,
                "line_errors": [{"index": 0, "ingredient": body.needs[0].ingredient,
                                 "code": "invalid_total", "message": "Combined sales quantity is too large"}],
                "planned_lines": plans, "no_partial_orders": True}
    for row in lines:
        saved = gw.store.get_offer(row["offer_id"])
        available = (saved[0].get("delivery") or {}).get("available_amount") if saved else None
        if available is not None and row["quantity"] > available + 0.001:
            for requirement in row["requirements"]:
                for index in requirement["indices"]:
                    issue(index, "aggregate_stock_shortfall", "The combined demand for this SKU exceeds "
                          "available stock. Review affected ingredients; no partial basket created.")
    if errors:
        return {"status": "needs_review", "complete": False, "order": None, "line_errors": errors,
                "planned_lines": plans, "no_partial_orders": True}
    warnings = ["Review product relevance, pack surplus and recipe suitability in the complete cart."]
    for plan in plans:
        surplus = plan["covers"] - plan["requested"]
        if surplus > 0.001:
            warnings.append(f"{plan['ingredient']}: requested {plan['requested']:g} "
                            f"{plan['requested_unit']}; "
                            f"supplier pack supplies {plan['covers']:g}; surplus {surplus:g} "
                            f"{plan['requested_unit']}. Confirm this explicitly in the final cart.")
    try:
        order = gw.place_order(mandate, [OrderLineIn(offer_id=row["offer_id"], quantity=row["quantity"])
                                         for row in lines], key, rationale,
                               agent_passport=profile.passport)
    except GatewayError:
        return {"status": "needs_review", "complete": False, "order": None,
                "line_errors": [{"index": index, "ingredient": need.ingredient,
                                 "code": "fresh_validation_failed",
                                 "message": "Fresh validation failed; retry the complete basket."}
                                for index, need in enumerate(body.needs)],
                "planned_lines": plans, "no_partial_orders": True}
    if order["data"].get("rationale") != rationale:
        raise HTTPException(409, "Concurrent request key belongs to another draft; review and retry anew")
    if {row["offer_id"] for row in order["data"]["lines"]} != {row["offer_id"] for row in lines}:
        # Another identical request won the idempotency race. Return its actual cart,
        # never overwrite its evidence with newly generated offer IDs.
        return {"status": order["status"], "complete": True, "order": order,
                "lines": [{**row["offer"], "quantity": row["quantity"],
                           "unit": row["offer"]["sales_unit"]} for row in order["data"]["lines"]],
                "requirements": [need.model_dump() for need in body.needs], "line_errors": [],
                "requires_cart_confirmation": order["status"] == "awaiting_cart_confirmation",
                "no_partial_orders": True, "idempotent_replay": True,
                **_basket_policy_feedback(order, body.needs)}
    evidence = {"mandate": mandate.model_dump(mode="json"),
                "offers": {row["offer_id"]: row for row in lines},
                "menu_requirements": [need.model_dump() for need in body.needs],
                "menu_plans": plans, "warnings": warnings}
    path = gw.settings.state_dir / "web_evidence" / f"{order['order_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False))
    gw.store.audit(mandate.agent_id, "menu.basket_proposed", order["order_id"],
                   "One complete reviewed menu basket; no per-ingredient orders",
                   {"requirements": evidence["menu_requirements"], "warnings": warnings})
    return {"status": order["status"], "complete": True, "order": order, "lines": lines,
            "requirements": evidence["menu_requirements"], "line_errors": [], "warnings": warnings,
            "evidence": evidence,
            "requires_cart_confirmation": order["status"] == "awaiting_cart_confirmation",
            "no_partial_orders": True, **_basket_policy_feedback(order, body.needs)}


@router.post("/ui/menu/basket")
async def menu_basket(body: MenuBasketRequest) -> dict:
    return await run_in_threadpool(_combined_basket, body)
