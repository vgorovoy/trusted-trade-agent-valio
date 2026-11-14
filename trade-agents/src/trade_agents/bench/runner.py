"""Run scenarios through the buyer agent and score them."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..buyer_agent import BuyerAgent, InProcessTransport
from ..buyer_agent.agent import AgentResult
from ..llm import LLM, LLMResponse, ToolCall, is_provider_error
from .replay import ReplayLLM, ReplayMiss
from .scenario import BENCH_DIR, Scenario
from .verify import CheckResult, Final, verify
from .world import CassetteMiss, World

LLMFactory = Callable[[], LLM]

SKIP_KINDS = {"provider": "provider errors", "replay": "no recorded reply",
              "cassette": "no API cassette"}


class NullLLM:
    """The 'agent did nothing' baseline: finishes at once without ordering. The scenarios
    it passes are the ones a model gets for free (refusals, clarifications)."""

    name = "offline:do-nothing"
    vision = False

    def chat(self, messages, tools=(), *, system="", json_output=False) -> LLMResponse:
        return LLMResponse(tool_calls=[ToolCall(
            "finish", {"status": "no_order", "explanation_for_chef": "No order."}, id="null")])


@dataclass
class ScenarioResult:
    id: str
    category: str
    title: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    skipped: str | None = None          # why the scenario was not scored
    skip_kind: str | None = None        # provider | replay | cassette (see SKIP_KINDS)
    model: str = ""
    status: str | None = None
    model_claimed_status: str | None = None
    total_eur: float | None = None
    products: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    explanation: str = ""
    steps: int = 0
    tokens: int = 0
    api_calls: int = 0                   # requests to /api/price and /api/availability
    api_products: int = 0                # distinct products in them
    seconds: float = 0.0
    expected_outcome: str = "unspecified"
    live_model_calls: int = 0
    replay_hits: int = 0
    live_tokens: int = 0

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    @property
    def honest(self) -> bool | None:
        """Did the model report the gateway's decision as it was? None: nothing to report."""
        if self.model_claimed_status is None or self.status in ("no_order", "needs_clarification"):
            return None
        return self.model_claimed_status == self.status


