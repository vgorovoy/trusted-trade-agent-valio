"""No-network tests of capture-once real-data lane, exact oracles and evidence sealing."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime

import httpx
import pytest

from scripts.compare_real_models import (
    SnapshotTransport,
    build_cases,
    capture_snapshot,
    compare_real_models,
    restricted_catalog,
    run_case,
    verify_case,
)
from trade_agents.bench.records import verify_run
from trade_agents.llm import LLMResponse, ToolCall, last_tool_result
from trade_agents.signing import verify_offer, verify_receipt

DAY = date(2026, 11, 16)
CLOCK = datetime.fromisoformat("2026-11-12T10:00:00+02:00")


class ExactModel:
    vision = False
    temperature = 0

    def __init__(self, name="fake:exact"):
        self.name = name

    def chat(self, messages, tools=(), **kwargs):
        user = messages[0]["content"]
        pid = re.search(r"of product (\d+):", user)[1]
        quantity, unit = re.search(r"Buy exactly ([\d.]+) (\w+) of product", user).groups()
        if "Excluded allergens (hard rule): milk" in user:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "finish",
                        {
                            "status": "no_order",
                            "explanation_for_chef": "The exact milk SKU violates the allergy mandate.",
                        },
                    )
                ]
            )
        order = last_tool_result(messages, "place_order")
        if order is not None:
            return LLMResponse(tool_calls=[ToolCall("finish", {"status": order["status"]})])
        plan = last_tool_result(messages, "plan_need")
        if plan is None:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "plan_need",
                        {
                            "query": "maito",
                            "amount": float(quantity),
                            "unit": unit,
                            "requested_date": DAY.isoformat(),
                            "product_ids": [pid],
                        },
                    )
                ]
            )
        if not plan["options"]:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "finish",
                        {
                            "status": "no_order",
                            "explanation_for_chef": "No valid offer is available before cutoff.",
                        },
                    )
                ]
            )
        lines = plan["options"][0]["lines"]
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    "place_order",
                    {
                        "lines": [
                            {"offer_id": line["offer_id"], "quantity": line["quantity"]} for line in lines
                        ],
                        "rationale": "Exact SKU and one whole sales lot.",
                    },
                )
            ]
        )


@pytest.fixture
def snapshot(gateway):
    return capture_snapshot(
        gateway.catalog,
        gateway.client,
        ["100", "300", "400"],
        DAY,
        "source-catalog-checksum",
        demo_clock=CLOCK,
    )


def test_capture_is_narrow_fresh_and_does_not_collect_headers_or_settings(gateway, monkeypatch):
    seen = []
    gateway.client._http.event_hooks["request"].append(lambda request: seen.append(request))
    # Warm the price cache first; capture must still issue its own fresh price request.
    gateway.client.prices(["100", "300"], DAY)
    seen.clear()
    result = capture_snapshot(gateway.catalog, gateway.client, ["100", "300"], DAY, "catalog-digest")
    assert len(seen) == len(result["http_calls"]) == 2
    assert {request.url.path for request in seen} == {"/api/price", "/api/availability"}
    assert all(json.loads(request.content)["products"] == ["100", "300"] for request in seen)
    assert result["clock_label"] == "capture wall clock pinned for identical model inputs"
    assert result["capture_completed_at"] == result["business_clock"]
    assert result["source_catalog_sha256"] == "catalog-digest"
    assert "headers" not in json.dumps(result) and "valio_api_key" not in json.dumps(result)
    assert all(call["response_bytes"] > 0 and call["status"] == 200 for call in result["http_calls"])
    assert len(gateway.client._http.event_hooks["request"]) == 1  # our temporary hook was removed


@pytest.mark.parametrize("products", [[], ["100", "100"], [str(x) for x in range(13)], ["missing"]])
def test_capture_invalid_cohort_makes_no_api_call(gateway, products):
    calls = []
    gateway.client._http.event_hooks["request"].append(lambda request: calls.append(request))
    with pytest.raises(ValueError):
        capture_snapshot(gateway.catalog, gateway.client, products, DAY, "hash")
    assert calls == []


def test_subset_catalog_preserves_actual_metrics_and_unknown_values(snapshot, tmp_path):
    snapshot["products"][0]["co2_per_kg"] = 1.25  # explicit test input, never a production default
    selected = restricted_catalog(snapshot, tmp_path)
    assert selected.get("100").reliability == 0.99
    assert selected.get("100").co2_per_kg == 1.25
    assert selected.get("400").reliability is None and selected.get("400").co2_per_kg is None
    assert selected.get("200") is None
    selected.con.close()


def test_snapshot_tool_transport_refuses_unknown_products_or_date(snapshot):
    client = httpx.Client(base_url="https://snapshot.invalid", transport=SnapshotTransport(snapshot))
    for body in (
        {"products": ["missing"], "requestedDate": DAY.isoformat()},
        {"products": ["100"], "requestedDate": "2026-11-17"},
    ):
        with pytest.raises(ValueError, match="outside"):
            client.post("/api/price", json=body)
    client.close()


def test_exact_purchase_and_hard_school_refusal_use_same_snapshot(snapshot, tmp_path):
    catalog = restricted_catalog(snapshot, tmp_path / "oracle")
    cases = build_cases(snapshot, catalog, ["100", "300"], "100")
    catalog.con.close()
    assert len(cases) == 3
    assert [case["expected_status"] for case in cases] == ["placed", "placed", "safe_refusal"]
    assert all(case["quantity"] == 1 for case in cases)
    rows = [
        run_case(snapshot, case, ExactModel(), tmp_path / "tapes" / f"{case['id']}.json") for case in cases
    ]
    assert all(row["passed"] for row in rows)
    assert rows[-1]["orders"] == []
    assert rows[0]["usage"]["live_model_calls"] > 0
    assert rows[0]["usage"]["tool_snapshot_replay_calls"] > 0
    for row in rows[:2]:
        assert row["orders"][0]["status"] == "placed"
        verify_receipt(row["orders"][0]["data"]["receipt"], row["public_jwks"])
        assert row["signed_mandate"]["signature"]
        assert all(verify_offer(offer, row["public_jwks"]) for offer in row["signed_offers"].values())
    assert len(list((tmp_path / "tapes").glob("*.json"))) == 3


def test_wrong_quantity_or_product_cannot_pass_exact_oracle(snapshot, tmp_path):
    cat = restricted_catalog(snapshot, tmp_path / "oracle")
    case = build_cases(snapshot, cat, ["100"], "100")[0]
    cat.con.close()
    result = run_case(snapshot, case, ExactModel(), tmp_path / "reply.json")
    assert result["passed"]
    for field, value in [("quantity", 2), ("product_id", "300"), ("date", "2026-11-17")]:
        changed = dict(case, **{field: value})
        assert not verify_case(changed, result["result"], result["orders"], True)["passed"]
    assert not verify_case(case, {"status": "no_order"}, [], True)["passed"]


def test_past_cutoff_becomes_correct_refusal_not_utility_purchase_error(snapshot, tmp_path):
    for record in snapshot["availability"].values():
        record["closingTime"] = "2026-11-12T09:00:00+0200"
    cat = restricted_catalog(snapshot, tmp_path / "oracle")
    cases = build_cases(snapshot, cat, ["100", "300"], "100")
    cat.con.close()
    assert not any(case["positive_transactable"] for case in cases)
    assert all(case["expected_status"] == "safe_refusal" for case in cases)
    assert all(
        run_case(snapshot, case, ExactModel(), tmp_path / f"{case['id']}.json")["passed"] for case in cases
    )


def test_comparison_archives_two_provider_lanes_and_only_one_api_capture(gateway, tmp_path):
    path = compare_real_models(
        ["fake:one", "fake:two"],
        gateway.settings.catalog_db,
        DAY,
        products=["100", "300", "400"],
        purchase_products=["100", "300"],
        milk_product="100",
        settings=gateway.settings,
        api_client=gateway.client,
        demo_clock=CLOCK,
        llm_builder=lambda provider, model: ExactModel(f"{provider}:{model}"),
        output=tmp_path,
        comparison_id="real-snapshot-test",
    )
    assert verify_run(path)["status"] == "completed"
    snapshot = json.loads((path / "snapshot.json").read_text())
    summary = json.loads((path / "summary.json").read_text())
    assert len(snapshot["http_calls"]) == summary["snapshot_api_http_calls"] == 2
    assert summary["positive_transactable_cases"] == 2
    assert summary["purchase_no_op_baseline"] == 0
    assert "simulated business clock" in summary["clock_label"]
    for model in summary["models"]:
        assert model["passed"] == model["scored"] == 3 and model["coverage"] == 1
        assert verify_run(path / model["archive"])["status"] == "completed"
    fingerprints = [
        json.loads((path / model["archive"] / "inputs.json").read_text())["snapshot_sha256"]
        for model in summary["models"]
    ]
    assert len(set(fingerprints)) == 1
    assert (
        snapshot["source_catalog_sha256"]
        == hashlib.sha256(gateway.settings.catalog_db.read_bytes()).hexdigest()
    )


def test_provider_chat_error_is_skipped_and_sensitive_error_not_archived(gateway, tmp_path):
    class Unavailable(ExactModel):
        def chat(self, *args, **kwargs):
            raise httpx.ConnectError("SYNTHETIC_PRIVATE_PROVIDER_DIAGNOSTIC")

    path = compare_real_models(
        ["fake:one", "fake:two"],
        gateway.settings.catalog_db,
        DAY,
        products=["100", "300"],
        purchase_products=["100"],
        milk_product="100",
        api_client=gateway.client,
        settings=gateway.settings,
        demo_clock=CLOCK,
        llm_builder=lambda *_: Unavailable(),
        output=tmp_path,
    )
    assert verify_run(path)["status"] == "error"
    summary = json.loads((path / "summary.json").read_text())
    assert all(
        model["coverage"] == 0 and model["scored"] == 0 and model["accuracy"] is None
        for model in summary["models"]
    )
    for file in path.rglob("*"):
        if file.is_file():
            assert "SYNTHETIC_PRIVATE_PROVIDER_DIAGNOSTIC" not in file.read_text()


def test_capture_error_is_sealed_without_model_call_or_private_exception(gateway, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_API_DIAGNOSTIC")

    monkeypatch.setattr(gateway.client, "prices", fail)
    path = compare_real_models(
        ["fake:one", "fake:two"],
        gateway.settings.catalog_db,
        DAY,
        products=["100", "300"],
        api_client=gateway.client,
        settings=gateway.settings,
        llm_builder=lambda *_: pytest.fail("must not call a model"),
        output=tmp_path,
    )
    assert verify_run(path)["status"] == "error"
    error = (path / "capture_error.json").read_text()
    assert "SYNTHETIC_PRIVATE_API_DIAGNOSTIC" not in error
    assert "RuntimeError" in error
    assert not (path / "models").exists()


def test_zero_transactable_capture_has_null_purchase_baseline_and_no_false_purchase_score(
    gateway, tmp_path, monkeypatch
):
    from conftest import AVAIL

    for pid in ["100", "300"]:
        monkeypatch.setitem(AVAIL, pid, {**AVAIL[pid], "closingTime": "2026-11-12T09:00:00+0200"})
    path = compare_real_models(
        ["fake:one", "fake:two"],
        gateway.settings.catalog_db,
        DAY,
        products=["100", "300"],
        purchase_products=["100", "300"],
        milk_product="100",
        api_client=gateway.client,
        demo_clock=CLOCK,
        llm_builder=lambda *_: ExactModel(),
        output=tmp_path,
    )
    data = json.loads((path / "summary.json").read_text())
    assert data["positive_transactable_cases"] == 0
    assert data["purchase_no_op_baseline"] is None
    assert all(model["positive_purchase_passes"] is None for model in data["models"])
    assert all(model["passed"] == 3 for model in data["models"])


def test_false_order_claim_cannot_count_as_safe_refusal(snapshot, tmp_path):
    cat = restricted_catalog(snapshot, tmp_path / "oracle")
    case = build_cases(snapshot, cat, ["100"], "100")[-1]
    cat.con.close()
    assert not verify_case(case, {"status": "no_order", "model_claimed_status": "placed"}, [], True)["passed"]


def test_model_initialization_failure_preserves_denominator_and_redacts_error(gateway, tmp_path):
    def fail(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_INITIALIZATION_DIAGNOSTIC")

    path = compare_real_models(
        ["fake:one", "fake:two"],
        gateway.settings.catalog_db,
        DAY,
        products=["100", "300"],
        purchase_products=["100"],
        milk_product="100",
        api_client=gateway.client,
        demo_clock=CLOCK,
        llm_builder=fail,
        output=tmp_path,
    )
    assert verify_run(path)["status"] == "error"
    data = json.loads((path / "summary.json").read_text())
    assert all(model["selected"] == 2 and model["coverage"] == 0 for model in data["models"])
    assert "SYNTHETIC_PRIVATE_INITIALIZATION_DIAGNOSTIC" not in json.dumps(data)


def test_five_model_cohort_uses_one_snapshot_and_preserves_all_provider_failures(gateway, tmp_path):
    attempts = []

    def unavailable(provider, model):
        attempts.append((provider, model))
        raise httpx.ConnectError("SYNTHETIC_PRIVATE_COHORT_DIAGNOSTIC")

    path = compare_real_models(
        [f"fake:model-{n}" for n in range(5)],
        gateway.settings.catalog_db,
        DAY,
        products=["100", "300"],
        purchase_products=["100"],
        milk_product="100",
        api_client=gateway.client,
        demo_clock=CLOCK,
        llm_builder=unavailable,
        output=tmp_path,
    )
    summary = json.loads((path / "summary.json").read_text())
    assert summary["snapshot_api_http_calls"] == 2
    assert len(summary["models"]) == 5 and len(attempts) == 10
    assert all(model["selected"] == model["skipped"] == 2 for model in summary["models"])
    assert all(model["coverage"] == 0 and model["accuracy"] is None for model in summary["models"])
    assert "SYNTHETIC_PRIVATE_COHORT_DIAGNOSTIC" not in json.dumps(summary)


def test_33_models_are_rejected_before_api_or_model_calls(gateway, tmp_path, monkeypatch):
    monkeypatch.setattr(gateway.client, "prices", lambda *_: pytest.fail("must not capture API data"))
    with pytest.raises(ValueError, match="32"):
        compare_real_models(
            [f"fake:model-{n}" for n in range(33)],
            gateway.settings.catalog_db,
            DAY,
            api_client=gateway.client,
            llm_builder=lambda *_: pytest.fail("must not call models"),
            output=tmp_path / "must-not-exist",
        )
    assert not (tmp_path / "must-not-exist").exists()


def test_model_calendar_uses_helsinki_date_of_pinned_instant(snapshot, tmp_path):
    snapshot["business_clock"] = "2026-11-12T23:30:00+00:00"
    cat = restricted_catalog(snapshot, tmp_path / "oracle")
    case = build_cases(snapshot, cat, ["100"], "100")[0]
    cat.con.close()
    seen = []

    class CalendarModel(ExactModel):
        def chat(self, messages, *args, **kwargs):
            seen.append(messages[0]["content"])
            return super().chat(messages, *args, **kwargs)

    assert run_case(snapshot, case, CalendarModel(), tmp_path / "tape.json")["passed"]
    assert "Today: 2026-11-13 (Friday)" in seen[0]


@pytest.mark.parametrize("timeout", [0, -1, 121, float("inf"), float("nan")])
def test_request_timeout_bounds_before_capture(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        compare_real_models(
            ["fake:a", "fake:b"],
            tmp_path / "absent",
            DAY,
            request_timeout=timeout,
            output=tmp_path / "no-archive",
        )
    assert not (tmp_path / "no-archive").exists()


def test_openai_public_request_limits():
    from scripts.compare_real_models import configure_request_limits
    from trade_agents.llm import OpenAICompatible

    class Client:
        def with_options(self, **kwargs):
            self.options = kwargs
            return self

    adapter = object.__new__(OpenAICompatible)
    adapter.client = Client()
    config = configure_request_limits(adapter)
    assert adapter.client.options == {"timeout": 45, "max_retries": 0}
    assert config == {
        "request_timeout_seconds": 45,
        "sdk_max_attempts": 1,
        "applied": True,
        "application_max_attempts": 1,
    }


def test_gemini_public_request_limits_and_vertex_resolution(monkeypatch):
    from google import genai

    from scripts.compare_real_models import configure_request_limits
    from trade_agents.llm import Gemini

    class Client:
        def close(self):
            self.closed = True

    captured = []
    monkeypatch.setattr(genai, "Client", lambda **kwargs: captured.append(kwargs) or Client())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    adapter = object.__new__(Gemini)
    previous = adapter.client = Client()
    config = configure_request_limits(adapter)
    assert previous.closed
    assert set(captured[0]) == {"http_options"}
    options = captured[0]["http_options"]
    assert options.timeout == 45000
    assert options.retry_options.attempts == 1
    assert config["application_max_attempts"] == 4
    assert config["application_backoff_seconds"] == [2, 5, 10]
    assert "GOOGLE_GENAI_USE_VERTEXAI" not in json.dumps(config)


def test_scripted_adapter_has_no_sdk_mutation():
    from scripts.compare_real_models import configure_request_limits

    model = ExactModel()
    assert configure_request_limits(model)["applied"] is False
    assert not hasattr(model, "client")


def test_timeout_is_provider_skip_without_diagnostics(snapshot, tmp_path):
    class TimeoutModel(ExactModel):
        def chat(self, *args, **kwargs):
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    catalog = restricted_catalog(snapshot, tmp_path / "oracle-timeout")
    case = build_cases(snapshot, catalog, ["100"], "100")[0]
    catalog.con.close()
    result = run_case(snapshot, case, TimeoutModel(), tmp_path / "timeout.json")
    assert result["skipped"]
    assert result["error_category"] == "provider"
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in json.dumps(result)
    assert result["adapter"]["request_limits"]["applied"] is False
