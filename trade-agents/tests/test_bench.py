"""Agent bench: scenario files, worlds, verifiers, record / replay, repeatability."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime

import httpx
import pytest

from trade_agents import store as store_mod
from trade_agents.bench import load_scenarios, run_bench
from trade_agents.bench.replay import ReplayLLM, ReplayMiss
from trade_agents.bench.runner import NullLLM, run_scenario
from trade_agents.bench.scenario import BENCH_DIR
from trade_agents.bench.verify import CHECKS, Final, verify
from trade_agents.bench.world import Cassette, CassetteMiss, World, frozen
from trade_agents.buyer_agent import tools as tools_mod
from trade_agents.buyer_agent.agent import AgentResult, TraceStep
from trade_agents.buyer_agent.offline import OfflineBuyerLLM
from trade_agents.config import TZ
from trade_agents.llm import LLMResponse, ScriptedLLM, ToolCall, last_tool_result

SCENARIOS = {s.id: s for s in load_scenarios()}


def call(name, **args) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name, args)])


def test_scenario_files():
    assert len(SCENARIOS) >= 37
    cats = {s.category for s in SCENARIOS.values()}
    assert {"basic", "cutoff", "partial", "budget", "allergens", "substitution",
            "preferences", "autonomy", "ambiguity", "attacks"} <= cats
    for s in SCENARIOS.values():
        assert set(s.expect) <= set(CHECKS), s.id
        assert s.world.now.tzinfo is not None


def test_offline_bench_is_repeatable():
    sc = list(SCENARIOS.values())
    a = run_bench(sc, OfflineBuyerLLM, save=False)
    b = run_bench(sc, OfflineBuyerLLM, save=False)
    strip = lambda r: {k: v for k, v in r.items() if k != "seconds"}  # noqa: E731
    assert [strip(r) for r in a.to_dict()["results"]] == [strip(r) for r in b.to_dict()["results"]]
    assert not [r for r in a.results if r.skipped]
    res = {r.id: r for r in a.results}
    # guardrail scenarios hold for any agent that follows the tools
    for sid in ("basic-milk", "budget-over-auto", "budget-cumulative", "mode-no-passport",
                "attack-product-text", "attack-request-text"):
        assert res[sid].passed, (sid, res[sid].failed_checks)
    # Valio mentors' strict mode: dearer top-ups and substitutes, hospital, agent-found ones
    for sid in ("partial-dearer-fill", "substitution-official-dearer",
                "substitution-hospital-strict", "substitution-agent-found",
                "substitution-official", "substitution-never"):
        assert res[sid].passed, (sid, res[sid].failed_checks)
    # the rule-based agent ignores the reliability floor: the bench must catch that
    assert not res["pref-reliability"].passed
    assert 0 < a.accuracy < 1 and "Accuracy:" in a.render()


def test_good_agent_passes_substitution():
    """A scripted 'good' agent shows the substitution check can pass."""
    def order_substitute(messages, tools):
        offer = last_tool_result(messages, "get_offers")["offers"][0]
        return call("place_order", rationale="1002 is sales-blocked; Valio lists 1001",
                    lines=[{"offer_id": offer["offer_id"], "quantity": 300,
                            "substitutes_for": "1002"}])
    steps = [call("get_product", product_id="1002"),
             call("get_offers", product_ids=["1001"], requested_date="2026-11-16"),
             order_substitute, call("finish", status="placed", explanation_for_chef="ok")]
    r = run_scenario(SCENARIOS["substitution-official"],
                     lambda: ScriptedLLM(list(steps), name="offline:scripted"))
    assert r.passed, r.failed_checks
    r = run_scenario(SCENARIOS["substitution-never"],
                     lambda: ScriptedLLM(list(steps), name="offline:scripted"))
    assert r.status == "rejected" and r.passed


def test_invariants_catch_a_tampered_mandate():
    sc = SCENARIOS["basic-milk"]
    with World(sc) as w:
        profile = w.profile()
        tampered = profile.mandate.model_copy(update={"auto_order_limit_eur": 99999})
        order = {"order_id": "ord_x", "total_eur": 1.0,
                 "data": {"mandate": tampered.model_dump(mode="json", exclude={"signature"}),
                          "lines": [], "decision": {"reasons": []}}}
        res = AgentResult(sc.request, "hotel", "placed")
        checks = {c.name: c for c in verify(Final(res, [order, order], profile.mandate, True),
                                            {"status": "placed"})}
    assert not checks["inv.mandate"].ok and not checks["inv.one_order"].ok
    assert checks["status"].ok


def test_api_scope_only_the_agents_candidates():
    """Valio: query price / availability only when needed, never for all products."""
    sc = SCENARIOS["basic-milk"]
    with World(sc) as w:
        mandate = w.profile().mandate
    res = AgentResult(sc.request, "hotel", "no_order")
    res.trace = [TraceStep(1, "plan_need", {"product_ids": ["1001", "1002"]}, "")]
    ok = {c.name: c for c in verify(Final(res, [], mandate, True, {"1001"}), {})}
    bad = {c.name: c for c in verify(Final(res, [], mandate, True, {"1001", "1003"}), {})}
    assert ok["inv.api_scope"].ok
    assert not bad["inv.api_scope"].ok and "1003" in bad["inv.api_scope"].detail
    report = run_bench(list(SCENARIOS.values()), OfflineBuyerLLM, save=False)
    for r in report.results:
        assert all(c.ok for c in r.checks if c.name == "inv.api_scope"), r.id
        assert r.api_products <= 3, r.id   # never the whole catalogue (11 products)


def test_frozen_clock_and_ids_are_restored():
    at = datetime(2026, 11, 13, 20, 0, tzinfo=TZ)
    with frozen(at, "seed"):
        assert store_mod.now() == at
        first = store_mod.uuid.uuid4()
        assert tools_mod.uuid.uuid4() != first          # one sequence for both modules
        assert uuid.uuid4() != uuid.uuid4()             # the global uuid4 stays random
    with frozen(at, "seed"):
        assert store_mod.uuid.uuid4() == first
    assert store_mod.now() > at.replace(year=2000)
    assert store_mod.datetime is datetime
    assert store_mod.uuid is uuid and tools_mod.uuid is uuid


def test_cassette_record_and_replay(tmp_path):
    hits = []

    def live(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        return httpx.Response(200, json={"products": [{"productId": "1"}]})

    path = tmp_path / "c.json"
    rec = Cassette(path, "record", httpx.MockTransport(live))
    body = {"products": ["1"], "requestedDate": "2026-11-16"}
    with httpx.Client(transport=rec, base_url="http://x") as c:
        assert c.post("/api/price", json=body).json()["products"][0]["productId"] == "1"
    rec.save()
    rep = Cassette(path, "replay")
    with httpx.Client(transport=rep, base_url="http://x") as c:
        # same body, keys in another order: still a hit
        assert c.post("/api/price", json=dict(reversed(body.items()))).status_code == 200
        with pytest.raises(CassetteMiss):
            c.post("/api/availability", json=body)
    assert hits == ["/api/price"]


def test_live_scenario_without_cassette_is_skipped(tmp_path):
    sc = SCENARIOS["basic-milk"]
    live = type(sc)(**{**sc.__dict__, "id": "live-x",
                       "world": type(sc.world)("live", sc.world.now, sc.world.delivery, "", {})})
    r = run_scenario(live, OfflineBuyerLLM, bench_dir=tmp_path)
    assert r.skipped and "cassette" in r.skipped


def test_replay_llm(tmp_path):
    path = tmp_path / "r.json"
    msgs = [{"role": "user", "content": "200 l milk"}]
    inner = ScriptedLLM([call("search_products", query="maito")], name="m1")
    rec = ReplayLLM(inner, path, "auto")
    first = rec.chat(msgs, system="s")
    rec.save()
    rep = ReplayLLM(None, path, "replay")
    again = rep.chat(msgs, system="s")
    assert rep.name == "m1" and again.tool_calls[0].args == {"query": "maito"}
    assert again.tool_calls[0].id == first.tool_calls[0].id
    with pytest.raises(ReplayMiss):
        rep.chat(msgs + [{"role": "user", "content": "more"}], system="s")
    assert json.loads(path.read_text())["__model__"] == "m1"


# -- provider errors and replay with OpenAI-compatible SDKs -------------------------------

class _HTTPError(Exception):
    """Shaped like the SDKs' status errors: openai `status_code`, google-genai `code`."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _Raising:
    name = "fake:raising"
    vision = False

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def chat(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize("exc", [_HTTPError(429), _HTTPError(503), TimeoutError("read")])
def test_provider_error_is_skipped_not_failed(tmp_path, exc):
    sc = SCENARIOS["basic-milk"]
    r = run_scenario(sc, lambda: _Raising(exc), bench_dir=tmp_path)
    assert r.skipped and r.skip_kind == "provider" and not r.checks
    report = run_bench([sc, SCENARIOS["ambiguous-no-amount"]], lambda: _Raising(exc),
                       bench_dir=tmp_path, save=False)
    assert report.scored == [] and report.skipped_by_kind() == {"provider": 2}
    assert "Skipped: 2 (provider errors)" in report.render()


def test_genai_and_openai_errors_are_provider_errors():
    import openai
    from google.genai import errors as genai_errors

    from trade_agents.llm import is_provider_error

    req = httpx.Request("POST", "http://x")
    assert is_provider_error(genai_errors.ClientError(429, {"error": {"message": "quota"}}))
    assert is_provider_error(genai_errors.ServerError(503, {"error": {"message": "busy"}}))
    assert not is_provider_error(genai_errors.ClientError(400, {"error": {"message": "bad"}}))
    assert is_provider_error(openai.APITimeoutError(request=req))
    assert is_provider_error(openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=req), body=None))
    assert not is_provider_error(ValueError("bad tool args"))


