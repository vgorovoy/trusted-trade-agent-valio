"""Record small, fair comparisons of the complete buyer agent and gateway harness.

No model calls on import. Run with explicit provider:model arguments; credentials are
handled only by get_llm through environment/.env. Archives never collect credentials.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from trade_agents.bench import load_scenarios, run_bench
from trade_agents.bench.records import RunArchive, source_identity, verify_run
from trade_agents.bench.scenario import BENCH_DIR, Scenario
from trade_agents.llm import get_llm

DEFAULT_CASES = ["basic-butter-fi", "pref-reliability", "horeca-school-traces-no-safe-stock",
                 "horeca-hospital-traces-in-meat"]
PROJECT = Path(__file__).resolve().parents[1]


def model_spec(value: str) -> tuple[str, str]:
    provider, separator, model = value.partition(":")
    if not separator or not provider or not model or any(c.isspace() for c in value):
        raise ValueError("models must be explicit provider:model pairs")
    return provider, model


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    """SDK errors may already have been caught by BuyerAgent, not this script.

    Strip their free-form text at every archive/output boundary, preserving the
    status, skip category, and structured exception type if supplied by the runner.
    """
    result = copy.deepcopy(result)
    if result.get("skip_kind") == "provider" or result.get("status") == "error":
        category = "provider" if result.get("skip_kind") == "provider" else "agent"
        result["error_category"] = category
        if result.get("skipped") is not None:
            result["skipped"] = f"{category} request failed; sensitive diagnostics omitted"
        result["explanation"] = f"{category} request failed; sensitive diagnostics omitted"
        if result.get("error") is not None:
            result["error"] = result["explanation"]
    return result


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    report = copy.deepcopy(report)
    report["results"] = [_public_result(result) for result in report["results"]]
    return report


def comparison_summary(runs: list[dict[str, Any]], scenario_ids: list[str]) -> dict[str, Any]:
    """Preserve failures, skips and errors; never rank only successful completed subsets."""
    models = []
    for run in runs:
        report = run.get("report")
        if report is not None:
            report = _public_report(report)
        entry = {"requested_model": run["requested_model"], "archive": run["archive"],
                 "run_status": run["status"], "error_type": run.get("error_type")}
        if report is not None:
            entry.update({key: report[key] for key in (
                "model", "accuracy", "passed", "scored", "skipped", "coverage", "usage",
                "safety", "outcome_breakdown", "do_nothing_baseline")})
            results = {row["id"]: row for row in report["results"]}
            entry["cases"] = [{"id": sid,
                               "outcome": ("missing" if sid not in results else
                                           "skipped" if results[sid]["skipped"] else
                                           "error" if results[sid]["status"] == "error" else
                                           "passed" if results[sid]["passed"] else "failed"),
                               "status": results.get(sid, {}).get("status"),
                               "failed_checks": [c["name"] for c in results.get(sid, {}).get("checks", [])
                                                 if not c["ok"]],
                               "skip_reason": results.get(sid, {}).get("skipped")}
                              for sid in scenario_ids]
        else:
            entry["cases"] = [{"id": sid, "outcome": "run_error"} for sid in scenario_ids]
        models.append(entry)
    return {"scenario_ids": scenario_ids, "models": models,
            "claim": "Single-trial diagnostic comparison, not a statistically significant ranking. "
                     "Task utility and safe resolution are scored separately from limited safety checks."}


def compare_models(specs: list[str], scenarios: list[Scenario], *, output: Path = BENCH_DIR / "runs",
                   comparison_id: str | None = None, temperature: float | None = None,
                   llm_builder=None, progress=None) -> Path:
    """Run identical synthetic scenarios from scratch, always recording genuine replies."""
    if len(specs) < 2:
        raise ValueError("compare at least two explicit models")
    parsed = [model_spec(spec) for spec in specs]
    if not scenarios or len({s.id for s in scenarios}) != len(scenarios):
        raise ValueError("comparison needs nonempty, unique scenario ids")
    if any(s.world.name == "live" for s in scenarios):
        raise ValueError("this comparison runner supports frozen synthetic worlds only")
    if temperature is not None and not math.isfinite(temperature):
        raise ValueError("temperature must be finite")
    llm_builder = llm_builder or get_llm
    archive = RunArchive(output, comparison_id)
    identity = source_identity(PROJECT)
    snapshot = [asdict(sc) for sc in scenarios]
    common = {"source": identity, "suite": snapshot,
              "budget": {"agent_max_steps": 14, "agent_max_tokens": 200_000},
              "generation": {"requested_temperature": temperature,
                             "other_parameters": "provider defaults; adapter may adjust temperature"},
              "replay_mode": "record", "evaluation": "whole buyer agent plus in-process gateway tools"}
    archive.write_json("inputs.json", common)
    runs = []
    interrupted = False
    for index, (spec, (provider, model_name)) in enumerate(zip(specs, parsed, strict=True)):
        run = RunArchive(archive.staging_path / "models", f"model-{index + 1:02d}")
        run.write_json("inputs.json", {**common, "provider": provider, "requested_model": model_name})
        status, error_type, report = "completed", None, None
        try:
            if source_identity(PROJECT)["code_sha256"] != identity["code_sha256"]:
                raise RuntimeError("source code changed during comparison")
            configurations, adapters = [], []

            def factory(provider=provider, model_name=model_name, configurations=configurations,
                        adapters=adapters):
                llm = llm_builder(provider, model_name)
                if temperature is not None:
                    if not hasattr(llm, "temperature"):
                        raise ValueError("adapter does not support temperature")
                    llm.temperature = temperature
                config = {"name": llm.name, "initial_temperature": getattr(llm, "temperature", None),
                          "vision_supported": bool(getattr(llm, "vision", False))}
                configurations.append(config)
                adapters.append(llm)
                return llm

            def on_trace(scenario, step, run=run, spec=spec):
                event = {"event": "tool_completed", "scenario_id": scenario.id, **asdict(step)}
                if step.error:
                    event["result"] = "Tool failed; sensitive diagnostics omitted"
                run.append_event(event)
                if progress:
                    progress({"model": spec, **event})

            def on_finished(result, run=run):
                public = _public_result(asdict(result))
                run.append_event({"event": "scenario_finished", "scenario_id": public["id"],
                                  "passed": public["passed"], "skipped": public["skipped"],
                                  "status": public["status"], "skip_kind": public["skip_kind"],
                                  "error_category": public.get("error_category"),
                                  "error_type": public.get("error_type")})

            report = _public_report(run_bench(
                copy.deepcopy(scenarios), factory, save=False, replay="record", bench_dir=run.staging_path,
                on_start=lambda sc, run=run: run.append_event(
                    {"event": "scenario_started", "scenario_id": sc.id}),
                on_trace=on_trace,
                progress=on_finished,
            ).to_dict())
            run.write_json("report.json", report)
            run.write_json("generation.json", {"adapter_configurations": [
                {**config, "final_temperature": getattr(adapter, "temperature", None)}
                for config, adapter in zip(configurations, adapters, strict=True)]})
            if source_identity(PROJECT)["code_sha256"] != identity["code_sha256"]:
                raise RuntimeError("source code changed during comparison")
        except (Exception, KeyboardInterrupt) as error:
            status, error_type = "error", type(error).__name__
            # Raw SDK errors can include URLs or credentials: retain the exception class,
            # never repr(error), environment snapshots, or SDK client internals.
            run.write_json("error.json", {"error_type": error_type,
                                           "message": "Run did not complete; inspect provider diagnostics."})
            interrupted = isinstance(error, KeyboardInterrupt)
        path = run.finalize(status=status)
        runs.append({"requested_model": spec, "archive": str(path.relative_to(archive.staging_path)),
                     "status": status, "error_type": error_type, "report": report})
        if interrupted:
            break
    summary = comparison_summary(runs, [sc.id for sc in scenarios])
    archive.write_json("summary.json", summary)
    archive.append_event({"event": "comparison_finished", "models_attempted": len(runs),
                          "requested_models": len(specs)})
    return archive.finalize(status="error" if interrupted or any(r["status"] == "error" for r in runs)
                            else "completed")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", required=True, help="explicit provider:model; repeat")
    parser.add_argument("--only", nargs="+", default=DEFAULT_CASES, help="scenario ids/categories")
    parser.add_argument("--output", type=Path, default=BENCH_DIR / "runs")
    parser.add_argument("--comparison-id")
    parser.add_argument("--temperature", type=float, help="same requested temperature for each adapter")
    args = parser.parse_args(argv)
    def progress(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    path = compare_models(args.model, load_scenarios(only=args.only), output=args.output,
                          comparison_id=args.comparison_id, temperature=args.temperature, progress=progress)
    print(f"Archive: {path}", flush=True)
    if verify_run(path)["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