@dataclass
class BenchReport:
    model: str
    started: str
    results: list[ScenarioResult]
    baseline: dict[str, bool] | None = None   # scenario id -> passed by NullLLM

    @property
    def baseline_passed(self) -> int | None:
        """Scored scenarios that the do-nothing agent passes too."""
        if self.baseline is None:
            return None
        return sum(self.baseline.get(r.id, False) for r in self.scored)

    def skipped_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            if r.skipped:
                k = r.skip_kind or "other"
                out[k] = out.get(k, 0) + 1
        return out

    @property
    def scored(self) -> list[ScenarioResult]:
        return [r for r in self.results if not r.skipped]

    @property
    def coverage(self) -> float:
        return len(self.scored) / len(self.results) if self.results else 0.0

    @property
    def accuracy(self) -> float:
        s = self.scored
        return sum(r.passed for r in s) / len(s) if s else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for r in self.scored:
            ok, n = out.get(r.category, (0, 0))
            out[r.category] = (ok + r.passed, n + 1)
        return out

    def outcome_breakdown(self) -> dict[str, dict[str, int]]:
        groups: dict[str, dict[str, int]] = {}
        for r in self.results:
            group = groups.setdefault(r.expected_outcome, {"selected": 0, "scored": 0,
                                                          "passed": 0, "skipped": 0})
            group["selected"] += 1
            group["skipped" if r.skipped else "scored"] += 1
            if not r.skipped:
                group["passed"] += r.passed
        return groups

    def safety_summary(self) -> dict[str, Any]:
        names = {"inv.audit_chain", "inv.mandate", "safety.allergens", "safety.hard_limit",
                 "safety.blocked_commit"}
        measured = [r for r in self.scored if names <= {c.name for c in r.checks}]
        violations = [{"scenario_id": r.id, "check": c.name, "detail": c.detail}
                      for r in measured for c in r.checks if c.name in names and not c.ok]
        return {"evaluated_scenarios": len(measured), "violations": len(violations),
                "details": violations,
                "scope": "committed-order allergens/traces, hard limit, rejected policy, mandate and audit"}

    def usage_summary(self) -> dict[str, Any]:
        live_calls = sum(r.live_model_calls for r in self.results)
        return {"trajectory_tokens": sum(r.tokens for r in self.results),
                "live_tokens": sum(r.live_tokens for r in self.results),
                "live_model_calls": live_calls,
                "replay_hits": sum(r.replay_hits for r in self.results),
                "cost_usd": None if live_calls else 0.0,
                "cost_note": "unknown: provider pricing not configured" if live_calls else
                             "no live provider calls; replay tokens describe recorded trajectories"}

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "started": self.started,
                "accuracy": round(self.accuracy, 4),
                "passed": sum(r.passed for r in self.scored), "scored": len(self.scored),
                "skipped": self.skipped_by_kind(),
                "do_nothing_baseline": self.baseline_passed,
                "coverage": {"selected": len(self.results), "scored": len(self.scored),
                             "fraction": self.coverage},
                "outcome_breakdown": self.outcome_breakdown(),
                "safety": self.safety_summary(), "usage": self.usage_summary(),
                "by_category": {c: {"passed": ok, "total": n}
                                for c, (ok, n) in self.by_category().items()},
                "results": [{**asdict(r), "honest": r.honest} for r in self.results]}

    def render(self, verbose: bool = False) -> str:
        lines = [f"Agent bench: {self.model}", ""]
        for r in self.results:
            mark = "SKIP" if r.skipped else ("PASS" if r.passed else "FAIL")
            lines.append(f"  {mark}  {r.category:<13} {r.id:<28} {r.status or '-':<27}"
                         + (f" {r.total_eur:>9.2f} EUR" if r.total_eur is not None else ""))
            if r.skipped:
                lines.append(f"        {r.skipped}")
            for c in (r.checks if verbose else r.failed_checks):
                lines.append(f"        {'ok ' if c.ok else 'x  '}{c.name}: {c.detail}")
        lines += ["", f"  {'category':<14}{'passed':>8}"]
        for cat, (ok, n) in self.by_category().items():
            lines.append(f"  {cat:<14}{ok:>4}/{n:<3} {ok / n:>6.0%}")
        honest = [r.honest for r in self.scored if r.honest is not None]
        s = self.scored
        lines += ["", f"  Accuracy: {sum(r.passed for r in s)}/{len(s)} = {self.accuracy:.1%}"]
        if self.baseline_passed is not None and s:
            lines.append(f"  Do-nothing agent (never orders): {self.baseline_passed}/{len(s)}"
                         f" = {self.baseline_passed / len(s):.1%}")
        for kind, n in self.skipped_by_kind().items():
            lines.append(f"  Skipped: {n} ({SKIP_KINDS.get(kind, kind)}), "
                         "not counted in accuracy")
        if honest:
            lines.append(f"  Status reported truthfully: {sum(honest)}/{len(honest)}")
        if s:
            lines.append(f"  Valio API: {sum(r.api_calls for r in s)} call(s) for "
                         f"{max(r.api_products for r in s)} product(s) at most per request")
        lines.append(f"  Coverage: {len(s)}/{len(self.results)} selected scenarios scored")
        for outcome, counts in self.outcome_breakdown().items():
            lines.append(f"  Expected {outcome}: {counts['passed']}/{counts['scored']} passed, "
                         f"{counts['skipped']} skipped")
        safety, usage = self.safety_summary(), self.usage_summary()
        lines.append(f"  Safety violations: {safety['violations']} "
                     f"({safety['evaluated_scenarios']} scenarios evaluated; limited check scope)")
        lines.append(f"  Tokens: {usage['trajectory_tokens']} trajectory, {usage['live_tokens']} live; "
                     f"model calls: {usage['live_model_calls']} live, {usage['replay_hits']} replay")
        lines.append(f"  Cost USD: {usage['cost_usd'] if usage['cost_usd'] is not None else 'unknown'}")
        return "\n".join(lines)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "model"


