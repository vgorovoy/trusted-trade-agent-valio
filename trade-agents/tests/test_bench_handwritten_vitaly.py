"""Internal JPEG byte integrity and scripted oracle controls, not live model quality."""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from trade_agents.bench.menu_eval import (
    MENU_DIR,
    evaluate_menu_case,
    load_menu_cases,
    load_menu_image,
    run_menu_evaluation,
)
from trade_agents.llm import LLMResponse, ScriptedLLM

DIRECTORY = MENU_DIR / "handwritten-vitaly-v1"


def fixture():
    return load_menu_cases(DIRECTORY)[0]


def response(case):
    return {"needs": [{key: value for key, value in row.items() if key != "aliases"}
                      for row in case["expect"]["needs"]],
            "assumptions": ["Delivery date supplied separately; chef review required."]}


def scripted(payload):
    return ScriptedLLM([LLMResponse(text=json.dumps(payload))], vision=True,
                       name="scripted:internal-photo-control")


def test_original_jpeg_preserved_and_internal_only():
    case = fixture()
    data = load_menu_image(case)
    root = Path(__file__).resolve().parents[2]
    assert data == (root / case["provenance"]["source_repository_path"]).read_bytes()
    assert data.startswith(b"\xff\xd8\xff")
    assert hashlib.sha256(data).hexdigest() == case["image"]["sha256"]
    assert case["provenance"]["internal_storage_authorized"] is True
    assert case["provenance"]["public_redistribution_authorized"] is False


def test_independent_pack_arithmetic_and_external_date():
    case = fixture()
    assert [Decimal(str(row["amount"])) for row in case["expect"]["needs"]] == [
        Decimal(500) / 1000 * 10, Decimal(1) * 6, Decimal(10) * 1,
        Decimal(5), Decimal("2.5") * 3,
    ]
    assert {row["date"] for row in case["expect"]["needs"]} == {"2026-11-16"}
    assert "photograph contains NO delivery date" in case["text"]
    assert "500" not in case["text"] and "7.5" not in case["text"]
    assert case["guests"] == 1 and case["buyer"] == "restaurant"


def test_jpeg_reaches_vision_model_without_gateway(monkeypatch):
    from trade_agents.service import Gateway
    case = fixture()
    seen = []

    class InspectLLM:
        name = "scripted:jpeg-inspection"
        vision = True

        def chat(self, messages, **kwargs):
            data, mime = messages[0]["images"][0]
            assert mime == "image/jpeg" and data == load_menu_image(case)
            seen.append(True)
            return LLMResponse(text=json.dumps(response(case)))

    def forbidden(*args, **kwargs):
        raise AssertionError("Draft extraction must not create a gateway")

    monkeypatch.setattr(Gateway, "__init__", forbidden)
    result = evaluate_menu_case(case, InspectLLM())
    assert seen and result["passed"]
    assert result["draft"]["status"] == "draft"
    assert result["draft"]["requires_review"] and result["draft"]["requires_approval"]


@pytest.mark.parametrize("row,wrong", [(0, 10), (0, 0.5), (2, 1), (4, 3), (4, 2.5)])
def test_wrong_pack_count_as_total_rejected(row, wrong):
    case = fixture()
    payload = response(case)
    payload["needs"][row]["amount"] = wrong
    assert not evaluate_menu_case(case, scripted(payload))["passed"]


@pytest.mark.parametrize("mutation", ["mime", "hash", "bytes"])
def test_mismatched_jpeg_rejected_before_model_call(mutation):
    case = copy.deepcopy(fixture())
    data = load_menu_image(case)
    if mutation == "mime":
        case["image"]["mime"] = "image/png"
    elif mutation == "hash":
        case["image"]["sha256"] = "0" * 64
    else:
        data = b"not an image"
        case["image"]["sha256"] = hashlib.sha256(data).hexdigest()
    result = evaluate_menu_case(case, scripted(response(case)), imagebytes=data)
    assert result["status"] == "error" and result["usage"]["draft_chat_calls"] == 0


def test_jpeg_archive_preserves_extension_and_bytes(tmp_path):
    case = fixture()
    report = run_menu_evaluation([case], lambda: scripted(response(case)),
                                 output=tmp_path, run_id="internal-jpeg-control")
    archive = Path(report["archive"])
    assert report["passed"] == 1
    assert (archive / "images/case-01.jpeg").read_bytes() == load_menu_image(case)
    assert not (archive / "images/case-01.png").exists()


def test_original_missing_date_and_note_reference_remain_gated():
    spec = json.loads((DIRECTORY / "specifications/02-original-missing-date.json").read_text())
    reference = spec["visual_reference"]
    assert reference["delivery_date"] is None
    assert reference["fresh_note_applies_to"] == "potato"
    notes = [row for row in reference["rows"] if "note_text" in row]
    assert [(row["ingredient"], row["note_text"]) for row in notes] == [("potato", "tuore")]
    assert spec["exact_oracle"]["delivery_date"] is None
    assert spec["exact_oracle"]["orders_created"] == 0
    assert spec["capability_gate"]["included_in_executable_suite"] is False
    assert spec["capability_gate"]["score"] is None
    assert len(load_menu_cases(DIRECTORY)) == 1

pytestmark = pytest.mark.skipif(
    not DIRECTORY.exists(),
    reason="Private fixture assets are intentionally excluded from the public snapshot",
)
