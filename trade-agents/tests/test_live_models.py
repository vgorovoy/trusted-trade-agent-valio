"""Live provider controls use fake clients; never make paid calls during tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from trade_agents.bench.scenario import load_scenarios
from trade_agents.buyer_agent.offline import OfflineBuyerLLM
from trade_agents.llm import LLMResponse, ScriptedLLM, Usage
from web import live, live_models, live_worker, server


def enable(monkeypatch):
    monkeypatch.setenv('TTA_LIVE_BENCH', '1')
    monkeypatch.setenv('GEMINI_API_KEY', 'unit-test-only-key')


def test_capabilities_require_optin_and_do_not_expose_credentials(monkeypatch):
    monkeypatch.delenv('TTA_LIVE_BENCH', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY', 'secret-unit-key')
    data = TestClient(server.app).get('/ui/live/capabilities').json()
    assert not data['live_available']
    assert 'secret-unit-key' not in str(data)
    enable(monkeypatch)
    assert live_models.capabilities()['live_available']
    with pytest.raises(ValueError):
        live_models.validate_selection('gemini', 'arbitrary-model')
    with pytest.raises(ValueError):
        live_models.validate_selection('http://attacker', 'model')


def test_live_input_and_container_budget_enforced_before_spawn(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(live.threading.Thread, 'start', lambda self: None)
    manager = live.LiveRuns()
    sc = load_scenarios(only=['basic-milk'])
    with pytest.raises(HTTPException) as error:
        manager.start(sc * 2, 'live', 'gemini', 'gemini-3.5-flash')
    assert error.value.status_code == 400 and manager.live_attempts == 0
    run = manager.start(sc, 'live', 'gemini', 'gemini-3.5-flash')
    assert run.provider == 'gemini' and manager.live_attempts == 1
    with pytest.raises(HTTPException) as error:
        manager.start(sc, 'live', 'gemini', 'gemini-3.5-flash')
    assert error.value.status_code == 409
    manager.active = None
    manager.live_attempts = 20
    with pytest.raises(HTTPException) as error:
        manager.start(sc, 'live', 'gemini', 'gemini-3.5-flash')
    assert error.value.status_code == 429


def test_gemini_request_limit_is_one_attempt_and_output_bounded(monkeypatch):
    from google import genai

    enable(monkeypatch)
    seen = []

    def generate(**kwargs):
        seen.append(kwargs)
        raise RuntimeError('private provider diagnostic with key')

    def client(**kwargs):
        assert kwargs['vertexai'] is False
        assert kwargs['http_options'].timeout == 45000
        assert kwargs['http_options'].retry_options.attempts == 1
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate), close=lambda: None)

    monkeypatch.setattr(genai, 'Client', client)
    model = live_models.bounded_model('gemini', 'gemini-3.5-flash')
    with pytest.raises(ConnectionError) as error:
        model.chat([{'role': 'user', 'content': 'test'}])
    assert len(seen) == 1 and seen[0]['config'].max_output_tokens == 4096
    assert 'private' not in str(error.value)


def test_public_model_exposes_text_not_reasoning_and_stops_before_ninth_chat():
    events = []
    model = ScriptedLLM([LLMResponse(text='<think>private chain</think>Public response',
                                    usage=Usage(10, 20)) for _ in range(8)])
    model.name = 'scripted:bounded-test'
    wrapper = live_models.PublicModel(model, lambda kind, data: events.append((kind, data)))
    for _ in range(8):
        wrapper.chat([])
    with pytest.raises(RuntimeError, match='budget'):
        wrapper.chat([])
    messages = [data['text'] for kind, data in events if kind == 'model_message']
    assert messages == ['Public response'] * 8
    assert wrapper.calls == 8 and wrapper.tokens == 240
    assert live_models.public_text('<analysis>unfinished private') == ''


def test_live_worker_emits_actual_tools_checks_and_no_implicit_harness_save(monkeypatch):
    enable(monkeypatch)
    model = OfflineBuyerLLM()
    model.name = 'scripted:live-worker-control'
    model.client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(live_models, 'bounded_model', lambda provider, name: model)
    events = []
    channel = SimpleNamespace(send=events.append)
    live_worker.run_live_worker(load_scenarios(only=['basic-milk']), channel,
                                {'provider': 'gemini', 'model': 'gemini-3.5-flash'})
    assert any(kind == 'tool' for kind, _ in events)
    result = next(data for kind, data in events if kind == 'scenario_finished')
    assert result['passed'] and result['checks']
    report = next(data for kind, data in events if kind == 'report')
    assert report['execution']['actual_model_calls'] > 0
    assert report['usage']['replay_hits'] == 0
    assert 'no causal save' in report['execution']['harness_attribution']
    assert report['execution']['data_scope'].startswith('Synthetic')


def test_sse_replays_real_stored_events_and_finishes(monkeypatch):
    manager = live.LiveRuns()
    run = live.LiveRun('test-sse', 'offline', ['basic-milk'], '2026-11-14')
    manager.runs[run.id] = run
    manager.emit(run, 'model_message', {'text': 'real stored event'})
    run.done = True
    monkeypatch.setattr(live, 'manager', manager)
    response = TestClient(server.app).get('/ui/live/runs/test-sse/events')
    assert response.status_code == 200
    assert 'text/event-stream' in response.headers['content-type']
    assert 'event: model_message' in response.text and 'real stored event' in response.text


def test_nvidia_ignores_custom_endpoint_and_applies_sdk_and_output_caps(monkeypatch):
    import openai

    monkeypatch.setenv('TTA_LIVE_BENCH', '1')
    monkeypatch.setenv('NVIDIA_API_KEY', 'test-key')
    monkeypatch.setenv('NVIDIA_BASE_URL', 'https://untrusted.invalid')
    requests = []
    clients = []
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='Public answer', tool_calls=[]))], usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: requests.append(kwargs) or response)), close=lambda: None)

    def with_options(**kwargs):
        assert kwargs == {'timeout': 45, 'max_retries': 0}
        return client

    client.with_options = with_options

    def constructor(**kwargs):
        clients.append(kwargs)
        return client

    monkeypatch.setattr(openai, 'OpenAI', constructor)
    model = live_models.bounded_model('nvidia', 'nvidia/nemotron-3.5-super-vl-120b-a12b')
    result = model.chat([{'role': 'user', 'content': 'test'}])
    assert result.text == 'Public answer'
    assert clients[0]['base_url'] == 'https://integrate.api.nvidia.com/v1'
    assert requests[0]['max_tokens'] == 4096
    assert 'temperature' not in requests[0]


def test_verda_nemotron_is_a_distinct_fixed_hosting_option(monkeypatch):
    monkeypatch.setenv('TTA_LIVE_BENCH', '1')
    monkeypatch.setenv('VERDA_INFERENCE_KEY', 'unit-test-private-key')
    live_models.validate_selection('verda-nemotron', 'nemotron-3.5-super-vl')
    provider = next(p for p in live_models.capabilities()['providers']
                    if p['id'] == 'verda-nemotron')
    assert provider['configured']
    assert 'unit-test-private-key' not in str(provider)
    assert live_models.MODELS['verda-nemotron'][3] == (
        'https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1')
    with pytest.raises(ValueError):
        live_models.validate_selection('verda-nemotron', 'kimi-k3-1-pro')
