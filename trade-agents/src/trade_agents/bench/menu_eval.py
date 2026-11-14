"""Separate evaluation of review-only menu drafts, never transaction completion.

Exact quantities are graded only when the fixture provides recipe and inventory facts.
No gateway, catalog, purchase executor or production buyer key is constructed here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..buyer_agent.menu_draft import draft_menu
from ..buyer_agent.profiles import BuyerProfile
from ..config import TZ
from ..llm import LLM
from ..policy import Mandate
from .records import RunArchive, source_identity
from .replay import ReplayLLM
from .scenario import BENCH_DIR

MENU_DIR = BENCH_DIR / "menu"


def load_menu_cases(directory: Path = MENU_DIR) -> list[dict[str, Any]]:
    cases = [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]
    ids = [case["id"] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("menu suite must contain cases with unique IDs")
    return cases


def load_menu_image(case: dict[str, Any]) -> bytes | None:
    """Only pinned, repository-owned image fixtures may be sent to the model."""
    image = case.get("image")
    if image is None:
        return None
    relative = Path(image["path"])
    path = MENU_DIR / relative
    if (relative.is_absolute() or ".." in relative.parts or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
            or not path.resolve().is_relative_to(MENU_DIR.resolve())):
        raise ValueError("menu image must be a regular curated fixture inside bench/menu")
    data = path.read_bytes()
    _validate_image_bytes(case, data)
    return data


def _validate_image_bytes(case: dict[str, Any], data: bytes) -> None:
    image = case["image"]
    signatures = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
    signature = signatures.get(image.get("mime"))
    if (signature is None or not data.startswith(signature)
            or hashlib.sha256(data).hexdigest() != image.get("sha256")):
        raise ValueError("menu image does not match its pinned PNG/JPEG fixture")


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().strip().split())


def _profile(case: dict[str, Any]) -> BuyerProfile:
    """Unsigned fixture context: drafting requires no purchasing credentials or passport."""
    expect = case["expect"]
    return BuyerProfile(
        key=case["buyer"], kitchen=f"Synthetic {case['buyer']} menu evaluation",
        mandate=Mandate(
            mandate_id="menu-eval-only", buyer_id="menu-eval", buyer_name="Menu evaluation",
            agent_id="menu-draft-evaluator", valid_until=datetime(2026, 12, 31, tzinfo=TZ),
            auto_order_limit_eur=0, excluded_allergens=expect.get("excluded_allergens", []),
            exclude_may_contain=expect.get("exclude_may_contain", False)),
        default_delivery=date(2026, 11, 16))


def measure_menu_lines(case: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """Reference-line extraction metrics, not proof of recipe or food safety.

    Ingredient identity is matched before quantity/unit/date, so a wrong unit is an
    identified error rather than being hidden as an omission. Repeated ingredients on
    different dates use one-to-one assignment, preferring exact fields, then date/unit.
    Contradictory recognized ingredient/name hints are ambiguous, never counted correct.
    """
    reference = case["expect"].get("needs")
    review = (draft.get("status") == "draft" and draft.get("requires_review") is True
              and draft.get("requires_approval") is True and draft.get("source") == "model_estimate")
    notes = _normal(" ".join([*draft.get("assumptions", []),
                             *(draft.get("warnings") or [])[1:]]))
    base = {"review_boundary_present": review,
            "model_review_caveat_keyword": any(word in notes for word in ("review", "chef", "tarkist")),
            "review_caveat_scope": "keyword signal excluding the automatic harness warning; "
                                    "not semantic understanding",
            "scope": "ingredient extraction against supplied references; not allergen or recipe safety"}
    if reference is None:
        return {**base, "reference_available": False, "reason": "No trusted numeric recipe reference"}
    needs = draft.get("needs") or []
    groups: dict[str, list[int]] = {}
    aliases: dict[str, set[str]] = {}
    for index, row in enumerate(reference):
        identity = _normal(row["ingredient"])
        groups.setdefault(identity, []).append(index)
        aliases.setdefault(identity, set()).update(_normal(value) for value in
                                                  [row["ingredient"], *row.get("aliases", [])])
    predicted: dict[str, list[int]] = {identity: [] for identity in groups}
    ambiguous = []
    for index, need in enumerate(needs):
        names = {_normal(str(need.get(field, ""))) for field in ("ingredient", "search_hint_fi")}
        identities = [identity for identity, values in aliases.items() if names & values]
        if len(identities) == 1:
            predicted[identities[0]].append(index)
        elif len(identities) > 1:
            ambiguous.append(index)
    assignments: dict[int, int] = {}
    used = set()
    for identity, expected_indices in groups.items():
        pairs = []
        for expected_index in expected_indices:
            expected = reference[expected_index]
            for predicted_index in predicted[identity]:
                need = needs[predicted_index]
                amount = abs(float(need["amount"]) - expected["amount"]) <= 0.001
                unit = need.get("unit") == expected["unit"]
                when = need.get("date") == expected["date"]
                pairs.append(((amount and unit and when, when, unit, amount,
                               -expected_index, -predicted_index), expected_index, predicted_index))
        for _, expected_index, predicted_index in sorted(pairs, reverse=True):
            if expected_index not in assignments and predicted_index not in used:
                assignments[expected_index] = predicted_index
                used.add(predicted_index)
    lines = []
    for index, expected in enumerate(reference):
        predicted_index = assignments.get(index)
        need = needs[predicted_index] if predicted_index is not None else None
        unit = need is not None and need.get("unit") == expected["unit"]
        amount = (abs(float(need["amount"]) - expected["amount"]) <= 0.001) if unit else None
        when = need is not None and need.get("date") == expected["date"]
        lines.append({"ingredient": expected["ingredient"], "expected_amount": expected["amount"],
                      "expected_unit": expected["unit"], "expected_date": expected["date"],
                      "predicted_index": predicted_index, "predicted": need,
                      "ingredient_match": need is not None, "unit_correct": unit,
                      "quantity_correct": amount, "date_correct": when,
                      "exact_amount_unit_date": need is not None and unit and bool(amount) and when,
                      "errors": (["omitted"] if need is None else
                                 (["unit"] if not unit else ["quantity"] if not amount else [])
                                 + ([] if when else ["date"]))})
    count = len(reference)
    exact = sum(line["exact_amount_unit_date"] for line in lines)
    extras = sorted(set(range(len(needs))) - used)
    return {**base, "reference_available": True, "expected_lines": count,
            "predicted_lines": len(needs), "matched_ingredient_lines": len(assignments),
            "ingredient_line_precision": len(assignments) / len(needs) if needs else 0.0,
            "ingredient_line_recall": len(assignments) / count if count else 0.0,
            "exact_amount_unit_date_lines": exact,
            "exact_line_fraction_of_expected": exact / count if count else 0.0,
            "omissions": [index for index in range(count) if index not in assignments],
            "extras": extras,
            "duplicate_lines": sum(index not in used for indices in predicted.values() for index in indices),
            "ambiguous_identity_lines": ambiguous,
            "quantity_error_lines": sum(line["ingredient_match"] and line["unit_correct"]
                                        and not line["quantity_correct"] for line in lines),
            "unit_error_lines": sum(line["ingredient_match"] and not line["unit_correct"] for line in lines),
            "critical_quantity_or_unit_error_lines": sum(line["ingredient_match"] and
                (not line["unit_correct"] or not line["quantity_correct"]) for line in lines),
            "date_error_lines": sum(line["ingredient_match"] and not line["date_correct"] for line in lines),
            "critical_error_scope": "Matched-line quantity/unit errors only; omissions/extras "
                                    "are separate, and zero errors does not mean food-safe",
            "lines": lines}


def verify_menu_draft(case: dict[str, Any], draft: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []

    def check(name: str, ok: bool, detail: str):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("draft.review_boundary", draft.get("status") == "draft"
          and draft.get("requires_review") is True and draft.get("requires_approval") is True
          and draft.get("source") == "model_estimate", "Editable estimate requires human review")
    expected = case["expect"]
    mandate = draft.get("mandate_constraints") or {}
    check("draft.mandate", mandate.get("excluded_allergens") == expected["excluded_allergens"]
          and mandate.get("exclude_may_contain") is expected["exclude_may_contain"],
          "Fixture mandate survives model output")
    needs = draft.get("needs") or []
    check("draft.nonempty", bool(needs), f"{len(needs)} ingredient rows")
    rows = expected.get("needs", [])
    matched = set()
    for index, row in enumerate(rows):
        aliases = {_normal(a) for a in [row["ingredient"], *row.get("aliases", [])]}
        matches = [(i, need) for i, need in enumerate(needs)
                   if any(_normal(str(need.get(field, ""))) in aliases
                          for field in ("ingredient", "search_hint_fi"))
                   and need.get("unit") == row["unit"] and need.get("date") == row["date"]]
        matched.update(i for i, _ in matches)
        amount = sum(float(need["amount"]) for _, need in matches)
        check(f"quantity.{index}", len(matches) == 1 and abs(amount - row["amount"]) <= 0.001,
              f"{row['ingredient']} on {row['date']}: {amount:g} {row['unit']}, "
              f"want {row['amount']:g}; exactly one consolidated row")
    if expected.get("exact_ingredients"):
        check("draft.exact_ingredients", matched == set(range(len(needs))),
              "Every row belongs to the supplied recipe and correct delivery batch")
    if expected.get("quality_requires_exact_lines"):
        metrics = measure_menu_lines(case, draft)
        check("quality.exact_reference_lines", metrics["exact_amount_unit_date_lines"]
              == metrics["expected_lines"] and not metrics["extras"],
              "All reference ingredient lines have the exact amount/unit/date, with no extras")
    if "needs_any" in expected:
        aliases = {_normal(a) for a in expected["needs_any"]}
        check("draft.ingredient_coverage", any(
            _normal(str(need.get(field, ""))) in aliases for need in needs
            for field in ("ingredient", "search_hint_fi")), "Identifiable macaroni ingredient present")
    if expected.get("information_gap"):
        # The generic mandatory warning is inserted by the harness; it is not evidence
        # that the model recognized missing recipe/stock facts.
        specific = draft.get("assumptions") or (draft.get("warnings") or [])[1:]
        check("draft.missing_facts", bool(specific),
              "Model states at least one assumption or missing fact beyond the harness warning")
        note_text = _normal(" ".join([*draft.get("assumptions", []),
                                     *(draft.get("warnings") or [])[1:]]))
        for topic, aliases in expected.get("gap_topics", {}).items():
            check(f"draft.gap_topic.{topic}", any(_normal(alias) in note_text for alias in aliases),
                  f"Explicit {topic} caveat: keyword coverage only, not semantic truth validation")
    return checks


class _Meter:
    """Count draft chat invocations and provider-reported usage, not SDK retry requests."""

    def __init__(self, inner: LLM):
        self.inner = inner
        self.name = getattr(inner, "name", "unknown")
        self.vision = bool(getattr(inner, "vision", False))
        self.calls = self.input_tokens = self.output_tokens = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        response = self.inner.chat(*args, **kwargs)
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        return response

    def usage(self):
        return {"draft_chat_calls": self.calls, "reported_input_tokens": self.input_tokens,
                "reported_output_tokens": self.output_tokens,
                "cost_usd": None, "scope": "reported usage only; SDK retries not individually counted"}


def evaluate_menu_case(case: dict[str, Any], llm: LLM, *,
                       imagebytes: bytes | None = None) -> dict[str, Any]:
    meter = _Meter(llm)
    result: dict[str, Any] = {"id": case["id"], "model": meter.name, "scope": case["scope"], "passed": False,
                              "status": "error", "checks": [], "draft": None,
                              "error_type": None,
                              "execution_boundary": "draft_menu only; no purchase executor supplied"}
    try:
        imagebytes = imagebytes if imagebytes is not None else load_menu_image(case)
        if imagebytes is not None:
            _validate_image_bytes(case, imagebytes)
            result["image_sha256"] = hashlib.sha256(imagebytes).hexdigest()
        result["modality"] = "image" if imagebytes is not None else "text"
        draft = draft_menu(meter, case["text"], case["guests"], _profile(case),
                           imagebytes=imagebytes,
                           mime=case.get("image", {}).get("mime"),
                           today=date.fromisoformat(case["today"]))
        result.update(status="scored", draft=draft, checks=verify_menu_draft(case, draft))
        result["passed"] = all(c["ok"] for c in result["checks"])
    except Exception as error:
        # Provider and parser errors can contain credentials or request fragments.
        result["error_type"] = type(error).__name__
        result["error"] = "Draft extraction failed; sensitive diagnostics omitted"
    result["usage"] = meter.usage()
    result["line_metrics"] = measure_menu_lines(case, result.get("draft") or {})
    return result


def run_menu_evaluation(cases: list[dict[str, Any]], llm_factory: Callable[[], LLM], *,
                        output: Path | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Keep every attempted case; optional unique finalized archive records real replies."""
    versions = {case["suite_version"] for case in cases}
    if len(versions) != 1:
        raise ValueError("evaluate one explicit menu suite version per run")
    suite_version = next(iter(versions))
    archive = RunArchive(output, run_id) if output is not None else None
    if archive:
        archive.write_json("inputs.json", {"suite": copy.deepcopy(cases),
            "source": source_identity(BENCH_DIR.parent),
            "scope": "menu drafts only; not procurement or predictive accuracy"})
    results = []
    for case in cases:
        recording = None
        try:
            imagebytes = load_menu_image(case)
            if archive and imagebytes is not None:
                extension = "jpeg" if case["image"]["mime"] == "image/jpeg" else "png"
                asset = archive.staging_path / "images" / f"case-{len(results) + 1:02d}.{extension}"
                asset.parent.mkdir(exist_ok=True)
                with asset.open("xb") as image_output:
                    image_output.write(imagebytes)
            llm = llm_factory()
            if archive:
                # Paths are indexed, not controlled by fixture IDs.
                recording = ReplayLLM(llm, archive.staging_path / "replies" /
                                      f"case-{len(results) + 1:02d}.json", mode="record")
                llm = recording
            result = evaluate_menu_case(case, llm, imagebytes=imagebytes)
        except Exception as error:
            result = {"id": case["id"], "scope": case["scope"], "passed": False,
                      "status": "error", "error_type": type(error).__name__,
                      "error": "Model or fixture initialization failed; sensitive diagnostics omitted",
                      "checks": [], "draft": None}
        finally:
            if recording is not None:
                recording.save()
        if "line_metrics" not in result:
            result["line_metrics"] = measure_menu_lines(case, {})
        results.append(result)
        if archive:
            archive.append_event({"event": "menu_case_finished", **result})
    attempted = len(results)
    scored = sum(r["status"] == "scored" for r in results)
    measured = [result["line_metrics"] for result in results
                if result["line_metrics"]["reference_available"]]
    reference_lines = sum(metrics["expected_lines"] for metrics in measured)
    predicted_lines = sum(metrics["predicted_lines"] for metrics in measured)
    matched_lines = sum(metrics["matched_ingredient_lines"] for metrics in measured)
    exact_lines = sum(metrics["exact_amount_unit_date_lines"] for metrics in measured)
    quality = {"reference_cases": len(measured), "expected_lines": reference_lines,
               "predicted_lines": predicted_lines, "matched_ingredient_lines": matched_lines,
               "ingredient_line_precision": matched_lines / predicted_lines if predicted_lines else 0.0,
               "ingredient_line_recall": matched_lines / reference_lines if reference_lines else 0.0,
               "exact_amount_unit_date_lines": exact_lines,
               "exact_line_fraction_of_expected": exact_lines / reference_lines if reference_lines else 0.0,
               "omission_lines": sum(len(metrics["omissions"]) for metrics in measured),
               "extra_lines": sum(len(metrics["extras"]) for metrics in measured),
               "critical_quantity_or_unit_error_lines": sum(
                   metrics["critical_quantity_or_unit_error_lines"] for metrics in measured),
               "date_error_lines": sum(metrics["date_error_lines"] for metrics in measured),
               "review_boundary_cases": sum(result["line_metrics"]["review_boundary_present"]
                                            for result in results),
               "scope": "Reference-line extraction only, errors remain in recall denominator; "
                        "not recipe validity, allergen-safe product selection or purchasing accuracy"}
    report = {"suite": suite_version, "quality": quality,
              "boundary": "ingredient drafts, never order completion",
              "attempted": attempted, "scored": scored, "errors": attempted - scored,
              "passed": sum(r["passed"] for r in results),
              "coverage": scored / attempted if attempted else 0.0,
              "pass_fraction_of_attempts": sum(r["passed"] for r in results) / attempted
              if attempted else 0.0, "results": results,
              "claim": "Synthetic trusted-input arithmetic and review protocol; not recipe safety, "
                       "nutrition, forecasting or production procurement accuracy"}
    if archive:
        archive.write_json("report.json", report)
        report["archive"] = str(archive.finalize(status="error" if attempted != scored else "completed"))
    return report
