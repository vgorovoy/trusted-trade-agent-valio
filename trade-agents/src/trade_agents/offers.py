"""Turn raw price + availability answers into normalised, explainable offers.

Interpretation confirmed by Valio Aimo (Tommi, Discord, 14.11.2026).
Every rule below is also returned to the agent as an explicit `assumption`:

- `availableAmount` >= 999 999 means stock is unknown (typically terminal products).
  Valio mock logic permits any quantity; it is not verified unlimited stock.
- A schedule line with amount 0 and no `availableDate` means out of stock.
- An absent availability record or empty schedule lines means not available.
- `availableDate` is the earliest delivery date the API offers; it does not move when
  `requestedDate` changes.
- `closingTime` is the order cut-off for that delivery. If it is already past, the
  delivery date is not reachable and the order must not be committed automatically.
- Price is per sales unit. Content per sales unit (litres / kg) = catalogPrice /
  comparisonPrice when a comparison unit is given (snapped to a round value, since both
  prices are rounded to cents); the name-derived size confirms it.
- `allowedLotSize` is 0.01 for many boxes and pieces. Fractional boxes are not orderable
  in practice, so countable sales units are ordered in whole units (lot >= 1).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from .catalog import Product
from .clock import current_time
from .config import TZ

UNKNOWN_STOCK_THRESHOLD = 999_999

DeliveryStatus = Literal[
    "available",       # requested quantity can arrive by the requested date
    "partial",         # only part of the quantity is available
    "late",            # earliest delivery date is after the requested date
    "cutoff_passed",   # order deadline for the earliest delivery date has passed
    "out_of_stock",
    "sales_blocked",
    "unknown",         # API returned no availability data
]


@dataclass
class Delivery:
    status: DeliveryStatus
    available_amount: float | None  # in sales units; None = unknown stock, mock permits quantity
    earliest_date: str | None
    order_cutoff: str | None
    logistic_model: str | None
    notes: list[str] = field(default_factory=list)


@dataclass
class Content:
    per_sales_unit: float
    unit: str            # "L" or "KG"
    source: str          # "price_api" | "name" | "price_api+name"


@dataclass
class Offer:
    product_id: str
    name: str
    sales_unit: str
    lot_size: float
    requested_date: str
    unit_price: float | None            # EUR excl. VAT per sales unit
    unit_price_with_tax: float | None
    vat_rate: float | None
    list_price: float | None
    contract_price: bool                # netPriceFound: buyer-specific price applied
    price_valid_to: str | None
    comparison_price: float | None      # EUR per L / KG excl. VAT
    comparison_unit: str | None
    content: Content | None
    delivery: Delivery
    delivery_reliability: float | None
    co2_kg_per_kg: float | None
    contains_allergens: list[str]
    may_contain_allergens: list[str]
    claims: list[str]
    labels: list[str]
    official_substitutes: list[str]
    assumptions: list[str] = field(default_factory=list)
    origin_country: str | None = None   # countryOfOrigin, part of the signed offer

    def units_for(self, amount: float, unit: str) -> float | None:
        """Sales units needed to cover `amount` of `unit` (L, KG, or the sales unit)."""
        if unit.upper() == self.sales_unit.upper():
            need = amount
        elif self.content and self.content.unit == unit.upper():
            need = amount / self.content.per_sales_unit
        else:
            return None
        return round_up_to_lot(need, self.lot_size)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MEASURED_UNITS = {"KG", "L"}


def effective_lot(sales_unit: str, lot: float | None) -> float:
    lot = lot or 1.0
    return lot if sales_unit.upper() in MEASURED_UNITS else max(lot, 1.0)


def snap(value: float) -> float:
    """Snap a ratio of two cent-rounded prices to a nearby round size (9.98 -> 10)."""
    if value <= 0:
        return value
    digits = 2 - int(math.floor(math.log10(value))) - 1
    nice = round(value, digits)
    return nice if abs(nice - value) <= 0.015 * value else round(value, 3)


def round_up_to_lot(qty: float, lot: float) -> float:
    lot = lot or 1.0
    steps = math.ceil(round(qty / lot, 9))
    return round(steps * lot, 6)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return datetime.fromisoformat(value)


def assess_delivery(
    rec: dict[str, Any] | None,
    requested: date,
    quantity: float | None,
    now: datetime | None = None,
) -> Delivery:
    now = now or current_time(datetime.now(TZ))
    if rec is None:
        return Delivery("out_of_stock", 0, None, None, None,
                        ["not available: product absent from availability answer"])
    lines = rec.get("scheduleLines") or []
    cutoff = rec.get("closingTime")
    model = rec.get("logisticModel")
    if rec.get("salesBlock") or any(line.get("salesBlock") for line in lines):
        return Delivery("sales_blocked", 0, None, cutoff, model, ["product is sales-blocked"])
    if not lines:
        return Delivery("out_of_stock", 0, None, cutoff, model,
                        ["not available: empty availability schedule"])
    dated = [line for line in lines if line.get("availableDate")]
    if not dated or all(line.get("availableAmount", 0) <= 0 for line in lines):
        return Delivery("out_of_stock", 0, None, cutoff, model, ["no stock"])

    earliest = min(line["availableDate"] for line in dated)
    by_date = [line for line in dated if line["availableDate"] <= requested.isoformat()]
    amounts = [line.get("availableAmount", 0) for line in (by_date or dated)]
    unknown_stock = any(a >= UNKNOWN_STOCK_THRESHOLD for a in amounts)
    amount = None if unknown_stock else float(sum(amounts))
    notes: list[str] = (["stock unknown: Valio mock permits any quantity; "
                         "this is not verified unlimited stock"] if unknown_stock else [])

    cutoff_ts = _parse_ts(cutoff)
    if cutoff_ts and now > cutoff_ts:
        notes.append(f"order cut-off {cutoff} has passed for delivery on {earliest}")
        return Delivery("cutoff_passed", amount, earliest, cutoff, model, notes)
    if not by_date:
        notes.append(f"earliest delivery {earliest} is after requested {requested.isoformat()}")
        return Delivery("late", amount, earliest, cutoff, model, notes)
    if quantity is not None and amount is not None and quantity > amount:
        notes.append(f"only {amount:g} available of {quantity:g} requested")
        return Delivery("partial", amount, earliest, cutoff, model, notes)
    return Delivery("available", amount, earliest, cutoff, model, notes)


def derive_content(product: Product, price: dict[str, Any] | None) -> Content | None:
    from_name = None
    if product.name_size and product.name_size_unit:
        from_name = (product.name_size * product.base_per_sales, product.name_size_unit)
    from_price = None
    if price:
        cat = (price.get("catalogPrice") or {}).get("value")
        comp = (price.get("comparisonPrice") or {}).get("value")
        unit = (price.get("comparisonUnit") or "").upper()
        if cat and comp and unit in ("L", "KG"):
            from_price = (snap(cat / comp), unit)
    if from_price and from_name and from_price[1] == from_name[1]:
        if abs(from_price[0] - from_name[0]) <= 0.03 * from_name[0]:
            return Content(round(from_name[0], 4), from_name[1], "price_api+name")
        return Content(round(from_price[0], 3), from_price[1], "price_api")
    if from_price:
        return Content(round(from_price[0], 3), from_price[1], "price_api")
    if from_name:
        return Content(round(from_name[0], 4), from_name[1], "name")
    return None


def build_offer(
    product: Product,
    price: dict[str, Any] | None,
    availability: dict[str, Any] | None,
    requested: date,
    quantity: float | None = None,
    now: datetime | None = None,
) -> Offer:
    price = price if price and not price.get("error") else None
    cat = (price or {}).get("catalogPrice") or {}
    tax = (price or {}).get("tax") or {}
    comp = (price or {}).get("comparisonPrice") or {}
    content = derive_content(product, price)
    assumptions = [
        "availableAmount >= 999999 means unknown stock; Valio mock permits any quantity",
        "empty availability means not available",
        "closingTime treated as order cut-off for the earliest delivery date",
    ]
    if effective_lot(product.sales_unit, product.lot_size) != (product.lot_size or 1.0):
        assumptions.append(f"lot size {product.lot_size} raised to whole {product.sales_unit}")
    if content and content.source == "price_api":
        assumptions.append("content per sales unit derived from catalogPrice / comparisonPrice")
    if price is None:
        assumptions.append("price unavailable: offer cannot be ordered")
    return Offer(
        product_id=product.id,
        name=product.name,
        sales_unit=product.sales_unit,
        lot_size=effective_lot(product.sales_unit, product.lot_size),
        requested_date=requested.isoformat(),
        unit_price=cat.get("value"),
        unit_price_with_tax=cat.get("valueWithTax"),
        vat_rate=tax.get("taxRate"),
        list_price=((price or {}).get("listPrice") or {}).get("value"),
        contract_price=bool((price or {}).get("netPriceFound")),
        price_valid_to=cat.get("validTo"),
        comparison_price=comp.get("value"),
        comparison_unit=(price or {}).get("comparisonUnit"),
        content=content,
        delivery=assess_delivery(availability, requested, quantity, now),
        delivery_reliability=product.reliability,
        co2_kg_per_kg=product.co2_per_kg,
        contains_allergens=product.contains,
        may_contain_allergens=product.may_contain,
        claims=product.claims,
        labels=product.labels,
        official_substitutes=product.substitutes,
        assumptions=assumptions,
        origin_country=product.country,
    )
