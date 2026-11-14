"""Alias corrections are a new oracle over preserved replies, not new model successes."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from trade_agents.bench.menu_eval import MENU_DIR, load_menu_cases, run_menu_evaluation
from trade_agents.bench.menu_rescore import rescore_menu_archive, validate_alias_revision
from trade_agents.bench.records import verify_run
from trade_agents.llm import LLMResponse, ScriptedLLM


def response(case):
    rows = [{key: value for key, value in row.items() if key != "aliases"}
            for row in case["expect"].get("needs", [])]
    if not rows:
        rows = [{"ingredient": "macaroni", "amount": 60, "unit": "KG", "date": "2026-11-16"}]
    for row in rows:
        if row["ingredient"] == "oil":
            row.update(ingredient="Cooking oil", search_hint_fi="ruokaoljy")
    return {"needs": rows, "assumptions": ["Missing recipe and usable stock need review"],
            "warnings": []}


def archived_original(tmp_path, *, wrong_quantity=False):
    cases = load_menu_cases()
    cases_iterator = iter(cases)

    def llm():
        case = next(cases_iterator)
        data = response(case)
        if wrong_quantity and case["id"].startswith("school-makaronilaatikko-1000-recipe"):
            data["needs"][0]["amount"] = 600
        return ScriptedLLM([LLMResponse(text=json.dumps(data))], name="scripted:alias-probe")

    report = run_menu_evaluation(cases, llm, output=tmp_path, run_id="source-v1")
    assert report["passed"] == 1  # the original oracle misses oil in both exact cases
    return tmp_path / "source-v1"


def test_alias_revision_changes_only_acceptance_vocabulary():
    old, revised = load_menu_cases(), load_menu_cases(MENU_DIR / "v2")
    validate_alias_revision(old, revised)
    assert all(case["suite_version"] == "menu-draft-v2" for case in revised)
    for case in revised:
        for row in case["expect"].get("needs", []):
            if row["ingredient"] == "oil":
                assert {"cooking oil", "ruokaöljy", "ruokaoljy"} <= set(row["aliases"])


@pytest.mark.parametrize("mutation", ["amount", "date", "prompt", "removed_alias", "extra_row"])
def test_rescore_cannot_silently_change_quantity_date_or_request(mutation):
    old, revised = load_menu_cases(), load_menu_cases(MENU_DIR / "v2")
    if mutation == "amount":
        revised[0]["expect"]["needs"][0]["amount"] += 1
    elif mutation == "date":
        revised[0]["expect"]["needs"][0]["date"] = "2026-11-17"
    elif mutation == "prompt":
        revised[0]["text"] += " Different recipe."
    elif mutation == "removed_alias":
        revised[0]["expect"]["needs"][0]["aliases"] = []
    else:
        revised[0]["expect"]["needs"].append(copy.deepcopy(revised[0]["expect"]["needs"][0]))
    with pytest.raises(ValueError):
        validate_alias_revision(old, revised)


def test_regrade_preserves_v1_and_anchors_original_manifest(tmp_path, monkeypatch):
    original = archived_original(tmp_path)
    before = {str(path.relative_to(original)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in original.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        raise AssertionError("Rescoring may not call a model")

    monkeypatch.setattr(ScriptedLLM, "chat", forbidden)
    result = rescore_menu_archive(original, load_menu_cases(MENU_DIR / "v2"), output=tmp_path,
                                 run_id="regrade-v2")
    assert result["original_passed"] == 1 and result["passed"] == 3
    assert result["regrade_only"] and result["new_model_calls"] == 0
    assert result["source_manifest_sha256"] == before["manifest.json"]
    after = {str(path.relative_to(original)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in original.rglob("*") if path.is_file()}
    assert before == after
    verify_run(original)
    verify_run(tmp_path / "regrade-v2")
    copy_report = json.loads((tmp_path / "regrade-v2" / "original-report.json").read_text())
    assert copy_report["passed"] == 1
    assert result["results"][0]["draft"] == copy_report["results"][0]["draft"]
    with pytest.raises(FileExistsError):
        rescore_menu_archive(original, load_menu_cases(MENU_DIR / "v2"), output=tmp_path,
                             run_id="regrade-v2")


def test_rescore_rejects_modified_source_archive(tmp_path):
    original = archived_original(tmp_path)
    report_path = original / "report.json"
    report_path.chmod(0o644)
    data = json.loads(report_path.read_text())
    data["results"][0]["draft"]["needs"][0]["amount"] = 600
    report_path.write_text(json.dumps(data))
    # An altered source archive must be rejected before any regrading is attempted.
    with pytest.raises(ValueError, match="modified"):
        rescore_menu_archive(original, load_menu_cases(MENU_DIR / "v2"), output=tmp_path,
                             run_id="tampered")


def test_alias_revision_does_not_forgive_actual_quantity_error(tmp_path):
    original = archived_original(tmp_path, wrong_quantity=True)
    result = rescore_menu_archive(original, load_menu_cases(MENU_DIR / "v2"), output=tmp_path,
                                 run_id="quantity-still-wrong")
    assert result["passed"] == 2
    failed = result["results"][0]
    assert not failed["passed"]
    assert any(check["name"] == "quantity.0" and not check["ok"] for check in failed["checks"])


def test_frozen_v1_can_be_rescored_to_v3_without_changing_input_facts():
    original = load_menu_cases()
    validate_alias_revision(original, load_menu_cases(MENU_DIR / "v3"))
    validate_alias_revision(load_menu_cases(MENU_DIR / "image-v1"),
                            load_menu_cases(MENU_DIR / "image-v2"))


def test_later_source_version_can_receive_a_newer_alias_revision():
    old = load_menu_cases(MENU_DIR / "v2")
    revised = load_menu_cases(MENU_DIR / "v3")
    for before, after in zip(old, revised, strict=True):
        after["source_case_id"] = before["id"]
    validate_alias_revision(old, revised)


@pytest.mark.parametrize("version", ["menu-draft-v1", "menu-draft-v0", "menu-draft-image-v3",
                                      "menu-draft-v03", "another-menu-v3"])
def test_backwards_same_version_or_cross_family_is_forbidden(version):
    revised = load_menu_cases(MENU_DIR / "v3")
    for case in revised:
        case["suite_version"] = version
    with pytest.raises(ValueError):
        validate_alias_revision(load_menu_cases(), revised)


@pytest.mark.parametrize("family", ["text", "image"])
def test_explicit_recipe_wording_aliases_pass_but_wrong_quantity_still_fails(tmp_path, family):
    if family == "text":
        original, revised = load_menu_cases(), load_menu_cases(MENU_DIR / "v3")
    else:
        original = load_menu_cases(MENU_DIR / "image-v1")
        revised = load_menu_cases(MENU_DIR / "image-v2")
    iterator = iter(original)

    def llm():
        case = next(iterator)
        data = response(case)
        for row in data["needs"]:
            if row["ingredient"] == "pasta":
                row.update(ingredient="Dry macaroni", search_hint_fi="kuivamakaroni")
            elif row["ingredient"] == "minced beef":
                row.update(ingredient="Raw minced beef", search_hint_fi="raaka jauheliha")
        return ScriptedLLM([LLMResponse(text=json.dumps(data))], vision=True,
                           name="scripted:literal-recipe-vocabulary")

    source_report = run_menu_evaluation(original, llm, output=tmp_path, run_id=f"{family}-source")
    source = tmp_path / f"{family}-source"
    original_bytes = (source / "report.json").read_bytes()
    result = rescore_menu_archive(source, revised, output=tmp_path, run_id=f"{family}-corrected")
    assert result["passed"] == len(original)
    assert result["new_model_calls"] == 0
    assert result["original_passed"] == source_report["passed"]
    assert (source / "report.json").read_bytes() == original_bytes
    changed = copy.deepcopy(revised)
    changed[0]["expect"]["needs"][0]["amount"] += 1
    with pytest.raises(ValueError, match="quantities"):
        rescore_menu_archive(source, changed, output=tmp_path, run_id="not-an-alias-correction")
    from trade_agents.bench.menu_eval import verify_menu_draft

    wrong_draft = copy.deepcopy(result["results"][0]["draft"])
    wrong_draft["needs"][0]["amount"] += 1
    assert not all(check["ok"] for check in verify_menu_draft(revised[0], wrong_draft))