def test_replay_miss_is_skipped(tmp_path):
    sc = SCENARIOS["basic-milk"]
    r = run_scenario(sc, lambda: ScriptedLLM([], name="never-recorded"), replay="replay",
                     bench_dir=tmp_path)
    assert r.skipped and r.skip_kind == "replay" and "no recorded reply" in r.skipped


def test_agent_errors_still_fail(tmp_path):
    """A broken agent is not a provider problem: it fails, it is not skipped."""
    sc = SCENARIOS["basic-milk"]
    r = run_scenario(sc, lambda: _Raising(ValueError("malformed tool call")),
                     bench_dir=tmp_path)
    assert not r.skipped and not r.passed and r.status == "error"
    idle = run_scenario(sc, lambda: ScriptedLLM([], name="offline:idle"), bench_dir=tmp_path)
    assert not idle.skipped and not idle.passed
    assert run_scenario(sc, NullLLM, bench_dir=tmp_path).passed is False


def test_do_nothing_baseline_in_report():
    report = run_bench(list(SCENARIOS.values()), OfflineBuyerLLM, save=False)
    base = report.baseline_passed
    assert base is not None and 0 < base < sum(r.passed for r in report.scored)
    assert report.baseline["budget-hard-limit"] and not report.baseline["basic-milk"]
    assert "Do-nothing agent" in report.render()
    assert report.to_dict()["do_nothing_baseline"] == base


