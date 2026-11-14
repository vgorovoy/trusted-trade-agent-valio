from copy import deepcopy
from datetime import date

import pytest
from conftest import AVAIL, PRICES

from trade_agents.api import PlanIn, plan
from trade_agents.service import GatewayError

DAY = date(2026, 11, 16)


def prepare(gateway, monkeypatch):
    for pid, price, rel, co2 in [('100', 1.4, .9, 3), ('200', 1.5, .99, 2), ('300', 1.6, .95, 1)]:
        monkeypatch.setitem(PRICES, pid, (price, price))
        monkeypatch.setitem(AVAIL, pid, deepcopy(AVAIL['100']))
        gateway.catalog.con.execute(
            'UPDATE products SET sales_unit="ST",base_per_sales=1,reliability=?,co2_per_kg=? WHERE id=?',
            (rel, co2, pid))
    gateway.catalog.con.commit()


def test_three_objectives_and_tradeoffs(gateway, monkeypatch):
    prepare(gateway, monkeypatch)
    r = gateway.plan('maito', 10, 'L', DAY)
    assert [o['rank'] for o in r['options']] == ['best_price', 'most_reliable', 'lowest_co2']
    assert [o['total_eur'] for o in r['options']] == [14, 15, 16]
    assert 'reliability 0.90 → 0.99' in r['options'][1]['trade_off']
    assert '+7.1 % price (14.00 → 15.00 EUR)' in r['options'][1]['trade_off']
    assert 'CO₂ 3.00 → 1.00 kg CO₂e/kg (category avg)' in r['options'][2]['trade_off']


@pytest.mark.parametrize(('optimize', 'pid'), [('price', '100'), ('reliability', '200'),
                                              ('co2', '300'), ('balanced', '200')])
def test_preferences(gateway, monkeypatch, optimize, pid):
    prepare(gateway, monkeypatch)
    r = gateway.plan('maito', 10, 'L', DAY, optimize=optimize)
    assert r['options'][0]['lines'][0]['product_id'] == pid


def test_premium_cap_and_unknown(gateway, monkeypatch):
    prepare(gateway, monkeypatch)
    r = gateway.plan('maito', 10, 'L', DAY, optimize='co2', max_premium_pct=8)
    assert max(o['total_eur'] for o in r['options']) <= 15
    gateway.catalog.con.execute('UPDATE products SET reliability=NULL,co2_per_kg=NULL')
    r = gateway.plan('maito', 10, 'L', DAY)
    # No metric known: no objective winner besides price, the next cheapest fill the places.
    assert [o['rank'] for o in r['options']] == ['best_price', 'runner_up', 'third']
    assert [o['objectives'] for o in r['options']] == [['best_price'], [], []]
    assert 'reliability no data; CO₂ no data' in r['options'][0]['trade_off']


def test_explicit_candidates_obey_constraints(gateway):
    r = gateway.plan('maito', 10, 'L', DAY, product_ids=['100'], exclude_allergens=['milk'])
    assert not r['options']
    assert 'constraint' in r['ruled_out'][0]['reason']


def test_rest_and_mcp_parameters(gateway, monkeypatch):
    from trade_agents import mcp_server

    prepare(gateway, monkeypatch)
    body = PlanIn(query='maito', amount=10, unit='L', requested_date=DAY,
                  optimize='co2', max_premium_pct=20)
    assert plan(body, gateway)['options'][0]['rank'] == 'lowest_co2'
    monkeypatch.setattr(mcp_server, 'gw', lambda: gateway)
    assert mcp_server.plan_need('maito', 10, 'L', DAY.isoformat(), optimize='reliability',
                                max_premium_pct=20)['options'][0]['rank'] == 'most_reliable'
    assert PlanIn(query='x', amount=1, unit='L', requested_date=DAY).optimize == 'price'


@pytest.mark.parametrize('kwargs', [{'optimize': 'wrong'}, {'max_premium_pct': -1}])
def test_invalid_preferences(gateway, kwargs):
    with pytest.raises(GatewayError):
        gateway.plan('maito', 10, 'L', DAY, **kwargs)


def test_split_unknown_carbon(gateway):
    r = gateway.plan('maito', 1000, 'L', DAY)
    split = next(o for o in r['options'] if len(o['lines']) == 2)
    assert split['delivery_reliability'] == .8
    assert split['co2_kg_per_kg'] is None


