"""No-network tests of evidence allocation, sealing and comparison semantics."""
from __future__ import annotations

import json

import pytest

from scripts.compare_models import DEFAULT_CASES, compare_models, comparison_summary
from trade_agents.bench import load_scenarios
from trade_agents.bench.records import RunArchive, source_identity, verify_run
from trade_agents.bench.runner import run_bench
from trade_agents.buyer_agent.offline import OfflineBuyerLLM


def test_allocate_unique_ids_and_refuse_existing_stage_or_archive(tmp_path):
    first, second = RunArchive(tmp_path), RunArchive(tmp_path)
    assert first.run_id != second.run_id
    assert first.staging_path.exists() and not first.final_path.exists()
    with pytest.raises(FileExistsError):
        RunArchive(tmp_path, first.run_id)
    first.write_json('report.json', {'passed': False})
    with pytest.raises(FileExistsError):
        first.write_json('report.json', {'passed': True})
    path = first.finalize()
    assert verify_run(path)['status'] == 'completed'
    assert not first.staging_path.exists()
    assert (path.stat().st_mode & 0o222) == 0
    assert ((path / 'report.json').stat().st_mode & 0o222) == 0
    with pytest.raises(FileExistsError):
        RunArchive(tmp_path, first.run_id)
    with pytest.raises(RuntimeError):
        first.append_event({'too': 'late'})


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'x/y', '..', ''])
def test_run_id_and_artifact_path_cannot_escape(tmp_path, name):
    if name:
        with pytest.raises(ValueError):
            RunArchive(tmp_path, name)
    archive = RunArchive(tmp_path)
    with pytest.raises(ValueError):
        archive.write_json('../escape.json', {})
    with pytest.raises(ValueError):
        archive.write_json('manifest.json', {})


@pytest.mark.parametrize('change', ['modified', 'missing', 'unexpected'])
def test_manifest_detects_artifact_tampering(tmp_path, change):
    archive = RunArchive(tmp_path)
    archive.write_json('report.json', {'passed': 0})
    path = archive.finalize()
    path.chmod(0o755)
    report = path / 'report.json'
    report.chmod(0o644)
    if change == 'modified':
        report.write_text('{}')
    elif change == 'missing':
        report.unlink()
    else:
        (path / 'unexpected.txt').write_text('extra')
    with pytest.raises(ValueError, match='modified, missing or unexpected'):
        verify_run(path)


def test_partial_and_symlink_archives_never_verify(tmp_path):
    archive = RunArchive(tmp_path)
    archive.write_json('report.json', {})
    with pytest.raises(ValueError, match='completion manifest'):
        verify_run(archive.staging_path)
    (archive.staging_path / 'outside').symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError, match='symlinks'):
        archive.finalize()
    assert not archive.final_path.exists()


def test_source_fingerprint_does_not_read_env_or_other_files(tmp_path):
    (tmp_path / 'src').mkdir()
    code = tmp_path / 'src' / 'example.py'
    code.write_text('answer = 42\n')
    (tmp_path / '.env').write_text('SECRET=do-not-collect-me')
    (tmp_path / 'private.txt').write_text('do-not-collect-me')
    before = source_identity(tmp_path)
    assert list(before['code_files']) == ['src/example.py']
    assert 'do-not-collect-me' not in json.dumps(before)
    code.write_text('answer = 43\n')
    assert source_identity(tmp_path)['code_sha256'] != before['code_sha256']


def _fake_model(provider, model):
    class FakeProvider:
        name = f'{provider}:{model}'
        vision = False
        temperature = 0

        def __init__(self):
            self.inner = OfflineBuyerLLM()

        def chat(self, *args, **kwargs):
            return self.inner.chat(*args, **kwargs)
    return FakeProvider()


