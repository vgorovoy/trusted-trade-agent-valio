"""Actual menu-draft pipeline with deterministic replies and independent outcome oracles."""
from __future__ import annotations

import copy
import json

import pytest

from trade_agents.bench.menu_eval import (
    evaluate_menu_case,
    load_menu_cases,
    run_menu_evaluation,
    verify_menu_draft,
)
from trade_agents.bench.records import verify_run
from trade_agents.llm import LLMResponse, ScriptedLLM, ToolCall


def response_for(case):
    rows = case["expect"].get("needs")
    if rows is None:
        rows = [{"ingredient": "macaroni", "amount": 60, "unit": "KG", "date": "2026-11-16"}]
    return {"needs": [{k: v for k, v in row.items() if k != "aliases"} for row in rows],
            "assumptions": ["Chef must supply the approved recipe and usable stock."],
            "warnings": []}


def model_for(case, mutate=None):
    response = response_for(case)
    if mutate:
        mutate(response)
    return ScriptedLLM([LLMResponse(text=json.dumps(response))], name="scripted:menu-test")


@pytest.mark.parametrize("case", load_menu_cases(), ids=lambda case: case["id"])
def test_menu_case_exercises_review_only_pipeline_with_supplied_facts(case, monkeypatch):
    from trade_agents.buyer_agent.agent import BuyerAgent
    from trade_agents.service import Gateway

    def forbidden(*args, **kwargs):
        raise AssertionError("Draft extraction must never initialize a transaction")

    monkeypatch.setattr(Gateway, "__init__", forbidden)
    monkeypatch.setattr(BuyerAgent, "run", forbidden)
    llm = model_for(case)
    result = evaluate_menu_case(case, llm)
    assert result["passed"], result["checks"]
    assert result["draft"]["status"] == "draft"
    assert result["draft"]["source"] == "model_estimate"
    sent = json.loads(llm.calls[0][0]["content"])
    assert sent["menu_text"] == case["text"]
    assert sent["guests"] == case["guests"]
    assert result["execution_boundary"].endswith("no purchase executor supplied")


def test_school_norms_are_not_gross_quantities_or_uniform_multiplier():
    case = load_menu_cases()[0]
    response = model_for(case, lambda data: data["needs"][0].update(amount=60))
    result = evaluate_menu_case(case, response)
    assert not result["passed"]
    assert any(c["name"] == "quantity.0" and not c["ok"] for c in result["checks"])


@pytest.mark.parametrize("fault", ["merge_dates", "reuse_inventory", "ignore_reservations"])
def test_weekly_oracle_catches_compound_quantity_errors(fault):
    case = load_menu_cases()[2]

    def mutate(data):
        if fault == "merge_dates":
            data["needs"][2]["amount"] += data["needs"][5]["amount"]
            del data["needs"][5]
        elif fault == "reuse_inventory":
            data["needs"][5]["amount"] = 4  # subtract initial2 L oat stock a second time
        else:
            data["needs"][0]["amount"] = 8  # subtract all6 KG instead of usable2

    result = evaluate_menu_case(case, model_for(case, mutate))
    assert not result["passed"]
    assert any(not c["ok"] for c in result["checks"] if c["name"].startswith("quantity."))


def test_underspecified_recipe_cannot_get_quantity_accuracy_credit():
    case = load_menu_cases()[1]
    result = evaluate_menu_case(case, model_for(case))
    assert result["passed"] and result["scope"] == "review_protocol_only"
    assert not any(c["name"].startswith("quantity.") for c in result["checks"])
    no_assumptions = model_for(case, lambda data: data.update(assumptions=[]))
    result = evaluate_menu_case(case, no_assumptions)
    assert not result["passed"]  # automatic harness warning alone is not model recognition


def test_synonyms_are_accepted_but_duplicate_rows_are_not():
    case = load_menu_cases()[0]
    llm = model_for(case, lambda data: data["needs"][0].update(ingredient="makaroni"))
    result = evaluate_menu_case(case, llm)
    assert result["passed"]
    draft = copy.deepcopy(result["draft"])
    draft["needs"][0]["amount"] /= 2
    draft["needs"].append(copy.deepcopy(draft["needs"][0]))
    assert not all(c["ok"] for c in verify_menu_draft(case, draft))


def test_invalid_tool_actions_are_errors_never_executed():
    case = load_menu_cases()[0]
    llm = ScriptedLLM([LLMResponse(tool_calls=[ToolCall("place_order", {})])], name="scripted:bad")
    result = evaluate_menu_case(case, llm)
    assert result["status"] == "error" and not result["passed"]
    assert result["error_type"] == "MenuDraftError"


def test_every_attempt_and_real_scripted_reply_are_archived(tmp_path):
    cases = load_menu_cases()
    responses = iter(cases)
    report = run_menu_evaluation(cases, lambda: model_for(next(responses)),
                                 output=tmp_path, run_id="scripted-menu-controls")
    assert report["attempted"] == report["scored"] == report["passed"] == 3
    assert report["coverage"] == 1
    archive = tmp_path / "scripted-menu-controls"
    verify_run(archive)
    assert len(list((archive / "replies").glob("*.json"))) == 3
    with pytest.raises(FileExistsError):
        run_menu_evaluation(cases, lambda: model_for(cases[0]), output=tmp_path,
                            run_id="scripted-menu-controls")


def test_errors_stay_in_denominator_and_sensitive_error_text_is_not_saved(tmp_path):
    cases = load_menu_cases()

    def broken_factory():
        raise RuntimeError("private credential diagnostic")

    report = run_menu_evaluation(cases, broken_factory, output=tmp_path, run_id="errors")
    assert report["attempted"] == report["errors"] == 3
    assert report["scored"] == report["passed"] == report["coverage"] == 0
    assert report["pass_fraction_of_attempts"] == 0
    assert "private credential" not in json.dumps(report)
    manifest = verify_run(tmp_path / "errors")
    assert manifest["status"] == "error"
