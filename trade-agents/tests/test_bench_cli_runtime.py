"""CLI subset selection and coverage gates without network/provider calls."""
import json
from types import SimpleNamespace

import pytest

from trade_agents.cli import main


@pytest.mark.parametrize('threshold', ['-1', '1.1', 'nan'])
def test_cli_rejects_invalid_coverage(threshold):
    with pytest.raises(SystemExit, match='between 0 and 1'):
        main(['bench', '--provider', 'offline', '--min-coverage', threshold])


def test_cli_tier_and_coverage_gate(monkeypatch, capsys):
    import trade_agents.bench as bench

    report = SimpleNamespace(results=[1, 2], scored=[1], accuracy=1.0,
                             to_dict=lambda: {'accuracy': 1.0, 'scored': 1})
    seen = []
    monkeypatch.setattr(bench, 'load_scenarios',
                        lambda **kwargs: seen.append(kwargs) or [object()])
    monkeypatch.setattr(bench, 'run_bench', lambda *args, **kwargs: report)
    with pytest.raises(SystemExit) as error:
        main(['bench', 'budget', '--tier', 'smoke', '--provider', 'offline',
              '--min-coverage', '1', '--json', '--no-save'])
    assert error.value.code == 1
    assert seen == [{'only': ['budget'], 'tier': 'smoke'}]
    assert json.loads(capsys.readouterr().out)['scored'] == 1


def test_cli_invalid_selector_is_clear():
    with pytest.raises(SystemExit, match='unknown scenario'):
        main(['bench', 'not-a-real-scenario', '--provider', 'offline'])
