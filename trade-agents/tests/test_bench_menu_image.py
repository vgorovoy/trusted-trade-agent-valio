"""Real PNG byte plumbing through the vision draft path; scripted responses are controls."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from trade_agents.bench.menu_eval import (
    MENU_DIR,
    evaluate_menu_case,
    load_menu_cases,
    load_menu_image,
    run_menu_evaluation,
)
from trade_agents.bench.records import verify_run
from trade_agents.llm import LLMResponse, ScriptedLLM


def image_case():
    return load_menu_cases(MENU_DIR / "image-v1")[0]


def expected_response(case):
    return {"needs": [{key: value for key, value in row.items() if key != "aliases"}
                      for row in case["expect"]["needs"]],
            "assumptions": ["Synthetic chef recipe and usable stock read from the card."],
            "warnings": []}


def test_image_fixture_bytes_reach_vision_model_without_recipe_text_bypass(monkeypatch):
    from trade_agents.service import Gateway

    case = image_case()
    seen = []

    def respond(messages, tools):
        seen.append(messages)
        assert not tools
        content = json.loads(messages[0]["content"])
        assert content["menu_text"] == case["text"]
        # Count/context is permitted; exact recipe and inventory quantities are image-only.
        assert "6 KG" not in messages[0]["content"]
        assert "0.5 L" not in messages[0]["content"]
        assert "8 KG" not in messages[0]["content"]
        data, mime = messages[0]["images"][0]
        assert mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(data).hexdigest() == case["image"]["sha256"]
        return LLMResponse(text=json.dumps(expected_response(case)))

    def no_gateway(*args, **kwargs):
        raise AssertionError("An image ingredient draft must not initialize a gateway")

    monkeypatch.setattr(Gateway, "__init__", no_gateway)
    result = evaluate_menu_case(case, ScriptedLLM([respond], vision=True, name="scripted:vision-control"))
    assert result["passed"], result["checks"]
    assert seen and result["modality"] == "image"
    assert result["image_sha256"] == case["image"]["sha256"]
    assert result["draft"]["status"] == "draft"
    assert result["draft"]["requires_review"]


def test_nonvision_model_cannot_silently_fall_back_to_text():
    case = image_case()
    llm = ScriptedLLM([LLMResponse(text=json.dumps(expected_response(case)))],
                      vision=False, name="scripted:text-only-control")
    result = evaluate_menu_case(case, llm)
    assert result["status"] == "error" and not result["passed"]
    assert result["error_type"] == "MenuDraftError"
    assert llm.calls == []


@pytest.mark.parametrize("path", ["../outside.png", "/tmp/outside.png"])
def test_image_source_is_restricted_to_curated_fixture_tree(path):
    case = image_case()
    case["image"]["path"] = path
    with pytest.raises(ValueError, match="curated"):
        load_menu_image(case)


def test_wrong_image_hash_is_an_error_before_model_call():
    case = image_case()
    case["image"]["sha256"] = "0" * 64
    llm = ScriptedLLM([], vision=True, name="scripted:unused")
    result = evaluate_menu_case(case, llm)
    assert result["status"] == "error" and llm.calls == []


def test_image_archive_pins_actual_png_and_does_not_mislabel_suite_version(tmp_path):
    case = image_case()
    report = run_menu_evaluation(
        [case], lambda: ScriptedLLM([LLMResponse(text=json.dumps(expected_response(case)))],
                                   vision=True, name="scripted:image-archive-control"),
        output=tmp_path, run_id="image-control")
    assert report["passed"] == report["scored"] == 1
    assert report["suite"] == "menu-draft-image-v1"
    archive = Path(report["archive"])
    manifest = verify_run(archive)
    assert manifest["artifacts"]["images/case-01.png"] == case["image"]["sha256"]
    assert (archive / "images/case-01.png").read_bytes() == load_menu_image(case)
    inputs = json.loads((archive / "inputs.json").read_text())
    assert inputs["suite"][0]["image"]["sha256"] == case["image"]["sha256"]


def test_image_wrong_quantity_still_fails():
    case = image_case()
    data = expected_response(case)
    data["needs"][0]["amount"] = 60  # gross pasta, forgetting8 KG usable inventory
    result = evaluate_menu_case(case, ScriptedLLM([LLMResponse(text=json.dumps(data))],
                                                vision=True, name="scripted:bad-quantity"))
    assert not result["passed"]
    assert any(c["name"] == "quantity.0" and not c["ok"] for c in result["checks"])


def test_image_aliases_do_not_rewrite_frozen_text_v2():
    case = image_case()
    image_pasta = case["expect"]["needs"][0]
    assert "dry macaroni" in image_pasta["aliases"]
    assert "kuivamakaroni" in image_pasta["aliases"]
    old = load_menu_cases(MENU_DIR / "v2")[0]
    assert "dry macaroni" not in old["expect"]["needs"][0]["aliases"]
    copied = copy.deepcopy(case)
    copied["expect"]["needs"][0]["amount"] = 60
    assert copied["expect"]["needs"][0]["amount"] != case["expect"]["needs"][0]["amount"]
