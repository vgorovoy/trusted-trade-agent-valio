"""Versioned alias-only rescoring of immutable menu drafts, with zero model calls.

This is an oracle correction, never a new model run. Original reports and source
manifests remain intact and the resulting evidence explicitly retains both scores.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .menu_eval import verify_menu_draft
from .records import RunArchive, source_identity, verify_run
from .scenario import BENCH_DIR


def _suite_version(cases: list[dict[str, Any]]) -> tuple[str, int, str]:
    versions = {case.get("suite_version") for case in cases}
    if len(versions) != 1:
        raise ValueError("alias revision requires one explicit suite version")
    version = next(iter(versions))
    match = re.fullmatch(r"(menu-draft(?:-image)?)-v([1-9][0-9]*)", str(version))
    if not match:
        raise ValueError("unsupported menu suite version")
    return match[1], int(match[2]), str(version)


def validate_alias_revision(original: list[dict[str, Any]], revised: list[dict[str, Any]]) -> None:
    """Only aliases may grow; changing requirements is not an alias correction."""
    old_family, old_version, _ = _suite_version(original)
    new_family, new_version, _ = _suite_version(revised)
    if old_family != new_family or new_version <= old_version:
        raise ValueError("alias revision must use a strictly newer version in the same suite family")
    old = {case["id"]: case for case in original}
    if len(old) != len(original):
        raise ValueError("original case IDs must be unique")
    sources = [case.get("source_case_id") for case in revised]
    if len(sources) != len(set(sources)) or set(sources) != set(old):
        raise ValueError("revision must map each original case exactly once")
    new_ids = [case["id"] for case in revised]
    if len(new_ids) != len(set(new_ids)):
        raise ValueError("revised case IDs must be unique")
    for case in revised:
        before, after = copy.deepcopy(old[case["source_case_id"]]), copy.deepcopy(case)
        if case["id"] == before["id"]:
            raise ValueError("revised oracle requires a distinct case ID")
        for document in (before, after):
            document.pop("source_case_id", None)
            document.pop("id")
            document.pop("suite_version")
        before_rows = before["expect"].get("needs", [])
        after_rows = after["expect"].get("needs", [])
        if len(before_rows) != len(after_rows):
            raise ValueError("alias revision cannot change expected ingredient rows")
        for row_before, row_after in zip(before_rows, after_rows, strict=True):
            old_aliases, new_aliases = row_before.pop("aliases", []), row_after.pop("aliases", [])
            if not set(old_aliases) <= set(new_aliases):
                raise ValueError("alias revision may only extend accepted aliases")
        if before != after:
            raise ValueError("alias revision cannot change prompts, quantities, dates or requirements")


def rescore_menu_archive(source: Path, revised: list[dict[str, Any]], *,
                         output: Path, run_id: str | None = None) -> dict[str, Any]:
    """Verify original evidence, regrade saved drafts and finalize a separate archive."""
    source = source.resolve()
    original_manifest = verify_run(source)
    original_inputs = json.loads((source / "inputs.json").read_text())
    original_report = json.loads((source / "report.json").read_text())
    validate_alias_revision(original_inputs["suite"], revised)
    records = {result["id"]: result for result in original_report["results"]}
    if (len(records) != len(original_report["results"])
            or set(records) != {case["source_case_id"] for case in revised}):
        raise ValueError("source report does not cover exactly the revised case mapping")
    manifest_hash = hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
    results = []
    for case in revised:
        original = records[case["source_case_id"]]
        result = {"id": case["id"], "source_case_id": original["id"],
                  "scope": original["scope"], "original_passed": original["passed"],
                  "original_checks": original["checks"], "status": original["status"],
                  "original_error_type": original.get("error_type"), "passed": False,
                  "checks": [], "draft": copy.deepcopy(original.get("draft")),
                  "model": original.get("model") or (original.get("draft") or {}).get("model")}
        if original["status"] == "scored" and result["draft"] is not None:
            result["checks"] = verify_menu_draft(case, result["draft"])
            result["passed"] = all(check["ok"] for check in result["checks"])
        results.append(result)
    attempted = len(results)
    scored = sum(result["status"] == "scored" for result in results)
    report = {"suite": _suite_version(revised)[2],
              "source_suite": _suite_version(original_inputs["suite"])[2],
              "regrade_only": True, "new_model_calls": 0,
              "source_run_id": original_manifest["run_id"],
              "source_manifest_sha256": manifest_hash,
              "original_passed": original_report["passed"],
              "attempted": attempted, "scored": scored, "errors": attempted - scored,
              "passed": sum(result["passed"] for result in results),
              "coverage": scored / attempted if attempted else 0.0,
              "results": results,
              "claim": "Alias-only oracle correction over the original saved drafts, not a new "
                       "model trial or held-out improvement; original scores remain preserved"}
    archive = RunArchive(output, run_id)
    archive.write_json("inputs.json", {
        "suite": copy.deepcopy(revised), "source": source_identity(BENCH_DIR.parent),
        "source_run_id": original_manifest["run_id"],
        "source_manifest_sha256": manifest_hash,
        "source_manifest_artifacts": original_manifest["artifacts"],
        "revision": "Only accepted ingredient aliases were extended"})
    archive.write_json("original-manifest.json", original_manifest)
    archive.write_json("original-inputs.json", original_inputs)
    archive.write_json("original-report.json", original_report)
    archive.write_json("report.json", report)
    report["archive"] = str(archive.finalize(status="completed" if scored == attempted else "error"))
    return report
