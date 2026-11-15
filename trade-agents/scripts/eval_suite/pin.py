"""Freeze a source snapshot and an evaluation-suite manifest for a reproducible model run.

    python scripts/eval_suite/pin.py --suite full-v1 --pin-dir /tmp/tta-pin-full-v1
    python scripts/eval_suite/pin.py --suite core-v1 --pin-dir /tmp/tta-pin-core-v1 \
        --cases-from bench/evaluation-suites/core-v1.json

The pin directory is a copy of the code under test (never overwritten), so a run is not
affected by later edits in the working tree. The manifest records a SHA-256 per scenario;
the runner refuses to start if a pinned scenario changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS = [
    {"provider": "gemini", "model": "gemini-3.5-flash"},
    {"provider": "verda-nemotron", "model": "nemotron-3.5-super-vl"},
    {"provider": "verda", "model": "kimi-k3-1-pro"},
    {"provider": "verda-deepseek", "model": "deepseek-4-1-flash-1017"},
]
DEFAULT_LIMITS = {
    "max_chat_steps": 8,
    "http_timeout_seconds": 45,
    "sdk_attempts": 1,
    "application_attempts": 1,
    "max_output_tokens_per_request": 4096,
    "trajectory_token_stop_threshold": 40000,
    "wall_seconds_per_case": 240,
    "max_concurrent_providers": 3,
}


def scenario_sha256(scenario) -> str:
    encoded = json.dumps(asdict(scenario), sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--suite", required=True, help="suite id, e.g. full-v1")
    parser.add_argument("--pin-dir", required=True, type=Path)
    parser.add_argument("--title", default=None)
    parser.add_argument(
        "--cases-from",
        type=Path,
        default=None,
        help="take the case list from an existing manifest (default: all loaded scenarios)",
    )
    parser.add_argument("--frozen-date", default=None)
    parser.add_argument("--retire", action="append", default=[], metavar="ID=REASON",
                        help="leave a scenario out of the suite, recording why in the manifest")
    args = parser.parse_args()

    dst: Path = args.pin_dir
    if dst.exists():
        raise SystemExit(f"{dst} already exists; pins are never overwritten")
    dst.mkdir(parents=True)
    shutil.copytree(PROJECT / "src", dst / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dst / "web").mkdir()
    shutil.copyfile(PROJECT / "web/live_models.py", dst / "web/live_models.py")
    (dst / "bench").mkdir()
    for folder in ("scenarios", "worlds"):
        shutil.copytree(PROJECT / "bench" / folder, dst / "bench" / folder)
    shutil.copyfile(PROJECT / "bench/tiers.toml", dst / "bench/tiers.toml")

    files = {
        str(p.relative_to(dst)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(dst.rglob("*"))
        if p.is_file()
    }

    def git(*cmd: str) -> str:
        return subprocess.check_output(["git", *cmd], cwd=PROJECT, text=True)

    meta = {
        "origin_git_sha": git("rev-parse", "HEAD").strip(),
        "origin_had_uncommitted_changes": bool(git("status", "--porcelain").strip()),
        "source_kind": "Explicit copied source snapshot; hashes pin uncommitted code too",
        "files": files,
        "snapshot_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    (dst / "pin-source.json").write_text(json.dumps(meta, indent=2) + "\n")

    sys.path[:0] = [str(dst), str(dst / "src")]
    from trade_agents.bench.scenario import load_scenarios

    by_id = {s.id: s for s in load_scenarios()}
    if args.cases_from:
        ids = [c["id"] for c in json.loads(args.cases_from.read_text())["cases"]]
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise SystemExit(f"unknown scenarios: {missing}")
    else:
        ids = sorted(by_id)
    retired = dict(item.split("=", 1) for item in args.retire)
    unknown = [i for i in retired if i not in by_id]
    if unknown:
        raise SystemExit(f"unknown scenarios to retire: {unknown}")
    ids = [i for i in ids if i not in retired]
    from datetime import date

    manifest = {
        "schema_version": 1,
        "id": args.suite,
        "title": args.title or f"Frozen {len(ids)}-case synthetic procurement and safety suite",
        "frozen_date": args.frozen_date or date.today().isoformat(),
        "scope": "Synthetic supplier/catalogue/orders; actual live models. Separate from the "
        "3-case official-mock snapshot compatibility lane.",
        "repetitions_per_model_case": 1,
        "source_snapshot_sha256": meta["snapshot_sha256"],
        "origin_git_sha": meta["origin_git_sha"],
        "origin_had_uncommitted_changes": meta["origin_had_uncommitted_changes"],
        "models": DEFAULT_MODELS,
        "limits": DEFAULT_LIMITS,
        "cases": [
            {
                "id": s.id,
                "category": s.category,
                "title": s.title,
                "scenario_sha256": scenario_sha256(s),
                "expected": s.expect,
                "scripted_human": s.human or None,
            }
            for s in (by_id[i] for i in ids)
        ],
        "retired_cases": [{"id": i, "reason": r} for i, r in retired.items()],
        "limitations": [
            "N=1 exploratory; no statistical ranking power.",
            f"This run contains {len(ids)} of {len(by_id)} loaded synthetic scenarios; "
            "not menu/vision fixtures or official supplier diagnostics.",
            "Human choice script is environmental assistance, not autonomous model success.",
            "All attempts/errors retained; no post-run case selection.",
            "Cost unknown; token/call usage may omit failed-response metadata.",
        ],
    }
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    (dst / "suite.json").write_text(text)
    out = PROJECT / "bench/evaluation-suites" / f"{args.suite}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    print(
        json.dumps(
            {
                "pin_dir": str(dst),
                "snapshot_sha256": meta["snapshot_sha256"],
                "cases": len(ids),
                "manifest": str(out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
