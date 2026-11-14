"""Trusted synthetic techcards: numerical oracle plus actual draft pipeline controls."""
from __future__ import annotations

import json
import runpy

import pytest

from trade_agents.bench.menu_eval import MENU_DIR, evaluate_menu_case, load_menu_cases
from trade_agents.llm import LLMResponse, ScriptedLLM

SUITE = MENU_DIR / 'techcards-v1'


def test_standalone_decimal_oracle_validates_frozen_inputs_without_rewriting():
    namespace = runpy.run_path(str(SUITE / 'validate_fixture.py'))
    namespace['validate_fixture']()
    with pytest.raises(ValueError, match='Missing per-portion quantity: oat drink'):
        namespace['calculate'](namespace['read']('inputs/menu.json'),
                               namespace['read']('inputs/recipe-cards-missing.json'),
                               namespace['read']('inputs/inventory.json'))


def response_for(case):
    return {'needs': [{key: value for key, value in row.items() if key != 'aliases'}
                      for row in case['expect']['needs']],
            'assumptions': ['Trusted synthetic cards, chef must review quantities.'], 'warnings': []}


def evaluate(case, response):
    return evaluate_menu_case(case, ScriptedLLM([LLMResponse(text=json.dumps(response))],
                                              name='scripted:techcards-control'))


def test_three_cards_two_batches_exercise_review_only_pipeline(monkeypatch):
    from trade_agents.service import Gateway

    def forbidden(*args, **kwargs):
        raise AssertionError('Recipe arithmetic must not initialize purchasing')

    monkeypatch.setattr(Gateway, '__init__', forbidden)
    cases = load_menu_cases(SUITE)
    assert len(cases) == 1
    case = cases[0]
    assert len(case['expect']['needs']) == 18
    result = evaluate(case, response_for(case))
    assert result['passed'], result['checks']
    assert result['draft']['requires_review'] and result['draft']['requires_approval']
    assert result['draft']['status'] == 'draft'


@pytest.mark.parametrize('fault', ['skip_yield', 'double_yield', 'repeat_stock', 'use_late_receipt'])
def test_techcard_oracle_rejects_compound_arithmetic_errors(fault):
    case = load_menu_cases(SUITE)[0]
    data = response_for(case)
    rows = {(row['date'], row['ingredient']): row for row in data['needs']}
    if fault == 'skip_yield':
        rows['2026-11-20', 'carrot']['amount'] = 3.8  # net carrot coefficient treated as gross
    elif fault == 'double_yield':
        rows['2026-11-20', 'carrot']['amount'] = 7.4  # pasta carrot yield corrected twice
    elif fault == 'repeat_stock':
        rows['2026-11-21', 'pasta']['amount'] = 2  # reuse opening2 KG
    else:
        del data['needs'][next(index for index, row in enumerate(data['needs'])
                               if row['date'] == '2026-11-21' and row['ingredient'] == 'pasta')]
    result = evaluate(case, data)
    assert result['status'] == 'scored' and not result['passed']
    assert any(not check['ok'] for check in result['checks'])


def test_missing_coefficient_spec_is_excluded_from_procurement_scores():
    spec = json.loads((SUITE / 'specifications/02-missing-techcard-clarification.json').read_text())
    assert spec['execution_status'] == 'capability_gated_not_executable'
    assert spec['capability_gate']['score'] is None
    assert spec['capability_gate']['included_in_executable_suite'] is False
    assert spec['exact_oracle']['status'] == 'needs_clarification'
    assert spec['exact_oracle']['orders_created'] == 0


def test_contradictory_ingredient_hint_cannot_receive_exact_identity_credit():
    case = load_menu_cases(SUITE)[0]
    data = response_for(case)
    row = next(row for row in data['needs'] if row['ingredient'] == 'berries')
    row['search_hint_fi'] = 'kaurajuoma'
    result = evaluate(case, data)
    assert not result['passed']
    assert result['line_metrics']['ambiguous_identity_lines']
    assert any(check['name'] == 'quality.exact_reference_lines' and not check['ok']
               for check in result['checks'])
