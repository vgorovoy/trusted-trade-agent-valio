import json
from copy import deepcopy
from pathlib import Path

import pytest

from trade_agents.cli import main
from trade_agents.drift import detect_drift

FIXTURE = Path(__file__).parent / 'fixtures/standing_order_10_weeks.json'


def history():
    return json.loads(FIXTURE.read_text())


def test_four_anomalies():
    result = detect_drift(history())
    assert {a['kind'] for a in result['anomalies']} == {
        'new_product', 'missing_product', 'unit_price_increase', 'quantity_above_trend'}
    assert result['recommendation'] == 'ask'
    assert all(a['explanation'] and a['recommendation'] == 'ask' for a in result['anomalies'])


def test_trend_without_false_alarm():
    weeks = history()[:-1]
    assert detect_drift(weeks)['recommendation'] == 'renew'


def test_cli(capsys):
    main(['drift', str(FIXTURE)])
    assert json.loads(capsys.readouterr().out)['recommendation'] == 'ask'


@pytest.mark.parametrize('mutation', ['short', 'duplicate', 'unit', 'nan', 'date'])
def test_invalid_history(mutation):
    weeks = deepcopy(history())
    if mutation == 'short':
        weeks = weeks[:2]
    elif mutation == 'duplicate':
        weeks[0]['lines'].append(weeks[0]['lines'][0])
    elif mutation == 'unit':
        weeks[0]['lines'][0]['unit'] = 'KG'
    elif mutation == 'nan':
        weeks[0]['lines'][0]['quantity'] = float('nan')
    else:
        weeks[1]['week'] = weeks[0]['week']
    with pytest.raises(ValueError):
        detect_drift(weeks)
