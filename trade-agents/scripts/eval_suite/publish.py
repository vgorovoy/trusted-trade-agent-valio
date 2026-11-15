"""Summarise suite runs (finished or in progress) into the JSON consumed by the /models page.

    python scripts/eval_suite/publish.py --pin-dir /tmp/tta-pin-full-v1 --suite full-v1 \
        --run-tag 20261114-n1 --providers gemini verda-nemotron verda \
        --out web/data/full-comparison-20261114-v1.json

Finished archives are verified before use. Unfinished cases are reported as not yet
assessed — never as passes or failures. Safe to re-run while runs are in progress.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
LABELS = {
    "verda-nemotron": "verda-nvidia-nemotron",
    "verda": "verda-kimi",
    "gemini": "gemini",
    "nvidia": "nvidia",
}


def case_usage(folder: Path) -> tuple[int, int]:
    """Input / output tokens the provider reported for one case (model_usage events)."""
    log = folder / "model-events.jsonl"
    if not log.exists():
        return 0, 0
    tin = tout = 0
    for line in log.read_text().splitlines():
        event = json.loads(line)
        if event.get("kind") == "model_usage":
            tin += int(event["data"].get("input_tokens") or 0)
            tout += int(event["data"].get("output_tokens") or 0)
    return tin, tout


def case_cost(price: dict | None, tin: int, tout: int) -> float | None:
    if not price or price.get("basis") != "per_token":
        return None
    return tin / 1e6 * price["input_per_million"] + tout / 1e6 * price["output_per_million"]


def median(values: list[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def repeat_metrics(
    runs_dir: Path,
    suite: str,
    provider: str,
    tags: list[str],
    cases: list[dict],
    price: dict | None,
    verify_run,
) -> dict | None:
    """pass@k (any of k runs passed), pass^k (all k passed) and mean pass rate over finished runs."""
    finished = []
    for tag in tags:
        root = runs_dir / suite / f"{provider}-{suite}-{tag}"
        if root.exists():
            verify_run(root)
            finished.append(
                (tag, root, {r["id"]: r for r in json.loads((root / "report.json").read_text())["results"]})
            )
    if len(finished) < 2:
        return None
    per_case, run_costs, seconds = [], [], []
    for case in cases:
        outcomes = [res.get(case["id"]) for _, _, res in finished]
        scored = [r for r in outcomes if r and not r.get("skipped")]
        per_case.append(
            {
                "id": case["id"],
                "passes": sum(bool(r["passed"]) for r in scored),
                "scored": len(scored),
                "runs": len(finished),
            }
        )
        seconds += [r["seconds"] for r in outcomes if r and r.get("seconds") is not None]
    for _, root, res in finished:
        total = 0.0
        for cid in res:
            tin, tout = case_usage(root / "cases" / cid)
            cost = case_cost(price, tin, tout)
            total += cost or 0.0
        run_costs.append(total)
    k = len(finished)
    complete = [c for c in per_case if c["scored"] == k]
    return {
        "runs": k,
        "tags": [t for t, _, _ in finished],
        "pass_hat_k": sum(c["passes"] == k for c in complete),
        "pass_at_k": sum(c["passes"] > 0 for c in per_case),
        "mean_pass_rate": round(
            sum(c["passes"] for c in per_case) / max(1, sum(c["scored"] for c in per_case)), 4
        ),
        "cases_fully_scored": len(complete),
        "cost_usd_mean_run": round(sum(run_costs) / k, 4) if price else None,
        "seconds_median_case": median(seconds),
        "per_case": per_case,
    }


def ledger_row(case: dict, r: dict | None) -> dict:
    human = (r or {}).get("human_action")
    return {
        "id": case["id"],
        "category": case["category"],
        "state": "assessed" if r else "not_yet_assessed",
        "raw_pass": r["passed"] if r else None,
        "scored": bool(r and not r.get("skipped")),
        "status": (r or {}).get("status"),
        "agent_status": (r or {}).get("agent_status"),
        "model_claimed_status": (r or {}).get("model_claimed_status"),
        "error_kind": (r or {}).get("skip_kind"),
        "failed_checks": [c["name"] for c in (r or {}).get("checks", []) if not c["ok"]],
        "checks": [{"name": c["name"], "ok": c["ok"]} for c in (r or {}).get("checks", [])],
        "scripted_human_planned": bool(case["scripted_human"]),
        "scripted_human_applied": bool(human and human.get("applied")),
        "reported_tokens": (r or {}).get("live_tokens", 0),
        "model_call_attempts": (r or {}).get("live_model_calls", 0),
        "seconds": (r or {}).get("seconds"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pin-dir", required=True, type=Path)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--run-tag", required=True)
    parser.add_argument("--providers", nargs="+", required=True)
    parser.add_argument("--runs-dir", type=Path, default=PROJECT / "bench/runs")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report-id", default=None)
    parser.add_argument(
        "--repeat-tags",
        nargs="*",
        default=[],
        help="extra finished run tags of the same pin for pass@k / pass^k (e.g. 20261114-n2 ...)",
    )
    parser.add_argument("--prices", type=Path, default=None, help="public price list (bench/prices-*.json)")
    parser.add_argument("--note", action="append", default=[], help="extra limitation shown with the report")
    args = parser.parse_args()

    pin = args.pin_dir.resolve()
    sys.path[:0] = [str(pin), str(pin / "src")]
    from trade_agents.bench.records import verify_run

    manifest = next(
        json.loads((pin / n).read_text()) for n in ("suite.json", f"{args.suite}.json") if (pin / n).exists()
    )
    total = len(manifest["cases"])
    prices = json.loads(args.prices.read_text()) if args.prices else None
    rows = []
    for provider in args.providers:
        name = f"{provider}-{manifest['id']}-{args.run_tag}"
        root = args.runs_dir / manifest["id"] / name
        complete = root.exists()
        if complete:
            verify_run(root)
            report = json.loads((root / "report.json").read_text())
            inputs = json.loads((root / "inputs.json").read_text())
        else:
            root = root.parent / f".{name}.partial"
            progress = root / "progress.json"
            report = json.loads(progress.read_text()) if progress.exists() else {"results": []}
            inputs = json.loads((root / "inputs.json").read_text()) if (root / "inputs.json").exists() else {}
        results = {r["id"]: r for r in report["results"]}
        ledger = [ledger_row(c, results.get(c["id"])) for c in manifest["cases"]]
        price = (prices or {}).get("models", {}).get(provider)
        for row in ledger:
            if row["state"] == "assessed":
                row["input_tokens"], row["output_tokens"] = case_usage(root / "cases" / row["id"])
                row["cost_usd"] = case_cost(price, row["input_tokens"], row["output_tokens"])
        done_rows = [x for x in ledger if x["state"] == "assessed"]
        costs = [x["cost_usd"] for x in done_rows if x.get("cost_usd") is not None]
        scored = sum(x["scored"] for x in ledger)
        passed = sum(bool(x["raw_pass"]) for x in ledger if x["scored"])
        repeats = (
            repeat_metrics(
                args.runs_dir,
                manifest["id"],
                provider,
                [args.run_tag, *args.repeat_tags],
                manifest["cases"],
                price,
                verify_run,
            )
            if args.repeat_tags
            else None
        )
        rows.append(
            {
                "provider": LABELS.get(provider, provider),
                "provider_id": provider,
                "model_id": inputs.get("model"),
                "model": f"{provider}:{inputs.get('model')}",
                "added_after_freeze": bool(inputs.get("added_after_freeze")),
                "state": "completed" if complete else "running",
                "selected": total,
                "completed": len(results),
                "raw_scored": scored,
                "raw_passed": passed,
                "coverage": scored / total,
                "raw_accuracy": passed / scored if complete and scored else None,
                "provider_errors": sum(x["error_kind"] == "provider" for x in ledger),
                "execution_errors": sum(
                    bool(x["error_kind"]) and x["error_kind"] != "provider" for x in ledger
                ),
                "reported_tokens": sum(x["reported_tokens"] for x in ledger),
                "model_call_attempts": sum(x["model_call_attempts"] for x in ledger),
                "input_tokens": sum(x.get("input_tokens", 0) for x in done_rows),
                "output_tokens": sum(x.get("output_tokens", 0) for x in done_rows),
                "seconds_total": round(sum(x["seconds"] or 0 for x in done_rows), 1),
                "seconds_median_case": median([x["seconds"] for x in done_rows if x["seconds"] is not None]),
                "cost_usd": round(sum(costs), 4) if costs else None,
                "cost_usd_per_case": round(sum(costs) / len(costs), 5) if costs else None,
                "cost_usd_per_pass": round(sum(costs) / passed, 5) if costs and passed else None,
                "price": price,
                "repeats": repeats,
                "manifest_verified": complete,
                "cases": ledger,
            }
        )
    data = {
        "schema_version": 1,
        "id": args.report_id or args.out.stem,
        "title": f"Synthetic suite {manifest['id']}: {total} tasks × {len(rows)} live models",
        "state": "completed" if all(m["state"] == "completed" for m in rows) else "running",
        "repetitions_per_model_case": 1,
        "repeated_runs": sorted({m["repeats"]["runs"] for m in rows if m.get("repeats")}) or None,
        "scope": manifest["scope"],
        "source_snapshot_sha256": manifest["source_snapshot_sha256"],
        "source_origin_git_sha": manifest["origin_git_sha"],
        "source_had_uncommitted_changes": manifest["origin_had_uncommitted_changes"],
        "limits": manifest["limits"],
        "prices": {k: prices[k] for k in ("retrieved", "currency", "note")} if prices else None,
        "models": rows,
        "retired_cases": manifest.get("retired_cases", []),
        "limitations": manifest["limitations"]
        + [f"Retired from this suite: {c['id']} — {c['reason']}" for c in manifest.get("retired_cases", [])]
        + args.note
        + [
            "Unfinished cases are not yet assessed, not passes or failures.",
            "Scripted human decisions are environmental assistance, not autonomous model success.",
            "Policy checks show observed guard outcomes, not causal proof that the harness rescued a model.",
            "No official Valio API calls occur in this synthetic lane; "
            "official snapshot compatibility is a separate report.",
        ]
        + (
            [
                "Models marked added_after_freeze were run on the same pinned code and cases "
                "after the manifest was frozen."
            ]
            if any(m["added_after_freeze"] for m in rows)
            else []
        ),
    }
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "state": data["state"],
                "models": [
                    {
                        k: m[k]
                        for k in ("provider", "completed", "raw_passed", "raw_scored", "provider_errors")
                    }
                    for m in rows
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
