"""Explicit model invocation for the separate review-only menu draft benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from trade_agents.bench.menu_eval import MENU_DIR, load_menu_cases, run_menu_evaluation
from trade_agents.llm import get_llm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cases", type=Path, default=MENU_DIR)
    parser.add_argument("--output", type=Path, default=MENU_DIR.parent / "runs" / "menu")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    report = run_menu_evaluation(
        load_menu_cases(args.cases), lambda: get_llm(args.provider, args.model),
        output=args.output, run_id=args.run_id)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] == report["attempted"] and report["attempted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
