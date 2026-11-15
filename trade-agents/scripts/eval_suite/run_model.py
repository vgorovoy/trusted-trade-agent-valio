"""Run one provider through a pinned evaluation suite (N=1 per case) and archive the result.

    python scripts/eval_suite/run_model.py --pin-dir /tmp/tta-pin-full-v1 --suite full-v1 \
        --provider gemini --run-tag 20261114-n1

Use the interpreter of a synced project venv (uv sync). Run one process per provider; they are
independent. Credentials are read from --env-file into this process only. The synthetic lane
never calls the Valio API. Each case runs in a fresh spawned process with a wall-clock limit;
timeouts and crashes are recorded as unscored attempts, never dropped.

Output: <runs-dir>/<suite>/<provider>-<suite>-<run-tag>/ (immutable RunArchive, verified on
finalize). While running, progress lives in the archive's hidden .partial staging folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
CREDENTIALS = {
    "gemini": "GEMINI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "verda-nemotron": "VERDA_INFERENCE_KEY",
    "verda": "VERDA_INFERENCE_API_KEY",
    "verda-deepseek": "VERDA_INFERENCE_API_KEY",
}
# OpenAI-compatible endpoints that may be missing from an older pinned live_models.py.
EXTRA_PROVIDERS = {
    "verda-deepseek": (
        "Verda · DeepSeek",
        "deepseek-4-1-flash-1017",
        ("VERDA_INFERENCE_API_KEY",),
        "https://inference.verda.com/v1",
    ),
}

# Spawned case workers re-import this module, so the pinned source path travels via the env.
if os.environ.get("TTA_SUITE_PIN"):
    _pin = Path(os.environ["TTA_SUITE_PIN"])
    sys.path[:0] = [str(_pin), str(_pin / "src")]


RETRYABLE = (429, 500, 502, 503, 504)


def _status(error: BaseException) -> int | None:
    """bounded_model sanitises errors with `raise ... from None`; the original stays in __context__."""
    for candidate in (error, error.__context__, error.__cause__):
        code = getattr(candidate, "status_code", None) or getattr(candidate, "code", None)
        if isinstance(code, int):
            return code
        if candidate is not None and "RESOURCE_EXHAUSTED" in str(candidate):
            return 429
    return None


def with_rate_limit_retries(call, emit, attempts: int, max_wait: float):
    """Retry only provider throttling/5xx. Waiting for quota is not a model attempt."""
    import random

    def wrapped(**kwargs):
        for attempt in range(1, attempts + 1):
            try:
                return call(**kwargs)
            except Exception as error:
                code = _status(error)
                if code not in RETRYABLE or attempt == attempts:
                    raise
                wait = min(max_wait, 2**attempt) + random.uniform(0, 1.5)
                emit("provider_retry", {"status": code, "attempt": attempt, "wait_seconds": round(wait, 1)})
                time.sleep(wait)

    return wrapped


def load_manifest(pin: Path, suite: str) -> dict:
    for name in ("suite.json", f"{suite}.json"):
        if (pin / name).exists():
            return json.loads((pin / name).read_text())
    raise SystemExit(f"no manifest in {pin}")


def worker(
    provider: str,
    model: str,
    case_id: str,
    folder: str,
    max_steps: int,
    retry_attempts: int = 1,
    retry_max_wait: float = 30.0,
) -> None:
    from unittest.mock import patch

    from trade_agents.bench.runner import run_scenario
    from trade_agents.bench.scenario import load_scenarios
    from trade_agents.bench.world import World
    from web import live_models
    from web.live_models import PublicModel, bounded_model

    if provider in EXTRA_PROVIDERS and provider not in live_models.MODELS:
        live_models.MODELS[provider] = EXTRA_PROVIDERS[provider]

    folder = Path(folder)
    events = folder / "model-events.jsonl"

    def emit(kind, data):
        with events.open("a") as out:
            out.write(json.dumps({"kind": kind, "data": data}, default=str) + "\n")

    scenario = next(s for s in load_scenarios(only=[case_id]) if s.id == case_id)

    class ObservedWorld(World):
        def __exit__(self, *args):
            try:
                orders = []
                for order in self.agent_orders():
                    d = order.get("data", {})
                    orders.append(
                        {
                            "status": order["status"],
                            "total_eur": order["total_eur"],
                            "policy_outcome": d.get("decision", {}).get("outcome"),
                            "policy_checks": d.get("decision", {}).get("checks", []),
                            "receipt_present": bool(d.get("receipt")),
                            "lines": [
                                {
                                    "product_id": x["offer"]["product_id"],
                                    "quantity": x["quantity"],
                                    "sales_unit": x["offer"]["sales_unit"],
                                }
                                for x in d.get("lines", [])
                            ],
                        }
                    )
                (folder / "observed-gates.json").write_text(
                    json.dumps(
                        {
                            "agent_orders": orders,
                            "audit_valid": self.gateway.store.verify_audit()[0],
                            "attribution": "Observed policy outcomes, not inferred causal model rescue",
                        },
                        indent=2,
                    )
                    + "\n"
                )
            finally:
                super().__exit__(*args)

    inner = None
    try:
        inner = bounded_model(provider, model)
        if retry_attempts > 1:
            if provider == "gemini":
                models = inner.client.models
                models.generate_content = with_rate_limit_retries(
                    models.generate_content, emit, retry_attempts, retry_max_wait
                )
            else:
                completions = inner.client.chat.completions
                completions.create = with_rate_limit_retries(
                    completions.create, emit, retry_attempts, retry_max_wait
                )
        wrapped = PublicModel(inner, emit)
        traces = []

        def trace(step):
            traces.append(asdict(step))
            emit("tool", asdict(step))

        with patch("trade_agents.bench.runner.World", ObservedWorld):
            result = run_scenario(
                scenario,
                lambda: wrapped,
                replay="record",
                bench_dir=folder,
                max_steps=max_steps,
                on_trace=trace,
            )
        if result.skipped:
            result.skipped = "Provider execution unavailable; diagnostics omitted"
        if result.status == "error":
            result.explanation = "Agent/provider execution did not complete; diagnostics omitted"
        (folder / "result.json").write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")
        (folder / "trace.json").write_text(json.dumps(traces, indent=2, default=str) + "\n")
    except Exception as error:  # noqa: BLE001 - recorded as an unscored attempt
        (folder / "worker-error.json").write_text(
            json.dumps(
                {
                    "error_type": type(error).__name__,
                    "message": "Case worker failed; sensitive diagnostics omitted",
                }
            )
            + "\n"
        )
    finally:
        if inner is not None:
            inner.client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pin-dir", required=True, type=Path)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--provider", required=True, choices=sorted(CREDENTIALS))
    parser.add_argument("--run-tag", required=True, help="e.g. 20261114-n1; part of the archive name")
    parser.add_argument("--runs-dir", type=Path, default=PROJECT / "bench/runs")
    parser.add_argument("--env-file", type=Path, default=PROJECT / ".env")
    parser.add_argument("--concurrency", type=int, default=1, help="cases run in parallel for this provider")
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=1,
        help="attempts per HTTP call on 429/5xx (1 = no retries, as in the frozen limits)",
    )
    parser.add_argument("--retry-max-wait", type=float, default=30.0)
    parser.add_argument(
        "--import-from",
        type=Path,
        default=None,
        help="reuse finished case folders from an earlier run (archive or .partial) of the same pin",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model id for a provider not listed in the frozen manifest (recorded as added later)",
    )
    args = parser.parse_args()

    pin = args.pin_dir.resolve()
    os.environ["TTA_SUITE_PIN"] = str(pin)
    sys.path[:0] = [str(pin), str(pin / "src")]
    from dotenv import dotenv_values

    from trade_agents.bench.records import RunArchive, verify_run
    from trade_agents.bench.runner import BenchReport, ScenarioResult
    from trade_agents.bench.scenario import load_scenarios
    from trade_agents.bench.verify import CheckResult

    manifest = load_manifest(pin, args.suite)
    limits = manifest["limits"]
    wall = limits["wall_seconds_per_case"]
    spec = next((m for m in manifest["models"] if m["provider"] == args.provider), None)
    if spec is None:
        if not args.model:
            raise SystemExit(f"{args.provider} is not in the manifest; pass --model")
        spec = {"provider": args.provider, "model": args.model, "added_after_freeze": True}
    key = CREDENTIALS[args.provider]
    value = dotenv_values(args.env_file).get(key) or os.environ.get(key)
    if not value:
        raise SystemExit(f"{key} missing (env file or environment)")
    os.environ[key] = value
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1",
        TTA_LIVE_BENCH="1",
        TTA_LOCAL="1",
        TTA_VALIO_REVIEW="off",
        VALIO_BASE_URL="https://synthetic.invalid",
    )
    for name in ("VALIO_API_KEY", "TTA_DEMO_CLOCK", "AIVEN_PG_URL", "TTA_LLM_MODEL", "TTA_LLM_PROVIDER"):
        os.environ.pop(name, None)

    suite = manifest["id"]
    archive = RunArchive(args.runs_dir / suite, f"{args.provider}-{suite}-{args.run_tag}")
    archive.write_json("suite.json", manifest)
    archive.write_json("source.json", json.loads((pin / "pin-source.json").read_text()))
    code = Path(__file__).read_text()
    archive.write_json(
        "runner-source.json", {"sha256": hashlib.sha256(code.encode()).hexdigest(), "code": code}
    )
    archive.write_json(
        "inputs.json",
        {
            "provider": args.provider,
            "model": spec["model"],
            "repetitions": 1,
            "data_scope": "synthetic supplier/catalogue/orders; actual provider calls; no Valio network",
            "cost_usd": None,
            "limits": limits,
            "added_after_freeze": bool(spec.get("added_after_freeze")),
            "adapter": "runner EXTRA_PROVIDERS" if args.provider in EXTRA_PROVIDERS else "pinned live_models",
        },
    )

    # Waiting for provider quota must not eat the per-case model budget.
    wall_total = wall + (args.retry_max_wait * 3 if args.retry_attempts > 1 else 0)
    archive.write_json(
        "execution.json",
        {
            "concurrency": args.concurrency,
            "retry_attempts_per_http_call": args.retry_attempts,
            "retry_statuses": list(RETRYABLE),
            "retry_max_wait_seconds": args.retry_max_wait,
            "wall_seconds_per_case_including_retry_waits": wall_total,
            "imported_from": str(args.import_from) if args.import_from else None,
            "note": "Retries cover provider throttling/5xx only; they are not counted as model calls.",
        },
    )

    context = multiprocessing.get_context("spawn")
    scenarios = {s.id: s for s in load_scenarios()}
    order = [c["id"] for c in manifest["cases"]]
    for item in manifest["cases"]:
        current = hashlib.sha256(
            json.dumps(
                asdict(scenarios[item["id"]]), sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()
        if current != item["scenario_sha256"]:
            raise SystemExit(f"pinned case {item['id']} changed")

    done: dict[str, object] = {}

    def load_result(folder: Path, cid: str, elapsed: float | None):
        path = folder / "result.json"
        if path.exists():
            payload = json.loads(path.read_text())
            payload["checks"] = [CheckResult(**v) for v in payload["checks"]]
            return ScenarioResult(**payload)
        if folder.joinpath("timeout-result.json").exists() and elapsed is None:
            payload = json.loads(folder.joinpath("timeout-result.json").read_text())
            payload["checks"] = [CheckResult(**v) for v in payload.get("checks", [])]
            return ScenarioResult(**payload)
        scenario = scenarios[cid]
        elapsed = elapsed or 0.0
        reason = "execution_timeout" if elapsed >= wall_total - 1 else "execution_error"
        result = ScenarioResult(
            cid,
            scenario.category,
            scenario.title,
            False,
            skipped=reason,
            skip_kind=reason,
            model=f"{args.provider}:{spec['model']}",
            status="error",
            seconds=round(elapsed, 2),
        )
        log = folder / "model-events.jsonl"
        events = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        result.live_model_calls = sum(e["kind"] == "model_call" for e in events)
        result.live_tokens = sum(
            e["data"].get("input_tokens", 0) + e["data"].get("output_tokens", 0)
            for e in events
            if e["kind"] == "model_usage"
        )
        (folder / "timeout-result.json").write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")
        return result

    def record(cid: str, result, imported: bool = False) -> None:
        done[cid] = result
        archive.append_event(
            {
                "id": cid,
                "passed": result.passed,
                "scored": not result.skipped,
                "status": result.status,
                "live_model_calls": result.live_model_calls,
                "reported_tokens": result.live_tokens,
                "seconds": result.seconds,
                "imported": imported,
            }
        )
        print(
            json.dumps(
                {
                    "provider": args.provider,
                    "completed": len(done),
                    "id": cid,
                    "pass": result.passed,
                    "scored": not result.skipped,
                    "status": result.status,
                    "imported": imported,
                    "calls": result.live_model_calls,
                    "tokens": result.live_tokens,
                }
            ),
            flush=True,
        )
        ordered = [done[c] for c in order if c in done]
        partial = BenchReport(
            f"{args.provider}:{spec['model']}", datetime.now(UTC).isoformat(), ordered
        ).to_dict()
        partial["planned_cases"] = len(order)
        partial["state"] = "running" if len(done) < len(order) else "completed"
        (archive.staging_path / "progress.json").write_text(json.dumps(partial, indent=2, default=str) + "\n")

    import shutil

    if args.import_from:
        for cid in order:
            source = args.import_from / "cases" / cid
            if (source / "result.json").exists():  # only cleanly finished cases; timeouts are re-run
                target = archive.staging_path / "cases" / cid
                shutil.copytree(source, target)
                record(cid, load_result(target, cid, None), imported=True)

    pending = [cid for cid in order if cid not in done]
    running: dict[str, tuple] = {}
    while pending or running:
        while pending and len(running) < max(1, args.concurrency):
            cid = pending.pop(0)
            folder = archive.staging_path / "cases" / cid
            folder.mkdir(parents=True)
            process = context.Process(
                target=worker,
                args=(
                    args.provider,
                    spec["model"],
                    cid,
                    str(folder),
                    limits["max_chat_steps"],
                    args.retry_attempts,
                    args.retry_max_wait,
                ),
            )
            process.start()
            running[cid] = (process, time.monotonic(), folder)
        time.sleep(0.5)
        for cid, (process, started, folder) in list(running.items()):
            elapsed = time.monotonic() - started
            if process.is_alive() and elapsed < wall_total:
                continue
            for stop in (process.terminate, process.kill):
                if process.is_alive():
                    stop()
                    process.join(5)
            process.join(1)
            process.close()
            del running[cid]
            record(cid, load_result(folder, cid, elapsed))

    results = [done[c] for c in order]
    report = BenchReport(f"{args.provider}:{spec['model']}", datetime.now(UTC).isoformat(), results).to_dict()
    report.update(
        suite=suite,
        repetitions=1,
        scope=manifest["scope"],
        limits=limits,
        causal_harness_rescues=None,
        attribution="Gate outcomes recorded separately; pending human approval is not "
        "automatically a rescued model error",
    )
    archive.write_json("report.json", report)
    final = archive.finalize()
    verify_run(final)
    print(
        json.dumps(
            {
                "provider": args.provider,
                "final_archive": str(final),
                "passed": report["passed"],
                "scored": report["scored"],
                "selected": len(manifest["cases"]),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
