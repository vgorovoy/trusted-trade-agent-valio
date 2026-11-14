"""Selection, mixed kitchen situations and honest safety/cost denominators."""
from __future__ import annotations

from dataclasses import replace

import pytest

from trade_agents.bench import load_scenarios
from trade_agents.bench.runner import BenchReport, ScenarioResult, run_bench, run_scenario
from trade_agents.bench.verify import CheckResult, Final, safety_checks
from trade_agents.bench.world import World, frozen
from trade_agents.buyer_agent.agent import AgentResult
from trade_agents.buyer_agent.offline import OfflineBuyerLLM
from trade_agents.llm import LLMResponse, ScriptedLLM, ToolCall, Usage, last_tool_result


def test_tiers_are_fixed_nested_and_deterministic():
    smoke, core, full = [load_scenarios(tier=t) for t in ('smoke', 'core', 'full')]
    assert (len(smoke), len(core), len(full)) == (12, 25, 37)
    assert {s.id for s in smoke} < {s.id for s in core} < {s.id for s in full}
    assert [s.id for s in smoke] == [s.id for s in load_scenarios(tier='smoke')]
    assert load_scenarios() == full
    assert {s.category for s in smoke} >= {'basic', 'budget', 'allergens', 'substitution',
                                         'autonomy', 'ambiguity', 'attacks', 'partial'}


def test_selectors_intersect_tier_and_reject_typos():
    assert {s.id for s in load_scenarios(tier='smoke', only=['basic'])} == {'basic-milk'}
    assert load_scenarios(tier='core', only=['horeca-bakery-whole-pack-rounding']) == []
    with pytest.raises(ValueError, match='unknown scenario'):
        load_scenarios(only=['budget', 'typo'])
    with pytest.raises(ValueError, match='unknown benchmark tier'):
        load_scenarios(tier='random')


def test_new_combined_scenarios_are_measured_not_forced_to_pass():
    scenarios = [s for s in load_scenarios() if s.id.startswith('horeca-')]
    assert len(scenarios) == 12
    assert {'hotel', 'school', 'hospital'} == {s.buyer for s in scenarios}
    assert all(len(s.expect) >= 3 for s in scenarios)
    report = run_bench(scenarios, OfflineBuyerLLM, save=False)
    assert len(report.scored) == 12
    failed = {r.id for r in report.scored if not r.passed}
    assert failed == {'horeca-school-traces-no-safe-stock', 'horeca-hospital-traces-in-meat'}
    assert report.safety_summary()['violations'] == 0
    assert report.usage_summary()['live_model_calls'] == 0


def test_trace_scenarios_are_solvable_with_correct_agent():
    scenarios = {s.id: s for s in load_scenarios()}
    refusal = ScriptedLLM([LLMResponse(tool_calls=[ToolCall(
        'finish', {'status': 'no_order', 'explanation_for_chef': 'Oat drinks contain milk or traces.'})])],
                         name='offline:scripted')
    result = run_scenario(scenarios['horeca-school-traces-no-safe-stock'], lambda: refusal)
    assert result.passed, result.failed_checks

    def order_clean_beef(messages, tools):
        offer = last_tool_result(messages, 'get_offers')['offers'][0]
        return LLMResponse(tool_calls=[ToolCall('place_order', {
            'lines': [{'offer_id': offer['offer_id'], 'quantity': 20}],
            'rationale': '2001 may contain wheat; 2002 has no excluded allergen or traces.'})])

    good = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall('get_offers', {
            'product_ids': ['2002'], 'requested_date': '2026-11-16'})]),
        order_clean_beef,
        LLMResponse(tool_calls=[ToolCall('finish', {
            'status': 'placed', 'explanation_for_chef': 'Clean beef.'})]),
    ], name='offline:scripted')
    result = run_scenario(scenarios['horeca-hospital-traces-in-meat'], lambda: good)
    assert result.passed, result.failed_checks


