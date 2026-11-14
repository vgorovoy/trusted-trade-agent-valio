"""Handwritten-style synthetic image extraction and interpretable line-quality controls."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from trade_agents.bench.menu_eval import (
    MENU_DIR,
    evaluate_menu_case,
    load_menu_cases,
    measure_menu_lines,
    run_menu_evaluation,
)
from trade_agents.llm import LLMResponse, ScriptedLLM


def fixture():
    return load_menu_cases(MENU_DIR / "handwritten-v1")[0]


def response_for(case):
    return {"needs": [{key: value for key, value in row.items() if key != "aliases"}
                      for row in case["expect"]["needs"]],
            "assumptions": ["Transcribed handwritten shopping quantities; chef must review the draft."],
            "warnings": []}


def evaluate(case, data):
    return evaluate_menu_case(case, ScriptedLLM([LLMResponse(text=json.dumps(data))],
                                              vision=True, name="scripted:handwritten-control"))


def test_actual_handwritten_png_is_sent_without_text_quantity_bypass(monkeypatch):
    from trade_agents.service import Gateway

    case = fixture()
    observed = []

    def inspect(messages, tools):
        assert not tools
        content = json.loads(messages[0]["content"])
        assert content["menu_text"] == case["text"]
        for fact in ["10 kg", "5 kg", "20 l", "60", "Makaronit", "Naudan"]:
            assert fact not in content["menu_text"]
        image, mime = messages[0]["images"][0]
        assert mime == "image/png"
        assert hashlib.sha256(image).hexdigest() == case["image"]["sha256"]
        observed.append(image)
        return LLMResponse(text=json.dumps(response_for(case)))

    def forbidden(*args, **kwargs):
        raise AssertionError("A handwritten list draft must not initialize purchasing")

    monkeypatch.setattr(Gateway, "__init__", forbidden)
    result = evaluate_menu_case(case, ScriptedLLM([inspect], vision=True, name="scripted:photo-control"))
    assert observed and result["passed"], result["checks"]
    metrics = result["line_metrics"]
    assert metrics["ingredient_line_precision"] == metrics["ingredient_line_recall"] == 1
    assert metrics["exact_amount_unit_date_lines"] == 6
    assert metrics["exact_line_fraction_of_expected"] == 1
    assert metrics["critical_quantity_or_unit_error_lines"] == 0
    assert metrics["omissions"] == metrics["extras"] == []
    assert metrics["review_boundary_present"] and metrics["model_review_caveat_keyword"]


@pytest.mark.parametrize("field,value,error_field", [
    ("amount", 50, "quantity_error_lines"),  # actual5 kg became50 kg
    ("unit", "L", "unit_error_lines"),
    ("date", "2026-11-17", "date_error_lines"),
])
def test_wrong_field_is_detected_without_hiding_ingredient_recognition(field, value, error_field):
    case = fixture()
    data = response_for(case)
    data["needs"][1][field] = value
    result = evaluate(case, data)
    assert not result["passed"]
    metrics = result["line_metrics"]
    assert metrics["ingredient_line_precision"] == metrics["ingredient_line_recall"] == 1
    assert metrics[error_field] == 1
    assert metrics["exact_amount_unit_date_lines"] == 5
    assert metrics["omissions"] == metrics["extras"] == []
    assert metrics["critical_quantity_or_unit_error_lines"] == (0 if field == "date" else 1)


def test_fifteen_instead_of_fifty_is_a_quantity_error_not_an_identity_omission():
    case = fixture()
    result = evaluate(case, response_for(case))
    reference = copy.deepcopy(case)
    reference["expect"]["needs"][1]["amount"] = 50
    draft = copy.deepcopy(result["draft"])
    draft["needs"][1]["amount"] = 15
    metrics = measure_menu_lines(reference, draft)
    assert metrics["quantity_error_lines"] == metrics["critical_quantity_or_unit_error_lines"] == 1
    assert metrics["matched_ingredient_lines"] == 6
    assert metrics["lines"][1]["errors"] == ["quantity"]


def test_missing_and_extra_ingredients_reduce_different_metrics():
    case = fixture()
    data = response_for(case)
    data["needs"].pop()
    missing = evaluate(case, data)
    assert not missing["passed"]
    metrics = missing["line_metrics"]
    assert metrics["ingredient_line_precision"] == 1
    assert metrics["ingredient_line_recall"] == 5 / 6
    assert metrics["omissions"] == [5] and metrics["extras"] == []
    data = response_for(case)
    data["needs"].append({"ingredient": "sugar", "amount": 2, "unit": "KG", "date": "2026-11-16"})
    extra = evaluate(case, data)
    assert not extra["passed"]
    metrics = extra["line_metrics"]
    assert metrics["ingredient_line_precision"] == 6 / 7
    assert metrics["ingredient_line_recall"] == 1
    assert metrics["extras"] == [6] and metrics["omissions"] == []
    assert metrics["exact_amount_unit_date_lines"] == 6  # extra-line penalty remains explicit


def test_noop_extraction_cannot_have_perfect_or_phantom_quality():
    case = fixture()
    result = evaluate(case, {"needs": [], "assumptions": [], "warnings": []})
    assert result["status"] == "error" and not result["passed"]
    metrics = result["line_metrics"]
    assert metrics["ingredient_line_precision"] == metrics["ingredient_line_recall"] == 0
    assert metrics["exact_amount_unit_date_lines"] == 0
    assert len(metrics["omissions"]) == 6
    assert not metrics["review_boundary_present"]
    # Zero measured bad quantity lines does not turn six omissions into a success.
    assert metrics["critical_quantity_or_unit_error_lines"] == 0


def test_unspecified_minced_meat_does_not_receive_beef_transcription_credit():
    case = fixture()
    data = response_for(case)
    data["needs"][1]["ingredient"] = "jauheliha"
    data["needs"][1]["search_hint_fi"] = "jauheliha"
    result = evaluate(case, data)
    assert not result["passed"]
    metrics = result["line_metrics"]
    assert metrics["matched_ingredient_lines"] == 5
    assert metrics["omissions"] == [1]
    assert metrics["extras"] == [1]


def test_duplicate_and_conflicting_recognized_identity_do_not_get_double_credit():
    case = fixture()
    data = response_for(case)
    data["needs"].append(copy.deepcopy(data["needs"][0]))
    result = evaluate(case, data)
    assert not result["passed"]
    assert result["line_metrics"]["duplicate_lines"] == 1
    assert result["line_metrics"]["matched_ingredient_lines"] == 6
    data = response_for(case)
    data["needs"][0]["search_hint_fi"] = "sipuli"  # recognized contradiction with pasta name
    result = evaluate(case, data)
    assert not result["passed"]
    assert result["line_metrics"]["ambiguous_identity_lines"] == [0]
    assert result["line_metrics"]["matched_ingredient_lines"] == 5


def test_automatic_review_warning_is_not_counted_as_model_review_recognition():
    case = fixture()
    data = response_for(case)
    data["assumptions"] = []
    result = evaluate(case, data)
    assert result["passed"]  # faithful extraction is separate from explicit model caveat wording
    assert result["line_metrics"]["review_boundary_present"]
    assert not result["line_metrics"]["model_review_caveat_keyword"]


def test_quality_aggregate_keeps_failed_attempts_in_reference_recall(tmp_path):
    case = fixture()
    duplicate = copy.deepcopy(case)
    duplicate["id"] += "-failed-control"
    outputs = iter([response_for(case), {"needs": []}])
    report = run_menu_evaluation([case, duplicate], lambda: ScriptedLLM(
        [LLMResponse(text=json.dumps(next(outputs)))], vision=True, name="scripted:quality-aggregate"),
        output=tmp_path, run_id="handwritten-quality-controls")
    assert report["attempted"] == 2 and report["errors"] == 1 and report["passed"] == 1
    metrics = report["quality"]
    assert metrics["expected_lines"] == 12
    assert metrics["ingredient_line_recall"] == metrics["exact_line_fraction_of_expected"] == .5
    assert metrics["omission_lines"] == 6
    assert metrics["review_boundary_cases"] == 1


def test_repeated_ingredient_dates_match_one_to_one_without_order_dependence():
    weekly = load_menu_cases(MENU_DIR / "v3")[2]
    data = response_for(weekly)
    data["needs"].reverse()
    result = evaluate(weekly, data)
    assert result["passed"]
    assert result["line_metrics"]["exact_amount_unit_date_lines"] == 7
    oats = [need for need in data["needs"] if need["ingredient"] == "oat drink"]
    oats[0]["date"] = "2026-11-16"  # second delivery's6 L incorrectly moved to first date
    result = evaluate(weekly, data)
    assert not result["passed"]
    metrics = result["line_metrics"]
    assert metrics["matched_ingredient_lines"] == 7
    assert metrics["date_error_lines"] == 1
    assert metrics["exact_amount_unit_date_lines"] == 6
