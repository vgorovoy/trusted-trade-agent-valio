"""Synthetic Valio-only top-up arithmetic controls; operational pack case stays gated."""
from __future__ import annotations

import copy
import json

import pytest

from trade_agents.bench.menu_eval import MENU_DIR, evaluate_menu_case, load_menu_cases
from trade_agents.llm import LLMResponse, ScriptedLLM

SUITE = MENU_DIR / "custdev-v1"


def case():
    return load_menu_cases(SUITE)[0]


def response_for(fixture):
    return {"needs": [{key: value for key, value in row.items() if key != "aliases"}
                      for row in fixture["expect"]["needs"]],
            "assumptions": ["Supplied stock and receipts applied once, chef must review."],
            "warnings": []}


def evaluate(fixture, response):
    return evaluate_menu_case(fixture, ScriptedLLM(
        [LLMResponse(text=json.dumps(response))], name="scripted:custdev-oracle-control"))


def test_weekend_topup_is_review_only_and_uses_independent_stock_arithmetic(monkeypatch):
    from trade_agents.service import Gateway

    def forbidden(*args, **kwargs):
        raise AssertionError("Draft arithmetic must not initialize an order gateway")

    monkeypatch.setattr(Gateway, "__init__", forbidden)
    fixture = case()
    assert [row["amount"] for row in fixture["expect"]["needs"]] == [
        12 - (7 - 2 - 1) - 3, 10 - 3 - 2, 4 - 1 - 1,
        8 - 2, 6, 3 - 1,
    ]
    result = evaluate(fixture, response_for(fixture))
    assert result["passed"], result["checks"]
    assert result["draft"]["requires_review"] and result["draft"]["requires_approval"]
    assert result["draft"]["status"] == "draft"
    assert "Valio Aimo only" in fixture["supplier_scope"]


@pytest.mark.parametrize("fault", [
    "gross_demand", "ignore_receipt", "double_count_stock", "double_count_receipt",
    "consume_reserved_expired", "use_monday_receipt", "merge_delivery_dates", "extra_item",
])
def test_topup_oracle_rejects_realistic_inventory_and_batch_errors(fault):
    fixture = case()
    response = response_for(fixture)
    rows = response["needs"]
    if fault == "gross_demand":
        rows[0]["amount"] = 12
    elif fault == "ignore_receipt":
        rows[0]["amount"] = 8
    elif fault == "double_count_stock":
        rows[3]["amount"] = 2  # Saturday6 minus Friday's already consumed4 KG
    elif fault == "double_count_receipt":
        rows[3]["amount"] = 3  # Friday's3 KG receipt reused on Saturday
    elif fault == "consume_reserved_expired":
        rows[0]["amount"] = 2  # Friday12 minus all7 KG stock minus3 KG receipt
    elif fault == "use_monday_receipt":
        rows[3]["amount"] = 1
    elif fault == "merge_delivery_dates":
        rows[0]["amount"] += rows[3]["amount"]
        del rows[3]
    else:
        rows.append({"ingredient": "salt", "amount": 1, "unit": "KG", "date": "2026-11-20"})
    result = evaluate(fixture, response)
    assert result["status"] == "scored"
    assert not result["passed"]
    assert any(not check["ok"] for check in result["checks"])


def test_order_independent_rows_preserve_both_delivery_batches():
    fixture = case()
    response = response_for(fixture)
    response["needs"].reverse()
    assert evaluate(fixture, response)["passed"]


def test_topup_rejects_contradictory_ingredient_search_hint():
    fixture = case()
    response = response_for(fixture)
    response["needs"][0]["search_hint_fi"] = "kaurajuoma"
    result = evaluate(fixture, response)
    assert result["status"] == "scored"
    assert not result["passed"]
    assert result["line_metrics"]["ambiguous_identity_lines"] == [0]
    assert any(check["name"] == "quality.exact_reference_lines" and not check["ok"]
               for check in result["checks"])


def test_pack_surplus_spec_is_not_loaded_or_scored_as_executable():
    fixtures = load_menu_cases(SUITE)
    assert len(fixtures) == 1
    spec = json.loads((SUITE / "specifications" / "02-pack-surplus-review.json").read_text())
    assert spec["execution_status"] == "capability_gated_not_executable"
    gate = spec["capability_gate"]
    assert gate["included_in_executable_suite"] is False and gate["score"] is None
    assert len(gate["missing_evaluator_capabilities"]) == 4
    state, oracle = spec["initial_state"], spec["exact_oracle"]
    assert state["surplus_authorization"] is None
    assert oracle["ingredient_draft_demand_kg"] == state["net_demand_kg"] == 3
    assert oracle["proposed_supply_kg"] == state["kg_per_case"] * oracle["proposed_cases"] == 5
    assert oracle["surplus_kg"] == oracle["proposed_supply_kg"] - state["net_demand_kg"] == 2
    assert oracle["total_eur"] < state["mandate_auto_limit_eur"]
    assert oracle["orders_before_explicit_surplus_approval"] == 0
    assert "surplus" in oracle["required_before_commit"]
    assert all(fixture["id"] != spec["id"] for fixture in fixtures)
    # A generic reviewed menu draft must not be used as operational pass evidence.
    response = response_for(copy.deepcopy(fixtures[0]))
    assert evaluate(fixtures[0], response)["passed"]
    assert gate["score"] is None

pytestmark = pytest.mark.skipif(
    not SUITE.exists(),
    reason="Private fixture assets are intentionally excluded from the public snapshot",
)