def test_explicit_may_contain_and_claims(gateway):
    gateway.catalog.con.execute('UPDATE products SET may_contain=\'["AM"]\',contains=\'[]\' WHERE id="100"')
    r = gateway.plan('x', 10, 'L', DAY, product_ids=['100'],
                     exclude_allergens=['milk'], exclude_may_contain=True)
    assert not r['options']
    r = gateway.plan('x', 10, 'L', DAY, product_ids=['100'], require_claims=['VEGAN'])
    assert not r['options']


def test_mass_weighted_split(gateway):
    from trade_agents.offers import Content
    from trade_agents.service import offer_from_dict

    quote = gateway.quote(['100'], DAY)[0]
    fields = {k: v for k, v in quote.items() if k not in {
        'offer_id', 'quantity_needed', 'valid_until', 'signature'}}
    first = offer_from_dict(fields)
    second = offer_from_dict(fields)
    first.content = Content(2, 'KG', 'name')
    second.content = Content(1, 'KG', 'name')
    first.co2_kg_per_kg, second.co2_kg_per_kg = 2, 8
    first.delivery_reliability, second.delivery_reliability = .99, .8
    option = gateway._option([(first, 2), (second, 2)], 6, 'KG', 'split')
    assert option['co2_kg_per_kg'] == 4
    assert option['delivery_reliability'] == .8
    second.delivery_reliability = None
    second.co2_kg_per_kg = None
    option = gateway._option([(first, 2), (second, 2)], 6, 'KG', 'split')
    assert option['delivery_reliability'] is None
    assert option['co2_kg_per_kg'] is None


def ranking_candidate(pid, price, reliability, carbon):
    return {'total_eur': price, 'delivery_reliability': reliability, 'co2_kg_per_kg': carbon,
            'lines': [{'product_id': pid, 'quantity': 1}]}


def test_balanced_selects_compromise_outside_individual_winners():
    from trade_agents.service import rank_options

    options = [ranking_candidate('cheap', 100, .8, 10),
               ranking_candidate('reliable', 110, 1, 10),
               ranking_candidate('green', 110, .8, 1),
               ranking_candidate('compromise', 105, .95, 3)]
    result = rank_options(options, optimize='balanced')
    assert len(result) == 3
    assert result[0]['lines'][0]['product_id'] == 'compromise'
    assert result[0]['rank'] == 'balanced'
    assert result[0]['objectives'] == ['balanced']
    capped = rank_options(options, optimize='balanced', max_premium_pct=0)
    assert len(capped) == 1
    assert capped[0]['lines'][0]['product_id'] == 'cheap'


@pytest.mark.parametrize('optimize', ['price', 'reliability', 'co2', 'balanced'])
def test_unknown_metrics_never_claim_objective_wins(optimize):
    from trade_agents.service import rank_options

    result = rank_options([ranking_candidate('cheap', 100, None, None)], optimize=optimize)
    assert len(result) == 1
    assert set(result[0]['objectives']) <= {'best_price', 'balanced'}
    assert result[0]['rank'] not in {'lowest_co2', 'most_reliable'}
    assert 'reliability no data; CO₂ no data' in result[0]['trade_off']
    if optimize in {'reliability', 'co2'}:
        assert 'no ' in result[0]['optimization_note']


@pytest.mark.parametrize(('optimize', 'missing'), [('co2', 'lowest_co2'),
                                                  ('reliability', 'most_reliable')])
def test_cap_removing_known_metrics_does_not_claim_winner(optimize, missing):
    from trade_agents.service import rank_options

    options = [ranking_candidate('cheap', 100, None, None),
               ranking_candidate('known', 120, .99, 1)]
    result = rank_options(options, optimize=optimize, max_premium_pct=10)
    assert len(result) == 1
    assert result[0]['rank'] == 'best_price'
    assert result[0]['objectives'] == ['best_price']
    assert missing not in result[0]['objectives']
    assert 'premium' in result[0]['optimization_note']


def test_only_available_metric_is_claimed():
    from trade_agents.service import rank_options

    result = rank_options([ranking_candidate('known', 100, .99, None)], optimize='co2')
    assert result[0]['rank'] == 'best_price'
    assert result[0]['objectives'] == ['best_price', 'most_reliable']
