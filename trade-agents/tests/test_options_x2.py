"""Stream X2: always up to three options with absolute trade-offs, substitutes on every
interface, strict allergen names, the live-API cap, and what the buyer agent sees."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from conftest import AVAIL, PRICES
from fastapi.testclient import TestClient

from trade_agents import api, mcp_server
from trade_agents.buyer_agent import BuyerProfile, InProcessTransport, RestTransport
from trade_agents.buyer_agent.profiles import hotel, sign_mandate
from trade_agents.buyer_agent.tools import AgentTools, _compact_plan
from trade_agents.catalog import AllergenFilter, UnknownAllergen, allergen_code
from trade_agents.config import TZ, Settings
from trade_agents.policy import Mandate
from trade_agents.service import GatewayError, rank_options, trade_off

DAY = date(2026, 11, 16)


def candidate(pid, price, reliability, carbon):
    return {'total_eur': price, 'delivery_reliability': reliability, 'co2_kg_per_kg': carbon,
            'lines': [{'product_id': pid, 'quantity': 1, 'name': f'name {pid}'}]}


def same_metrics(gateway, monkeypatch, rows):
    for pid, price, rel, co2 in rows:
        monkeypatch.setitem(PRICES, pid, (price, price))
        monkeypatch.setitem(AVAIL, pid, deepcopy(AVAIL['100']))
        gateway.catalog.con.execute(
            'UPDATE products SET sales_unit="ST",base_per_sales=1,reliability=?,co2_per_kg=? '
            'WHERE id=?', (rel, co2, pid))
    gateway.catalog.con.commit()


# -- 1. up to three options -------------------------------------------------------------

def test_one_winner_takes_all_objectives_and_runner_ups_fill():
    options = [candidate('a', 100, .99, 1), candidate('b', 104, .9, 2),
               candidate('c', 110, .8, 3), candidate('d', 120, .7, 4)]
    r = rank_options(options)
    assert [o['lines'][0]['product_id'] for o in r] == ['a', 'b', 'c']
    assert [o['rank'] for o in r] == ['best_price', 'runner_up', 'third']
    assert r[0]['objectives'] == ['best_price', 'most_reliable', 'lowest_co2']
    assert r[1]['objectives'] == [] and r[2]['objectives'] == []


def test_two_winners_then_third():
    options = [candidate('a', 100, .8, 1), candidate('b', 104, .99, 2),
               candidate('c', 103, .7, 3)]
    r = rank_options(options)
    assert [(o['rank'], o['lines'][0]['product_id']) for o in r] == [
        ('best_price', 'a'), ('most_reliable', 'b'), ('third', 'c')]
    assert r[0]['objectives'] == ['best_price', 'lowest_co2']


def test_premium_cap_excludes_and_rules_out(gateway, monkeypatch):
    same_metrics(gateway, monkeypatch, [('100', 1.4, .9, 3), ('200', 1.45, .99, 2),
                                        ('300', 1.6, .95, 1)])
    r = gateway.plan('maito', 10, 'L', DAY, max_premium_pct=5)
    assert [o['total_eur'] for o in r['options']] == [14, 14.5]
    cut = [x for x in r['ruled_out'] if 'max_premium_pct' in x['reason']]
    assert len(cut) == 1 and cut[0]['product_id'] == '300'
    assert '+14.3 % vs the cheapest 14.00 EUR' in cut[0]['reason']
    assert r['live_api'] == {'products_queried': 3, 'cap': gateway.settings.max_live_products}


# -- 2. trade-offs with absolute values ---------------------------------------------------

def test_trade_off_absolute_values_and_missing_data():
    base = candidate('a', 202, .92, 1.2)
    text = trade_off(base, candidate('b', 210.08, .99, .95))
    assert text == ('+4.0 % price (202.00 → 210.08 EUR); reliability 0.92 → 0.99; '
                    'CO₂ 1.20 → 0.95 kg CO₂e/kg (category avg)')
    assert trade_off(base, base).startswith('cheapest option (202.00 EUR); reliability 0.92')
    no_base = candidate('a', 100, None, None)
    text = trade_off(no_base, candidate('b', 101, .9, 5.12))
    assert 'CO₂ 5.12 kg CO₂e/kg (category avg), cheapest option: no data' in text
    assert 'reliability 0.90, cheapest option: no data' in text
    assert 'reliability no data (cheapest option: 0.92)' in trade_off(
        base, candidate('b', 210, None, 1))


def test_plan_explanation_mentions_alternatives(gateway, monkeypatch):
    same_metrics(gateway, monkeypatch, [('100', 1.4, .9, 3), ('200', 1.5, .99, 2),
                                        ('300', 1.6, .95, 1)])
    r = gateway.plan('maito', 10, 'L', DAY)
    assert r['explanation'].count('Alternative (') == 2
    assert 'reliability 0.90 → 0.99' in r['explanation']


# -- 3. allergen names ----------------------------------------------------------------------

@pytest.mark.parametrize(('name', 'code'), [('milk', 'AM'), ('lactose', 'ML'), ('gluten', 'AW'),
                                            ('nuts', 'AN'), ('Maito', 'AM'), ('am', 'AM'),
                                            ('AW', 'AW'), ('NC', 'NC'), (' peanut ', 'AP')])
def test_allergen_synonyms(name, code):
    assert allergen_code(name) == code


def test_unknown_allergen_is_an_error_everywhere(gateway):
    with pytest.raises(UnknownAllergen):
        allergen_code('lactosee')
    with pytest.raises(GatewayError, match='unknown allergen'):
        gateway.plan('maito', 10, 'L', DAY, exclude_allergens=['lactosee'])
    with pytest.raises(GatewayError, match='unknown allergen'):
        gateway.search('maito', exclude_allergens=['nutz'])
    with pytest.raises(GatewayError, match='unknown allergen'):
        gateway.substitutes('100', {'exclude_allergens': ['nutz']})
    with pytest.raises(ValueError, match='unknown allergen'):
        Mandate(mandate_id='m', buyer_id='b', buyer_name='B', agent_id='a',
                valid_until=datetime.now(TZ), auto_order_limit_eur=1,
                excluded_allergens=['lactosee'])
    with pytest.raises(UnknownAllergen):     # not a GS1 code seen in the data
        allergen_code('ZZ')


# Real GS1 tags from the Valio catalogue: Kikkoman soy sauce ["AY", "UW"], MONIN Hazelnut
# syrup ["SH"], Ferrero Raffaello ["SA", ...], ordinary Valio milk ["AM"] without a claim.
@pytest.mark.parametrize(('names', 'tags', 'claims', 'hit'), [
    (['gluten'], ['AY', 'UW'], [], {'UW'}),
    (['wheat'], ['AW'], [], {'AW'}),
    (['gluten'], ['GO'], [], set()),                       # oats are not gluten here
    (['nuts'], ['SH'], [], {'SH'}),
    (['tree nuts'], ['SA', 'AM'], [], {'SA'}),
    (['AN'], ['SW'], [], {'SW'}),
    (['nuts'], ['AP'], [], set()),                         # peanuts stay separate
    (['nuts and peanuts'], ['AP'], [], {'AP'}),
    (['milk'], ['ML'], [], {'ML'}),
    (['lactose'], ['AM'], [], {'AM'}),
    (['lactose'], ['AM'], ['FREE_FROM_LACTOSE'], set()),
    (['lactose'], ['AM'], ['LOW_LACTOSE'], set()),
    (['laktoosi'], ['ML'], ['FREE_FROM_LACTOSE'], {'ML'}),
])
def test_allergen_families(names, tags, claims, hit):
    assert AllergenFilter.parse(names).hits(tags, claims) == hit


def test_may_contain_uses_the_same_sets():
    f = AllergenFilter.parse(['nuts'])
    assert f.reason([], ['SH'], [], exclude_may_contain=False) is None
    assert f.reason([], ['SH'], [], exclude_may_contain=True) == 'may contain hazelnut'
    assert AllergenFilter.parse(['lactose']).reason([], ['AM'], [], True) == 'may contain milk'


def tag(gateway, pid, contains, claims=(), may_contain=()):
    gateway.catalog.con.execute(
        'UPDATE products SET contains=?, claims=?, may_contain=? WHERE id=?',
        (json.dumps(contains), json.dumps(list(claims)), json.dumps(list(may_contain)), pid))
    gateway.catalog.con.commit()


def test_one_predicate_for_search_plan_substitutes_and_mandate(gateway, sign, mandate):
    from trade_agents.service import OrderLineIn

    tag(gateway, '400', ['AY', 'UW'])                      # "Kikkoman"
    assert '400' not in [p['product_id'] for p in gateway.search('kaurajuoma',
                                                                 exclude_allergens=['gluten'])]
    r = gateway.plan('x', 10, 'L', DAY, product_ids=['400'], exclude_allergens=['gluten'])
    assert not r['options'] and 'contains wheat' in r['ruled_out'][0]['reason']
    tag(gateway, '400', ['SH'])                            # "MONIN Hazelnut"
    r = gateway.plan('x', 10, 'L', DAY, product_ids=['400'], exclude_allergens=['nuts'])
    assert not r['options']
    # ordinary milk (AM, no lactose claim) is not a lactose-free substitute
    tag(gateway, '400', ['AM'])
    tag(gateway, '100', ['AM'], ['FREE_FROM_LACTOSE'])
    res = gateway.substitutes('300', {'exclude_allergens': ['lactose']})
    assert [o['product_id'] for o in res['options']] == ['100', '200']  # lactose-free milk passes
    assert any(x['product_id'] == '400' and 'milk' in x['reason'] for x in res['ruled_out'])
    # the mandate check uses the same predicate
    m = sign(mandate.model_copy(update={'excluded_allergens': ['lactose'],
                                        'required_claims': []}))
    offer = gateway.quote(['400'], DAY)[0]
    order = gateway.place_order(m, [OrderLineIn(offer_id=offer['offer_id'], quantity=1)],
                                'x2-lactose-key', 'test')
    assert order['status'] == 'rejected'
    assert any('contains milk' in r for r in order['data']['decision']['reasons'])


# -- 3. substitutes on REST / MCP / CLI ---------------------------------------------------------

@pytest.fixture
def client(gateway):
    api.app.dependency_overrides[api.gateway] = lambda: gateway
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def milk_free_oat(gateway):
    gateway.catalog.con.execute('UPDATE products SET claims=? WHERE id="400"',
                                (json.dumps(['FREE_FROM_LACTOSE']),))
    gateway.catalog.con.commit()
    AVAIL['400'] = deepcopy(AVAIL['100'])


def test_rest_substitutes_milk_free_with_live_offer(gateway, client, monkeypatch):
    monkeypatch.setitem(AVAIL, '400', AVAIL['400'])
    milk_free_oat(gateway)
    r = client.get('/v1/products/100/substitutes', params={
        'exclude_allergens': ['milk'], 'requested_date': DAY.isoformat(), 'amount': 20,
        'unit': 'L'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [o['product_id'] for o in body['options']] == ['400']
    opt = body['options'][0]
    assert 'milk' not in opt['allergens_contains']
    assert opt['orderable'] and opt['offer']['offer_id'] and opt['offer']['quantity_needed'] == 20
    assert opt['differences'][0].startswith('price per unit 1.40 → 2.00 EUR (+42.9 %)')
    assert {x['product_id'] for x in body['ruled_out']} >= {'200', '300'}
    # static only: no live offer
    static = client.get('/v1/products/300/substitutes').json()
    assert static['options'][0]['product_id'] == '100' and 'offer' not in static['options'][0]
    assert client.get('/v1/products/100/substitutes',
                      params={'exclude_allergens': ['lactosee']}).status_code == 400
    assert client.get('/v1/products/nope/substitutes').status_code == 404
    assert client.get('/v1/products/search',
                      params={'q': 'maito', 'exclude_allergens': ['xyzzy']}).status_code == 400


def test_mcp_and_cli_substitutes(gateway, monkeypatch, capsys):
    from trade_agents import cli, service

    milk_free_oat(gateway)
    monkeypatch.setattr(mcp_server, 'gw', lambda: gateway)
    res = mcp_server.find_substitutes('100', exclude_allergens=['milk'])
    assert [o['product_id'] for o in res['options']] == ['400']
    monkeypatch.setattr(service, 'Gateway', lambda: gateway)
    cli.main(['substitutes', '100', '--exclude', 'milk'])
    assert [o['product_id'] for o in json.loads(capsys.readouterr().out)['options']] == ['400']
    with pytest.raises(SystemExit, match='unknown allergen'):
        cli.main(['substitutes', '100', '--exclude', 'lactosee'])


# -- live API cap (Valio: never query all products) ------------------------------------------

def test_live_api_cap(gateway, client, monkeypatch):
    gateway.settings = replace(gateway.settings, max_live_products=2)
    with pytest.raises(GatewayError, match='narrow candidates with search_products'):
        gateway.plan('x', 10, 'L', DAY, product_ids=['100', '200', '300'])
    with pytest.raises(GatewayError, match='cap is 2'):
        gateway.quote(['100', '200', '300'], DAY)
    r = client.post('/v1/offers', json={'product_ids': ['100', '200', '300'],
                                        'requested_date': DAY.isoformat()})
    assert r.status_code == 400 and 'cap is 2' in r.json()['detail']
    r = client.post('/v1/plans', json={'query': 'x', 'amount': 10, 'unit': 'L',
                                       'requested_date': DAY.isoformat(),
                                       'product_ids': ['100', '200', '300']})
    assert r.status_code == 400
    # search-driven plans and substitutes stay under the cap by construction
    assert gateway.plan('maito', 10, 'L', DAY)['candidates_considered'] <= 2
    before = gateway.live_lookups
    res = gateway.substitutes('300', requested_date=DAY, limit=10)
    assert len(res['options']) == 1          # limit clipped to cap - 1 (original is quoted too)
    assert gateway.live_lookups - before == 4  # 2 products x (price + availability)
    monkeypatch.setenv('TTA_MAX_LIVE_PRODUCTS', '7')
    assert Settings().max_live_products == 7


# -- 4. buyer agent ------------------------------------------------------------------------------

def test_compact_plan_keeps_ranking_fields():
    plan = {'options': rank_options([candidate('a', 100, .9, None),
                                     candidate('b', 103, .99, 2)])}
    c = _compact_plan(plan)
    assert c['options'][0]['rank'] == 'best_price'
    assert c['options'][1]['objectives'] == ['most_reliable', 'lowest_co2']
    assert '+3.0 % price (100.00 → 103.00 EUR)' in c['options'][1]['trade_off']


class Spy(InProcessTransport):
    def plan(self, *a, **kw):
        self.plan_kw = kw
        return super().plan(*a, **kw)


def school_profile(gateway, policy='approval'):
    m = Mandate(mandate_id='m-school', buyer_id='school-1', buyer_name='School',
                agent_id='school-agent', valid_until=datetime.now(TZ) + timedelta(days=1),
                auto_order_limit_eur=500, excluded_allergens=['milk'],
                substitution_policy=policy, approvers=['manager@school'])
    return BuyerProfile(key='school', kitchen='School', mandate=sign_mandate(
        m, gateway.settings.buyers_dir))


def test_agent_tools_pass_preferences_and_find_substitutes(gateway, monkeypatch):
    milk_free_oat(gateway)
    monkeypatch.setitem(AVAIL, '400', AVAIL['400'])
    profile = replace(school_profile(gateway), optimize='balanced', max_premium_pct=5)
    tools = AgentTools(Spy(gateway), profile)
    out = tools.call('plan_need', {'query': 'maito', 'amount': 10, 'unit': 'L',
                                   'requested_date': DAY.isoformat(), 'product_ids': ['100']})
    assert tools.transport.plan_kw == {'optimize': 'balanced', 'max_premium_pct': 5}
    assert not out.for_model['options']            # milk is excluded by the mandate
    out = tools.call('find_substitutes', {'product_id': '100',
                                          'requested_date': DAY.isoformat(),
                                          'amount': 10, 'unit': 'L'})
    assert not out.error, out.for_model
    assert [o['product_id'] for o in out.for_model['options']] == ['400']
    assert out.for_model['options'][0]['offer']['offer_id']
    assert out.for_model['substitution_policy'] == 'approval'
    never = AgentTools(InProcessTransport(gateway), school_profile(gateway, 'never'))
    assert not never.call('find_substitutes', {'product_id': '100'}).for_model['options']


def test_rest_transport_substitutes_and_plan_preferences(gateway, client, monkeypatch):
    milk_free_oat(gateway)
    rest = RestTransport('http://testserver', client=client)
    res = rest.substitutes('100', {'exclude_allergens': ['milk'], 'require_claims': []},
                           None, None, None, 'agent')
    assert [o['product_id'] for o in res['options']] == ['400']
    assert not rest.substitutes('100', {'substitution_policy': 'never'}, None, None, None,
                                'agent')['options']
    plan = rest.plan('maito', 10, 'L', DAY.isoformat(), [], [], ['100', '300'], 'agent',
                     optimize='balanced', max_premium_pct=5)
    assert plan['need']['optimize'] == 'balanced' and plan['need']['max_premium_pct'] == 5


def test_hotel_profile_prefers_balanced_within_five_percent(monkeypatch, tmp_path):
    monkeypatch.setenv('TTA_DATA_DIR', str(tmp_path))
    p = hotel()
    assert (p.optimize, p.max_premium_pct) == ('balanced', 5)
    assert 'optimize=balanced, at most 5 % above the cheapest' in p.context(DAY)


def test_substitutes_fall_back_to_category_family_with_vegan_implication(gateway):
    con = gateway.catalog.con
    con.execute('UPDATE products SET category="10400"')
    con.execute('UPDATE products SET category="10402", claims=? WHERE id="400"',
                (json.dumps(['VEGAN']),))
    con.execute('UPDATE products SET category="99999" WHERE id="200"')
    con.commit()
    res = gateway.substitutes('100', {'exclude_allergens': ['milk']})
    assert [o['product_id'] for o in res['options']] == ['400']
    diffs = ' | '.join(res['options'][0]['differences'])
    assert 'related category' in diffs and 'check with the kitchen' in diffs
    assert 'implied by VEGAN' in diffs
    # milk traces: no lactose-free inference from VEGAN
    con.execute('UPDATE products SET may_contain=\'["AM"]\' WHERE id="400"')
    assert not gateway.substitutes('100', {'exclude_allergens': ['milk']})['options']
    con.execute('UPDATE products SET may_contain="[]", claims="[]" WHERE id="400"')
    assert not gateway.substitutes('100', {'exclude_allergens': ['milk']})['options']


def test_family_fallback_below_three_safe_in_category(gateway):
    con = gateway.catalog.con
    con.execute('UPDATE products SET category="10400"')
    con.execute('UPDATE products SET category="10402", claims=? WHERE id="400"',
                (json.dumps(['VEGAN']),))
    con.execute('UPDATE products SET contains="[]" WHERE id="300"')   # one safe in category
    con.commit()
    res = gateway.substitutes('100', {'exclude_allergens': ['milk']})
    # one safe option in the category is too few: the family tier is added after it
    assert [o['product_id'] for o in res['options']] == ['300', '400']
    same, family = res['options']
    assert (same['tier'], same['needs_kitchen_check']) == ('category', False)
    assert (family['tier'], family['needs_kitchen_check']) == ('family', True)
    # three safe in the category: no family tier
    con.execute('UPDATE products SET contains="[]", claims=? WHERE id IN ("200", "300")',
                (json.dumps(['FREE_FROM_LACTOSE']),))
    con.execute('UPDATE products SET category="10400", contains="[]" WHERE id="400"')
    con.commit()
    res = gateway.substitutes('100', {'exclude_allergens': ['milk']})
    assert len(res['options']) == 3
    assert all(o['tier'] in ('official', 'category') for o in res['options'])


def test_dietary_claims_eggs_and_wheat_are_kept():
    from trade_agents.service import DIETARY_CLAIMS

    assert {'FREE_FROM_EGGS', 'FREE_FROM_WHEAT'} <= DIETARY_CLAIMS
