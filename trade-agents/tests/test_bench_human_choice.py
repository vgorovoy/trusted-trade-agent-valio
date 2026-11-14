"""Versioned human-resolution cases exercise the actual gateway, without live APIs."""

from dataclasses import replace

import pytest

from trade_agents.bench import load_scenarios, run_bench
from trade_agents.bench.runner import NullLLM, run_scenario
from trade_agents.bench.verify import Final, verify
from trade_agents.buyer_agent.agent import AgentResult
from trade_agents.llm import LLMResponse, ScriptedLLM, ToolCall


def call(name, **args):
    return LLMResponse(tool_calls=[ToolCall(name, args)])


def choice_agent():
    return ScriptedLLM([
        call("search_products", query="maito", require_claims=["FREE_FROM_LACTOSE"]),
        call("plan_need", query="maito", product_ids=["1001", "1002"], amount=200,
             unit="L", requested_date="2026-11-16"),
        call("finish", status="awaiting_choice", explanation_for_chef="Choose price or reliability."),
    ], name="offline:choice-script")


@pytest.mark.parametrize("suffix,final_status,product", [
    ("pending", "awaiting_choice", None),
    ("choose-price", "placed", "1001"),
    ("choose-reliability", "placed", "1002"),
    ("reject-both", "declined", None),
])
def test_human_choice_scenarios_use_real_gateway(suffix, final_status, product):
    sc = load_scenarios(only=[f"choice-price-reliability-{suffix}-v2"])[0]
    result = run_scenario(sc, choice_agent)
    assert result.passed, result.failed_checks
    assert result.agent_status == "awaiting_choice"
    assert result.status == final_status
    assert result.products == ([product] if product else [])
    assert result.live_model_calls == 0
    assert result.honest is True  # compares model claim with pre-human gateway state
    if sc.human:
        assert result.human_action["applied"]
        assert result.human_action["scripted"]
    else:
        assert result.human_action is None


def test_do_nothing_does_not_receive_credit_for_simulated_human_resolution():
    sc = load_scenarios(only=["choice-price-reliability-choose-reliability-v2"])[0]
    result = run_scenario(sc, NullLLM)
    assert not result.passed
    assert result.agent_status == "no_order"
    assert not result.human_action["applied"]
    assert result.products == []


def test_report_distinguishes_agent_escalation_and_simulated_human_action():
    scenarios = load_scenarios(only=["choice-price-reliability-pending-v2",
                                     "choice-price-reliability-choose-price-v2"])
    report = run_bench(scenarios, choice_agent, save=False, baseline=False)
    assert all(r.passed for r in report.results)
    summary = report.to_dict()["human_intervention"]
    assert summary["agent_awaiting_choice"] == 2
    assert summary["scripted_decisions_applied"] == 1
    assert "not model autonomy" in summary["scope"]


def test_legacy_reliability_oracle_is_not_rewritten():
    old = load_scenarios(only=["pref-reliability"])[0]
    new = load_scenarios(only=["pref-reliability-under-cap-v2"])[0]
    assert old.expect == {"status": "placed", "products_all": ["1002"], "covers": [200, "L"]}
    assert old.world.products["1002"]["price"] == 1.49
    assert new.world.products["1002"]["price"] == 1.40
    assert (1.40 / 1.35 - 1) * 100 < 5


def test_choice_checks_do_not_accept_missing_alternatives_or_early_commit():
    from trade_agents.bench.world import World

    sc = load_scenarios(only=["choice-price-reliability-pending-v2"])[0]
    with World(sc) as world:
        mandate = world.profile().mandate
        result = AgentResult(sc.request, sc.buyer, "awaiting_choice")
        final = Final(result, [], mandate, True, agent_status="awaiting_choice",
                      pre_human_orders=[{"status": "placed"}])
        checks = {c.name: c for c in verify(final, {
            "choice_count": 2, "choice_products": ["1001", "1002"],
            "committed_before_human_max": 0})}
        assert not checks["choice_count"].ok
        assert not checks["choice_products"].ok
        assert not checks["committed_before_human_max"].ok
        missing_choice = replace(final, pre_human_orders=[])
        assert not next(c for c in verify(missing_choice, {"choice_count": 2})
                        if c.name == "choice_count").ok


def test_reliable_option_within_premium_can_order_without_human():
    from trade_agents.llm import last_tool_result

    def order_first(messages, tools):
        option = last_tool_result(messages, "plan_need")["options"][0]
        return call("place_order", lines=[{"offer_id": ln["offer_id"], "quantity": ln["quantity"]}
                                         for ln in option["lines"]], rationale="Within price cap.")

    def agent():
        return ScriptedLLM([
            call("search_products", query="maito", require_claims=["FREE_FROM_LACTOSE"]),
            call("plan_need", query="maito", product_ids=["1001", "1002"], amount=200,
                 unit="L", requested_date="2026-11-16"),
            order_first,
            call("finish", status="placed", explanation_for_chef="Reliable milk within price cap."),
        ], name="offline:within-cap-script")

    sc = load_scenarios(only=["pref-reliability-under-cap-v2"])[0]
    result = run_scenario(sc, agent)
    assert result.passed, result.failed_checks
    assert result.status == result.agent_status == "placed"
    assert result.human_action is None


def test_simulated_human_cannot_turn_hard_block_into_order():
    sc = load_scenarios(only=["choice-price-reliability-choose-price-v2"])[0]
    blocked = replace(sc, mandate={"hard_limit_eur": 100})
    result = run_scenario(blocked, choice_agent)
    assert not result.passed  # failed to offer the expected two eligible baskets
    assert result.status != "placed"
    assert result.products == []
    assert not result.human_action["applied"]
    assert all(c.ok for c in result.checks if c.name.startswith("safety."))
