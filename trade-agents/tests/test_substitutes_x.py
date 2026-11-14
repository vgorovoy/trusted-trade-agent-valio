import json

import pytest

from trade_agents.service import GatewayError


def test_official_first_then_similarity(gateway):
    result = gateway.substitutes('300')
    assert result['options'][0]['product_id'] == '100'
    assert result['options'][0]['official']
    assert result['options'][1]['product_id'] == '200'
    assert any('package' in d for d in result['options'][0]['differences'])


def test_school_milk_refusal_and_plant_alternative(gateway):
    constraints = {'excluded_allergens': ['milk'], 'exclude_may_contain': True}
    # Claims cannot be silently dropped even to make a milk-free substitute.
    result = gateway.substitutes('100', constraints)
    assert not result['options'] and result['reason']
    gateway.catalog.con.execute('UPDATE products SET claims=? WHERE id="400"',
                                (json.dumps(['FREE_FROM_LACTOSE']),))
    result = gateway.substitutes('100', constraints)
    assert [o['product_id'] for o in result['options']] == ['400']
    assert not result['options'][0]['official']
    gateway.catalog.con.execute('UPDATE products SET may_contain=? WHERE id="400"', ('["AM"]',))
    assert not gateway.substitutes('100', constraints)['options']
    constraints['exclude_may_contain'] = False
    assert gateway.substitutes('100', constraints)['options']


def test_mandate_claims_and_never(gateway, mandate):
    m = mandate.model_copy(update={'excluded_allergens': ['AM']})
    assert not gateway.substitutes('300', m)['options']
    assert not gateway.substitutes('300', {'substitution_policy': 'never'})['options']
    with pytest.raises(GatewayError):
        gateway.substitutes('missing')


def test_ingredient_and_package_similarity(gateway):
    gateway.catalog.con.execute('DELETE FROM substitutions')
    gateway.catalog.con.execute('UPDATE products SET name_size=2,key_ingredients="OATS" WHERE id="200"')
    options = gateway.substitutes('300')['options']
    assert options[0]['product_id'] == '100'
    assert options[0]['similarity'] > options[1]['similarity']