def run_scenario(sc: Scenario, llm_factory: LLMFactory, *, replay: str = "auto",
                 record_api: bool = False, bench_dir: Path = BENCH_DIR,
                 max_steps: int = 14) -> ScenarioResult:
    statuses = sc.expect.get("status", [])
    statuses = {statuses} if isinstance(statuses, str) else set(statuses)
    outcome = ("purchase" if "placed" in statuses else
               "refusal" if statuses and statuses <= {"rejected", "no_order"} else
               "clarification" if statuses == {"needs_clarification"} else "safe_resolution")
    out = ScenarioResult(sc.id, sc.category, sc.title, passed=False, expected_outcome=outcome)
    t0 = time.monotonic()
    try:
        world = World(sc, record=record_api, bench_dir=bench_dir)
    except CassetteMiss as e:
        out.skipped, out.skip_kind = str(e), "cassette"
        return out
    llm = llm_factory()
    out.model = llm.name
    if not llm.name.startswith("offline"):  # the rule-based agent is deterministic already
        llm = ReplayLLM(llm, bench_dir / "replays" / _slug(llm.name) / f"{sc.id}.json", replay)
    with world:
        profile = world.profile(model=llm.name)
        world.place_prior_orders(profile)
        api_mark = len(world.api_log.calls)   # setup orders are not the agent's queries
        agent = BuyerAgent(profile, llm, InProcessTransport(world.gateway),
                           max_steps=max_steps, today=world.today)
        try:
            res = agent.run(sc.request)
        except Exception as e:  # a crashing agent fails the scenario, not the bench
            res = AgentResult(sc.request, sc.buyer, "error", explanation=f"{type(e).__name__}: {e}",
                              model=llm.name, error=f"{type(e).__name__}: {e}"[:500],
                              error_type=type(e).__name__,
                              provider_error=is_provider_error(e))
        finally:
            if isinstance(llm, ReplayLLM):
                llm.save()
                out.live_model_calls, out.replay_hits = llm.misses, llm.hits
                out.live_tokens = llm.live_tokens
        # The agent turns every model failure into status "error". A missing recorded reply
        # or the provider being down (quota, 5xx, network) says nothing about the agent:
        # such scenarios are skipped, not failed.
        kind = ("replay" if res.error_type == ReplayMiss.__name__
                else "provider" if res.provider_error else None)
        if kind is not None:
            out.skipped, out.skip_kind = res.error or kind, kind
            out.status, out.steps, out.tokens = res.status, res.steps, res.tokens
            out.seconds = round(time.monotonic() - t0, 2)
            return out
        orders = world.agent_orders()
        api_products = world.api_log.products(api_mark)
        out.api_calls = len(world.api_log.calls) - api_mark
        out.api_products = len(api_products)
        final = Final(res, orders, profile.mandate, world.gateway.store.verify_audit()[0],
                      api_products)
        out.checks = verify(final, sc.expect)
        out.products = final.product_ids
    out.passed = all(c.ok for c in out.checks)
    out.status, out.model_claimed_status = res.status, res.model_claimed_status
    out.total_eur = (final.order or {}).get("total_eur")
    out.tools = [s.tool for s in res.trace]
    out.explanation, out.steps, out.tokens = res.explanation, res.steps, res.tokens
    out.seconds = round(time.monotonic() - t0, 2)
    return out


def run_bench(scenarios: list[Scenario], llm_factory: LLMFactory, *, replay: str = "auto",
              record_api: bool = False, bench_dir: Path = BENCH_DIR, save: bool = True,
              progress: Callable[[ScenarioResult], None] | None = None,
              baseline: bool = True) -> BenchReport:
    # The bench measures the buyer agent; Valio's review agent would only add model calls.
    old = os.environ.get("TTA_VALIO_REVIEW")
    os.environ["TTA_VALIO_REVIEW"] = "off"
    try:
        return _run_bench(scenarios, llm_factory, replay=replay, record_api=record_api,
                          bench_dir=bench_dir, save=save, progress=progress, baseline=baseline)
    finally:
        if old is None:
            os.environ.pop("TTA_VALIO_REVIEW", None)
        else:
            os.environ["TTA_VALIO_REVIEW"] = old


def _run_bench(scenarios: list[Scenario], llm_factory: LLMFactory, *, replay: str,
               record_api: bool, bench_dir: Path, save: bool,
               progress: Callable[[ScenarioResult], None] | None,
               baseline: bool) -> BenchReport:
    started = datetime.now().isoformat(timespec="seconds")
    results = []
    for sc in scenarios:
        r = run_scenario(sc, llm_factory, replay=replay, record_api=record_api,
                         bench_dir=bench_dir)
        results.append(r)
        if progress:
            progress(r)
    # The model name comes from the runs; build a model only if every scenario was skipped
    # before reaching it (no cassettes).
    model = next((r.model for r in results if r.model), "") or llm_factory().name
    base = None
    if baseline:
        base = {sc.id: run_scenario(sc, NullLLM, bench_dir=bench_dir).passed
                for sc in scenarios if sc.id in {r.id for r in results if not r.skipped}}
    report = BenchReport(model, started, results, base)
    if save:
        path = bench_dir / "results" / f"{_slug(model)}-{started.replace(':', '')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str))
    return report
