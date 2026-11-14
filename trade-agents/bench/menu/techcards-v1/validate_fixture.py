"""Standalone stdlib deterministic arithmetic oracle; no model/provider calls."""
import copy
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
def D(value):
    return Decimal(str(value))


def read(path):
    return json.loads((ROOT / path).read_text())


def calculate(menu, cards, inventory):
    recipes = {dish['id']: dish for dish in cards['dishes']}
    gross = defaultdict(Decimal)
    units = {}
    for service in menu['services']:
        for item in recipes[service['dish_id']]['ingredients']:
            if item['amount_per_portion'] is None:
                raise ValueError('Missing per-portion quantity: ' + item['ingredient'])
            amount = D(item['amount_per_portion'])
            if item['basis'] == 'edible_net':
                ratio = item.get('usable_yield')
                if ratio is None or not 0 < D(ratio) <= 1:
                    raise ValueError('Missing/invalid usable yield: ' + item['ingredient'])
                amount /= D(ratio)
            else:
                assert item['basis'] == 'raw_purchase'
            key = (service['delivery_date'], item['ingredient'])
            gross[key] += amount * D(service['portions'])
            assert units.setdefault(item['ingredient'], item['unit']) == item['unit']
    available = {}
    for item in inventory['stock']:
        assert units[item['ingredient']] == item['unit']
        usable = D(item['total']) - D(item['reserved']) - D(item['expired'])
        assert usable >= 0
        available[item['ingredient']] = usable
    receipts = copy.deepcopy(inventory['receipts'])
    used_receipts = set()
    ledger = []
    for when in sorted({key[0] for key in gross}):
        for index, receipt in enumerate(receipts):
            if index not in used_receipts and receipt['available_date'] <= when:
                assert units[receipt['ingredient']] == receipt['unit']
                available[receipt['ingredient']] += D(receipt['amount'])
                used_receipts.add(index)
        for _, ingredient in sorted(key for key in gross if key[0] == when):
            demand = gross[(when, ingredient)]
            opening = available[ingredient]
            consumed = min(demand, opening)
            available[ingredient] -= consumed
            ledger.append(dict(ingredient=ingredient, unit=units[ingredient], date=when,
                               gross=float(demand), available_before=float(opening),
                               consumed=float(consumed), net=float(demand-consumed)))
    return ledger, used_receipts


def build_case(menu, cards, inventory, ledger):
    # Only inputs appear in the model prompt; numeric answers remain in the oracle.
    text = ('Prepare an editable net ingredient procurement draft for Valio Aimo only. '
            'These are trusted SYNTHETIC technological recipe cards, not official recipes. '
            'Use each service dish count provided; guests=1 is neutral, never a uniform multiplier. '
            'Apply edible_net / usable_yield once; raw_purchase amounts need no correction. '
            'Consume only usable opening stock and eligible receipts once chronologically. '
            'Keep dates separate, consolidate shared ingredients, no case rounding, forecasts, '
            'safety margins, products, prices, supplier promises or orders. Chef review required. '
            'INPUTS: ' + json.dumps(dict(menu=menu,recipe_cards=cards,inventory=inventory)))
    aliases = {'pasta':['dry pasta','macaroni','makaroni'], 'minced beef':['ground beef','naudan jauheliha'],
               'onion':['onions','sipuli'], 'carrot':['carrots','porkkana'],
               'potato':['potatoes','peruna'], 'oat drink':['oat milk','kaurajuoma'],
               'rapeseed oil':['rypsiöljy','rypsioljy'], 'oats':['oat flakes','kaurahiutaleet'],
               'berries':['mixed berries','marjat']}
    needs = [dict(ingredient=row['ingredient'],unit=row['unit'],amount=row['net'],date=row['date'],
                  aliases=aliases[row['ingredient']]) for row in ledger if row['net'] > 0]
    assert 1 <= len(needs) <= 20
    return dict(suite_version='menu-techcards-v1',id='valio-three-techcards-two-services-v1',
                today=menu['today'],buyer='restaurant',guests=1,scope='synthetic_trusted_recipe_yield_inventory_arithmetic',
                text=text,expect=dict(needs=needs,excluded_allergens=[],exclude_may_contain=False,exact_ingredients=True,
                                    quality_requires_exact_lines=True))


def validate_fixture():
    menu,cards,inventory = [read('inputs/'+name+'.json') for name in ('menu','recipe-cards','inventory')]
    ledger,used = calculate(menu,cards,inventory)
    actual = {(row['date'],row['ingredient']): D(row['net']) for row in ledger}
    # Independent hand-worked answers, not generated from recipe coefficients.
    expected = {
        '2026-11-20': {'pasta':5,'minced beef':4.4,'onion':1,'carrot':5.4,'potato':4,
                       'oat drink':6.6,'rapeseed oil':.1,'oats':2,'berries':1.5},
        '2026-11-21': {'pasta':4,'minced beef':4.8,'onion':1.5,'carrot':9.2,'potato':7.5,
                       'oat drink':7.8,'rapeseed oil':.35,'oats':1.8,'berries':.8}}
    assert actual == {(when,ingredient):D(amount) for when,rows in expected.items()
                      for ingredient,amount in rows.items()}
    assert used == set(range(8)), 'Monday receipt must remain unused'
    missing=read('inputs/recipe-cards-missing.json')
    try:
        calculate(menu,missing,inventory)
    except ValueError as exc:
        assert 'Missing per-portion quantity: oat drink' in str(exc)
    else:
        raise AssertionError('Missing data cannot produce numeric net requirements')
    candidate=build_case(menu,cards,inventory,ledger)
    assert read('01-three-techcards.json') == candidate
    assert read('inputs/oracle-ledger.json') == ledger
    print('PASS: 3 cards, 6 dish services, 2 dates, 18 exact net lines, missing-data rejection; no LLM calls')


if __name__ == '__main__':
    validate_fixture()
