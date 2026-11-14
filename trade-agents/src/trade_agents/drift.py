"""Explain changes before renewing a weekly order; no API calls or automatic orders.

Input: chronological [{week: ISO date, lines: [{product_id, name?, quantity,
unit_price_eur, unit}]}]. Last week is the proposed renewal, preceding weeks history.
Thresholds: quantity > linear forecast by 20%, price > historical median by 10%.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import median


def detect_drift(weeks: list[dict], *, quantity_threshold: float = 0.2,
                 price_threshold: float = 0.1) -> dict:
    if len(weeks) < 4:
        raise ValueError("Need at least three historical weeks and one proposed renewal")
    if quantity_threshold < 0 or price_threshold < 0:
        raise ValueError("Thresholds must be non-negative")
    dates = [date.fromisoformat(w["week"]) for w in weeks]
    if any((b - a).days != 7 for a, b in zip(dates, dates[1:], strict=False)):
        raise ValueError("Weeks must be consecutive, chronological and seven days apart")
    orders = []
    units = {}
    for week in weeks:
        lines = {}
        for line in week["lines"]:
            pid = line["product_id"]
            if pid in lines:
                raise ValueError(f"Duplicate product {pid}; aggregate lines first")
            for key in ("quantity", "unit_price_eur"):
                if not math.isfinite(line[key]) or line[key] <= 0:
                    raise ValueError(f"{key} must be finite and positive")
            if not line.get("unit") or (pid in units and units[pid] != line["unit"]):
                raise ValueError(f"Missing or inconsistent unit for {pid}")
            units[pid] = line["unit"]
            lines[pid] = line
        orders.append(lines)
    history, proposed = orders[:-1], orders[-1]
    anomalies = []
    def add(pid, kind, explanation):
        anomalies.append({"product_id": pid, "kind": kind, "explanation": explanation,
                          "recommendation": "ask"})
    previous_ids = set().union(*history)
    for pid in sorted(proposed.keys() - previous_ids):
        add(pid, "new_product", "New product: not seen in this kitchen's history; confirm its purpose")
    # Missing means present in the immediately preceding order; occasional historical
    # purchases are not treated as standing items forever.
    for pid in sorted(history[-1].keys() - proposed.keys()):
        add(pid, "missing_product", "Item from last week's order is missing; confirm the menu change")
    for pid in sorted(proposed.keys() & previous_ids):
        samples = [(i, order[pid]) for i, order in enumerate(history) if pid in order]
        current = proposed[pid]
        baseline = median(line["unit_price_eur"] for _, line in samples)
        if current["unit_price_eur"] > baseline * (1 + price_threshold) + 1e-9:
            add(pid, "unit_price_increase",
                f"Price per {current['unit']} rose from a median of {baseline:.2f} to "
                f"{current['unit_price_eur']:.2f} EUR; check the price before renewing")
        if len(samples) < 3:
            add(pid, "insufficient_history", "Fewer than three observations: quantity trend not yet reliable")
            continue
        xs = [i for i, _ in samples]
        ys = [line["quantity"] for _, line in samples]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sum((x - mx) ** 2 for x in xs)
        forecast = max(0, my + slope * (len(history) - mx))
        if current["quantity"] > forecast * (1 + quantity_threshold) + 1e-9:
            add(pid, "quantity_above_trend",
                f"Quantity {current['quantity']:g} {current['unit']} is above the weekly-trend forecast "
                f"of {forecast:.1f}; confirm the number of servings")
    return {"week": weeks[-1]["week"], "anomalies": anomalies,
            "recommendation": "ask" if anomalies else "renew",
            "explanation": "Review the changes before auto-renewal" if anomalies else
                           "Quantities follow the trend, prices are stable, order contents unchanged"}
