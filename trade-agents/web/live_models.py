"""Fixed, bounded provider adapters for explicitly enabled interactive benchmark calls."""
from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any

from trade_agents.llm import Gemini, OpenAICompatible

MODELS = {
    'verda-nemotron': ('Verda · Nemotron 3.5 Super VL', 'nemotron-3.5-super-vl', ('VERDA_INFERENCE_KEY',),
                       'https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1'),
    'gemini': ('Gemini', 'gemini-3.5-flash', ('GEMINI_API_KEY', 'GOOGLE_API_KEY'), None),
    'nvidia': ('NVIDIA', 'nvidia/nemotron-3.5-super-vl-120b-a12b', ('NVIDIA_API_KEY',),
               'https://integrate.api.nvidia.com/v1'),
    'verda': ('Verda', 'kimi-k3-1-pro', ('VERDA_API_KEY', 'VERDA_INFERENCE_API_KEY'),
              'https://inference.verda.com/v1'),
    'verda-deepseek': ('Verda · DeepSeek', 'deepseek-4-1-flash-1017',
                       ('VERDA_API_KEY', 'VERDA_INFERENCE_API_KEY'), 'https://inference.verda.com/v1'),
}
LIMITS = {'max_scenarios': 1, 'max_steps': 8, 'request_timeout_seconds': 45,
          'sdk_max_attempts': 1, 'application_max_attempts': 1, 'max_output_tokens': 4096,
          'max_trajectory_tokens': 40000, 'worker_timeout_seconds': 240,
          'max_live_runs_per_container': 100}


def credential(provider: str) -> str | None:
    return next((os.getenv(key) for key in MODELS[provider][2] if os.getenv(key)), None)


def capabilities() -> dict[str, Any]:
    enabled = os.getenv('TTA_LIVE_BENCH') == '1'
    providers = [{'id': key, 'label': value[0], 'configured': bool(credential(key)),
                  'models': [{'id': value[1], 'label': value[1]}]} for key, value in MODELS.items()]
    return {'providers': providers, 'live_available': enabled and any(p['configured'] for p in providers),
            'enabled': enabled, 'max_live_scenarios': 1, 'limits': LIMITS,
            'data_scope': 'Real provider calls; synthetic supplier/catalogue/orders; not live Valio data',
            'reasoning_scope': 'Public assistant text and tool events only; hidden reasoning is not exposed'}


def validate_selection(provider: str | None, model: str | None) -> None:
    if provider not in MODELS or model != MODELS[provider][1]:
        raise ValueError('Select a supported provider and its fixed model')
    if os.getenv('TTA_LIVE_BENCH') != '1' or not credential(provider):
        raise ValueError('Live benchmark provider is not enabled or configured')


def public_text(text: str) -> str:
    """Never expose separately marked model reasoning or provider raw payloads."""
    text = re.sub(r'<(think|analysis)>.*?</\1>', '', text, flags=re.I | re.S)
    text = re.split(r'<(?:think|analysis)>', text, maxsplit=1, flags=re.I)[0]
    return text[:12000]


def bounded_model(provider: str, model: str):
    """Ignore endpoint environment overrides; SDK/application requests get no retries."""
    validate_selection(provider, model)
    key = credential(provider)
    if provider == 'gemini':
        from google import genai
        from google.genai import types

        llm = object.__new__(Gemini)
        llm.model, llm.name, llm.temperature = model, f'gemini:{model}', None
        client = genai.Client(api_key=key, vertexai=False, http_options=types.HttpOptions(
            base_url='https://generativelanguage.googleapis.com', timeout=45000,
            retry_options=types.HttpRetryOptions(attempts=1)))

        def generate(**kwargs):
            kwargs['config'] = kwargs['config'].model_copy(update={'max_output_tokens': 4096})
            try:
                return client.models.generate_content(**kwargs)
            except Exception:
                # Gemini.chat's application retry checks numeric error codes. A sanitized
                # connection error prevents retries and still marks the result inconclusive.
                raise ConnectionError('Live provider request failed; diagnostics omitted') from None

        llm.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate), close=client.close)
        return llm
    llm = OpenAICompatible(model, api_key=key, base_url=MODELS[provider][3],
                           temperature=None, label=provider)
    client = llm.client.with_options(timeout=45, max_retries=0)

    def create(**kwargs):
        kwargs['max_tokens'] = 4096
        try:
            return client.chat.completions.create(**kwargs)
        except Exception:
            raise ConnectionError('Live provider request failed; diagnostics omitted') from None

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                 close=client.close)
    return llm


class PublicModel:
    """Count actual chat calls and publish only ordinary response text, never raw fields."""

    def __init__(self, inner, emit):
        self.inner, self.emit = inner, emit
        self.name, self.vision = inner.name, inner.vision
        self.calls = self.tokens = 0

    def chat(self, *args, **kwargs):
        if self.calls >= 8 or self.tokens >= 40000:
            raise RuntimeError('Live model budget exhausted')
        self.calls += 1
        self.emit('model_call', {'call': self.calls, 'model': self.name})
        response = self.inner.chat(*args, **kwargs)
        self.tokens += response.usage.total
        text = public_text(response.text)
        if text:
            self.emit('model_message', {'text': text, 'call': self.calls,
                                       'scope': 'public assistant text; not hidden reasoning'})
        self.emit('model_usage', {'call': self.calls, 'input_tokens': response.usage.input_tokens,
                                 'output_tokens': response.usage.output_tokens,
                                 'cost_usd': None})
        return response
