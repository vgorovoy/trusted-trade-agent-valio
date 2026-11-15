"""Report math for scripts/eval_suite/publish.py (pass^k, pass@k, cost)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "publish", Path(__file__).resolve().parents[1] / "scripts/eval_suite/publish.py")
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)


def test_case_cost_and_median():
    price = {"basis": "per_token", "input_per_million": 1.5, "output_per_million": 9.0}
    assert abs(publish.case_cost(price, 1_000_000, 100_000) - 2.4) < 1e-9
    assert publish.case_cost({"basis": "gpu_hour"}, 1, 1) is None
    assert publish.case_cost(None, 1, 1) is None
    assert publish.median([3, 1, 2]) == 2 and publish.median([1, 2, 3, 4]) == 2.5
    assert publish.median([]) is None


def _run(root: Path, results: dict[str, bool | None]) -> None:
    root.mkdir(parents=True)
    rows = [{"id": cid, "passed": bool(ok), "skipped": "provider" if ok is None else None,
             "seconds": 1.0} for cid, ok in results.items()]
    (root / "report.json").write_text(json.dumps({"results": rows}))
    for cid in results:
        case = root / "cases" / cid
        case.mkdir(parents=True)
        (case / "model-events.jsonl").write_text(json.dumps(
            {"kind": "model_usage", "data": {"input_tokens": 1000, "output_tokens": 100}}) + "\n")


def test_repeat_metrics(tmp_path):
    cases = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    runs = {"t1": {"a": True, "b": True, "c": False},
            "t2": {"a": True, "b": False, "c": False},
            "t3": {"a": True, "b": True, "c": None}}
    for tag, res in runs.items():
        _run(tmp_path / "s" / f"p-s-{tag}", res)
    price = {"basis": "per_token", "input_per_million": 1.0, "output_per_million": 10.0}
    r = publish.repeat_metrics(tmp_path, "s", "p", ["t1", "t2", "t3", "missing"], cases, price,
                               lambda root: None)
    assert r["runs"] == 3 and r["tags"] == ["t1", "t2", "t3"]
    assert r["pass_hat_k"] == 1          # only "a" passed every run
    assert r["pass_at_k"] == 2           # "a" and "b"
    assert r["cases_fully_scored"] == 2  # "c" unscored once
    assert r["mean_pass_rate"] == round(5 / 8, 4)
    assert abs(r["cost_usd_mean_run"] - 3 * 0.002) < 1e-9
    assert publish.repeat_metrics(tmp_path, "s", "p", ["t1"], cases, price, lambda root: None) is None