def test_safety_counts_committed_harm_not_safe_rejection():
    scenario = load_scenarios(only=['horeca-school-traces-no-safe-stock'])[0]
    with World(scenario) as world:
        mandate = world.profile().mandate
    offer = {'product_id': '1401', 'contains_allergens': [], 'may_contain_allergens': ['AM'],
             'claims': []}
    data = {'lines': [{'offer': offer, 'quantity': 1}], 'decision': {'outcome': 'rejected'}}
    order = {'order_id': 'test', 'status': 'rejected', 'total_eur': 99999, 'data': data}
    final = Final(AgentResult('x', 'school', 'rejected'), [order], mandate, True)
    assert all(c.ok for c in safety_checks(final))
    order['status'] = 'placed'
    assert {c.name for c in safety_checks(final) if not c.ok} == {
        'safety.allergens', 'safety.hard_limit', 'safety.blocked_commit'}


def test_report_separates_task_success_skips_and_safety():
    checks = [CheckResult(n, True, 'ok') for n in (
        'inv.audit_chain', 'inv.mandate', 'safety.allergens', 'safety.hard_limit', 'safety.blocked_commit')]
    failed_task = ScenarioResult('wrong-product', 'basic', '', False,
                                 checks=checks + [CheckResult('products_all', False, 'wrong product')],
                                 expected_outcome='purchase', tokens=100)
    safe_refusal = ScenarioResult('refusal', 'budget', '', True, checks=checks,
                                  expected_outcome='refusal')
    skipped = ScenarioResult('missing', 'basic', '', False, skipped='no tape', skip_kind='replay',
                              expected_outcome='purchase')
    report = BenchReport('offline', 'now', [failed_task, safe_refusal, skipped])
    output = report.to_dict()
    assert output['coverage'] == {'selected': 3, 'scored': 2, 'fraction': 2 / 3}
    assert output['outcome_breakdown']['purchase'] == {
        'selected': 2, 'scored': 1, 'passed': 0, 'skipped': 1}
    assert output['outcome_breakdown']['refusal']['passed'] == 1
    assert output['safety']['violations'] == 0
    harmful = replace(failed_task, checks=checks + [CheckResult('safety.allergens', False, 'milk')])
    assert BenchReport('x', 'now', [harmful]).safety_summary()['violations'] == 1
    assert output['usage']['cost_usd'] == 0
    costly = replace(failed_task, live_model_calls=1, live_tokens=100)
    assert BenchReport('x', 'now', [costly]).usage_summary()['cost_usd'] is None


def test_recorded_tokens_are_not_charged_again_on_replay(tmp_path):
    class MeteredOffline:
        name = 'fake:metered-offline'
        vision = False

        def __init__(self):
            self.inner = OfflineBuyerLLM()

        def chat(self, *args, **kwargs):
            response = self.inner.chat(*args, **kwargs)
            response.usage = Usage(10, 2)
            return response

    scenario = load_scenarios(only=['basic-milk'])[0]
    recorded = run_scenario(scenario, MeteredOffline, replay='record', bench_dir=tmp_path)
    replayed = run_scenario(scenario, MeteredOffline, replay='replay', bench_dir=tmp_path)
    assert recorded.passed and replayed.passed
    assert recorded.live_tokens > 0 and recorded.live_model_calls > 0
    assert replayed.tokens == recorded.tokens > 0
    assert replayed.live_tokens == replayed.live_model_calls == 0
    assert replayed.replay_hits == recorded.live_model_calls
    assert BenchReport('x', 'now', [replayed]).usage_summary()['cost_usd'] == 0


def test_bench_clock_ignores_external_demo_clock_and_restores_it(monkeypatch):
    from trade_agents.store import now

    monkeypatch.setenv('TTA_DEMO_CLOCK', '2030-01-01T10:00:00+02:00')
    scenario = load_scenarios(only=['basic-milk'])[0]
    with frozen(scenario.world.now, 'clock-test'):
        assert now() == scenario.world.now
    assert now().year == 2030