class _OpenAILike:
    """The offline agent behind an SDK that, like openai's, draws `uuid.uuid4()` for
    every request (idempotency key) and for tool calls without an id."""

    def __init__(self) -> None:
        self.inner = OfflineBuyerLLM()
        self.name = "fake-openai:offline"
        self.vision = False
        self.requests = 0

    def chat(self, *a, **k):
        self.requests += 1
        uuid.uuid4()
        resp = self.inner.chat(*a, **k)
        for tc in resp.tool_calls:
            tc.id = "call_" + uuid.uuid4().hex[:12]
        return resp


def test_record_then_replay_with_openai_like_sdk(tmp_path):
    shutil.copytree(BENCH_DIR / "scenarios", tmp_path / "scenarios")
    shutil.copytree(BENCH_DIR / "worlds", tmp_path / "worlds")
    sc = SCENARIOS["basic-milk"]
    live = _OpenAILike()
    rec = run_scenario(sc, lambda: live, replay="record", bench_dir=tmp_path)
    assert rec.passed and live.requests > 0, rec.failed_checks
    assert (tmp_path / "replays" / "fake-openai-offline" / "basic-milk.json").exists()
    again = _OpenAILike()
    rep = run_scenario(sc, lambda: again, replay="replay", bench_dir=tmp_path)
    assert not rep.skipped, rep.skipped
    assert rep.passed and again.requests == 0
    assert (rep.status, rep.products, rep.total_eur) == (rec.status, rec.products, rec.total_eur)