def test_comparison_archives_identical_inputs_failures_traces_and_real_tapes(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'do-not-collect-me')
    scenarios = load_scenarios(only=['basic-milk', *DEFAULT_CASES])
    path = compare_models(['fake:alpha', 'fake:beta'], scenarios, output=tmp_path,
                          llm_builder=_fake_model, temperature=0)
    assert verify_run(path)['status'] == 'completed'
    summary = json.loads((path / 'summary.json').read_text())
    assert len(summary['models']) == 2
    pinned = []
    for model in summary['models']:
        run = path / model['archive']
        assert verify_run(run)['status'] == 'completed'
        pinned.append(json.loads((run / 'inputs.json').read_text()))
        assert model['coverage']['fraction'] == 1
        assert model['scored'] == 5 and model['passed'] == 1
        assert {case['outcome'] for case in model['cases']} == {'passed', 'failed'}
        events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
        assert any(event['event'] == 'tool_completed' for event in events)
        assert len(list((run / 'replays').rglob('*.json'))) == 5
        assert model['usage']['live_model_calls'] > 0
        assert model['usage']['replay_hits'] == 0
        # A fake test adapter's unknown price must not be invented either.
        assert model['usage']['cost_usd'] is None
    assert pinned[0]['suite'] == pinned[1]['suite']
    assert pinned[0]['source'] == pinned[1]['source']
    assert pinned[0]['budget'] == pinned[1]['budget']
    assert not any('do-not-collect-me' in p.read_text() for p in path.rglob('*') if p.is_file())


def test_model_initialization_error_is_archived_without_exception_secrets(tmp_path):
    def broken(provider, model):
        raise RuntimeError('do-not-collect-me')
    path = compare_models(['fake:alpha', 'fake:beta'], load_scenarios(only=['basic-milk']),
                          output=tmp_path, llm_builder=broken)
    assert verify_run(path)['status'] == 'error'
    summary = json.loads((path / 'summary.json').read_text())
    assert all(m['run_status'] == 'error' for m in summary['models'])
    assert all(m['cases'][0]['outcome'] == 'run_error' for m in summary['models'])
    assert not any('do-not-collect-me' in p.read_text() for p in path.rglob('*') if p.is_file())


def test_comparison_summary_preserves_skips_errors_and_denominators():
    report = run_bench(load_scenarios(only=['basic-milk']), OfflineBuyerLLM, save=False).to_dict()
    report['results'][0].update(skipped='no recording', passed=False, status='error')
    report.update(passed=0, scored=0, skipped={'replay': 1},
                  coverage={'selected': 1, 'scored': 0, 'fraction': 0})
    runs = [{'requested_model': 'fake:a', 'archive': 'model-01', 'status': 'completed', 'report': report}]
    summary = comparison_summary(runs, ['basic-milk'])['models'][0]
    assert summary['cases'][0]['outcome'] == 'skipped'
    assert summary['coverage']['fraction'] == 0 and summary['scored'] == 0
    report['results'][0]['skipped'] = None
    assert comparison_summary(runs, ['basic-milk'])['models'][0]['cases'][0]['outcome'] == 'error'


@pytest.mark.parametrize('exception', [TimeoutError, ValueError])
def test_chat_time_errors_are_sanitized_in_stdout_reports_events_and_summary(tmp_path, monkeypatch,
                                                                           capsys, exception):
    from scripts import compare_models as comparison_module
    from trade_agents.llm import LLMResponse, ToolCall

    marker = 'synthetic-secret-in-provider-url'

    def leaking_provider(provider, model):
        class ChatFailure:
            name = f'{provider}:{model}'
            vision = False

            def __init__(self):
                self.calls = 0

            def chat(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(tool_calls=[ToolCall('search_products', {'query': 'maito'})])
                raise exception(marker)
        return ChatFailure()

    monkeypatch.setattr(comparison_module, 'get_llm', leaking_provider)
    comparison_module.main(['--model', 'fake:one', '--model', 'fake:two', '--only', 'basic-milk',
                            '--comparison-id', 'sanitized-chat', '--output', str(tmp_path)])
    assert marker not in capsys.readouterr().out
    path = tmp_path / 'sanitized-chat'
    assert not any(marker in p.read_text() for p in path.rglob('*') if p.is_file())
    summary = json.loads((path / 'summary.json').read_text())
    expected_outcome = 'skipped' if exception is TimeoutError else 'error'
    for model in summary['models']:
        assert model['cases'][0]['outcome'] == expected_outcome
        assert model['passed'] == 0
        run = path / model['archive']
        result = json.loads((run / 'report.json').read_text())['results'][0]
        assert result['error_category'] == ('provider' if exception is TimeoutError else 'agent')
        if exception is TimeoutError:
            assert result['skip_kind'] == 'provider'
            assert model['coverage']['fraction'] == 0
        assert verify_run(run)['status'] == 'completed'
    assert verify_run(path)['status'] == 'completed'
