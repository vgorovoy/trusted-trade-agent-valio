"""One provider-agnostic LLM adapter with tool calling.

Every agent in this repo talks to models through `LLM.chat(messages, tools)`:

- `Gemini` — google-genai SDK (AI Studio key or Vertex), multimodal.
- `OpenAICompatible` — OpenAI SDK with `base_url`: OpenAI / Codex models, Verda inference,
  NVIDIA Build (Nemotron), any other OpenAI-compatible endpoint.
- `ScriptedLLM` — deterministic replies for tests and offline demos.

Messages are plain dicts in one neutral format, translated per provider:

    {"role": "user", "content": "text", "images": [(bytes, "image/jpeg")]}
    {"role": "assistant", "content": "text", "tool_calls": [ToolCall], "raw": <provider data>}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "<json string>"}

The system prompt is passed separately. Choose a provider with `TTA_LLM_PROVIDER`
(gemini | openai | verda | nvidia | offline) and the model with `TTA_LLM_MODEL`.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import config  # noqa: F401  (loads .env)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema of the arguments object


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:12])


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: Any = None  # provider-specific assistant turn, replayed verbatim when needed

    def as_message(self) -> dict[str, Any]:
        return {"role": "assistant", "content": self.text, "tool_calls": self.tool_calls,
                "raw": self.raw}


class LLM(Protocol):
    name: str
    vision: bool

    def chat(self, messages: list[dict[str, Any]], tools: Sequence[ToolSpec] = (), *,
             system: str = "", json_output: bool = False) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


def _env_temperature(default: float | None) -> float | None:
    v = os.getenv("TTA_LLM_TEMPERATURE")
    if v is None or v == "":
        return default
    return None if v.lower() == "none" else float(v)


# -- Gemini ------------------------------------------------------------------------------

GEMINI_DEFAULT_MODEL = "gemini-3.5-flash"  # "-latest" aliases are not served on API v1
_RETRY_DELAYS = (2.0, 5.0, 10.0)


def _transient(e: Exception) -> bool:
    code = getattr(e, "status_code", None)  # openai SDK
    if not isinstance(code, int):
        code = getattr(e, "code", None)  # google-genai APIError.code (int)
    return isinstance(code, int) and code in (429, 500, 502, 503, 504)


def is_provider_error(e: BaseException) -> bool:
    """The model's service failed (quota, overload, network), not the agent: HTTP 408 /
    429 / 5xx from any SDK, openai connection and timeout errors, google-genai
    ServerError, transport-level timeouts."""
    code = getattr(e, "status_code", None)
    if not isinstance(code, int):
        code = getattr(e, "code", None)
    if isinstance(code, int) and (code in (408, 429) or 500 <= code < 600):
        return True
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
    try:
        import httpx

        if isinstance(e, httpx.TransportError):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        import openai

        if isinstance(e, (openai.APIConnectionError, openai.RateLimitError,
                          openai.InternalServerError)):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        from google.genai import errors as genai_errors

        if isinstance(e, genai_errors.ServerError):
            return True
    except ImportError:  # pragma: no cover
        pass
    return False


def _with_retries(call: Callable[[], Any]) -> Any:
    """Retry transient provider errors (e.g. Gemini 503 "high demand") with backoff."""
    for delay in _RETRY_DELAYS:
        try:
            return call()
        except Exception as e:  # provider SDK errors; anything else is re-raised at once
            if not _transient(e):
                raise
            time.sleep(delay)
    return call()


class Gemini:
    """google-genai SDK. Default model is pinned to GEMINI_DEFAULT_MODEL (gemini-3.5-flash).

    Gemini 3 models attach thought signatures to function calls; the raw model `Content`
    is kept in each assistant message and sent back unchanged, as the API requires.
    Temperature is left at the model default unless TTA_LLM_TEMPERATURE is set
    (Google recommends the default 1.0 for Gemini 3).
    """

    vision = True

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 temperature: float | None = None) -> None:
        from google import genai

        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key and not os.getenv("GOOGLE_GENAI_USE_VERTEXAI"):
            raise LLMError("GEMINI_API_KEY is not set")
        self.client = genai.Client(api_key=key) if key else genai.Client()
        self.model = model or os.getenv("TTA_LLM_MODEL") or GEMINI_DEFAULT_MODEL
        self.temperature = temperature if temperature is not None else _env_temperature(None)
        self.name = f"gemini:{self.model}"

    def _contents(self, messages: list[dict[str, Any]]) -> list[Any]:
        from google.genai import types

        out: list[Any] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                parts = [types.Part.from_text(text=m.get("content") or "")]
                for data, mime in m.get("images") or []:
                    parts.append(types.Part.from_bytes(data=data, mime_type=mime))
                out.append(types.Content(role="user", parts=parts))
            elif role == "assistant":
                if m.get("raw") is not None and isinstance(m["raw"], types.Content):
                    if m["raw"].parts:          # Gemini rejects turns without parts
                        out.append(m["raw"])
                    continue
                parts = []
                if m.get("content"):
                    parts.append(types.Part.from_text(text=m["content"]))
                for tc in m.get("tool_calls") or []:
                    parts.append(types.Part.from_function_call(name=tc.name, args=tc.args))
                if parts:
                    out.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                try:
                    payload = json.loads(m["content"])
                except (TypeError, ValueError):
                    payload = m["content"]
                if not isinstance(payload, dict):
                    payload = {"result": payload}
                part = types.Part.from_function_response(name=m["name"], response=payload)
                # Consecutive tool results go into one user turn.
                if out and getattr(out[-1], "role", None) == "user" and out[-1].parts and \
                        out[-1].parts[0].function_response is not None:
                    out[-1].parts.append(part)
                else:
                    out.append(types.Content(role="user", parts=[part]))
        return out

    def chat(self, messages, tools=(), *, system="", json_output=False) -> LLMResponse:
        from google.genai import types

        cfg: dict[str, Any] = {"system_instruction": system or None}
        if tools:
            cfg["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters_json_schema=t.parameters)
                for t in tools])]
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if json_output:
            cfg["response_mime_type"] = "application/json"
        if self.temperature is not None:
            cfg["temperature"] = self.temperature
        resp = _with_retries(lambda: self.client.models.generate_content(
            model=self.model, contents=self._contents(messages),
            config=types.GenerateContentConfig(**cfg)))
        cand = (resp.candidates or [None])[0]
        content = cand.content if cand is not None else None
        text, calls = [], []
        for part in (content.parts if content and content.parts else []):
            if part.function_call is not None:
                fc = part.function_call
                calls.append(ToolCall(name=fc.name, args=dict(fc.args or {}),
                                      id=fc.id or "call_" + uuid.uuid4().hex[:12]))
            elif part.text and not part.thought:
                text.append(part.text)
        um = resp.usage_metadata
        usage = Usage(getattr(um, "prompt_token_count", 0) or 0,
                      (getattr(um, "candidates_token_count", 0) or 0)
                      + (getattr(um, "thoughts_token_count", 0) or 0)) if um else Usage()
        return LLMResponse("".join(text), calls, usage, raw=content)


# -- OpenAI-compatible (OpenAI, Verda, NVIDIA Build, ...) --------------------------------

PRESETS = {
    "verda-nemotron": (("VERDA_INFERENCE_KEY",), "VERDA_NEMOTRON_BASE_URL",
                       "https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1",
                       "nemotron-3.5-super-vl", True),
    # provider: (api key envs, base url env, default base url, default model, vision by default)
    "openai": (("OPENAI_API_KEY",), "OPENAI_BASE_URL", None, "gpt-5-mini", True),
    # Verda Inference API (EU, open-weight models); a model must be enabled in the project.
    "verda": (("VERDA_API_KEY", "VERDA_INFERENCE_API_KEY"), "VERDA_BASE_URL",
              "https://inference.verda.com/v1", "kimi-k3-1-pro", True),
    # NVIDIA Build; Nemotron 3.5 Super VL is the model the NVIDIA Tech Challenge asks for.
    "nvidia": (("NVIDIA_API_KEY",), "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1",
               "nvidia/nemotron-3.5-super-vl-120b-a12b", True),
}


def _key(envs: Sequence[str]) -> str | None:
    return next((v for e in envs if (v := os.getenv(e))), None)


class OpenAICompatible:
    """Chat Completions with tools. Works with any OpenAI-compatible endpoint."""

    def __init__(self, model: str, *, api_key: str, base_url: str | None = None,
                 temperature: float | None = 0.0, vision: bool = True, label: str = "openai"):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.vision = vision
        self.name = f"{label}:{model}"

    @classmethod
    def from_preset(cls, provider: str, model: str | None = None) -> OpenAICompatible:
        key_envs, url_env, url_default, model_default, vision_default = PRESETS[provider]
        key = _key(key_envs)
        if not key:
            raise LLMError(f"{' or '.join(key_envs)} is not set")
        base_url = os.getenv(url_env) or url_default
        model = model or os.getenv("TTA_LLM_MODEL") or model_default
        if not model:
            raise LLMError("TTA_LLM_MODEL is not set")
        vision = os.getenv("TTA_LLM_VISION", "1" if vision_default else "0") == "1"
        return cls(model, api_key=key, base_url=base_url,
                   temperature=_env_temperature(0.0), vision=vision, label=provider)

    @staticmethod
    def _messages(messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
        import base64

        out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            role = m["role"]
            if role == "user":
                if m.get("images"):
                    parts: list[dict[str, Any]] = [{"type": "text", "text": m.get("content") or ""}]
                    for data, mime in m["images"]:
                        url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                    out.append({"role": "user", "content": parts})
                else:
                    out.append({"role": "user", "content": m.get("content") or ""})
            elif role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": json.dumps(tc.args)}}
                        for tc in m["tool_calls"]]
                out.append(msg)
            elif role == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                            "content": m["content"]})
        return out

    def chat(self, messages, tools=(), *, system="", json_output=False) -> LLMResponse:
        import openai

        kwargs: dict[str, Any] = {"model": self.model,
                                  "messages": self._messages(messages, system)}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools]
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            if "temperature" not in str(e) or "temperature" not in kwargs:
                raise
            kwargs.pop("temperature")  # reasoning models accept only the default
            self.temperature = None
            resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {"_unparsed": tc.function.arguments}
            calls.append(ToolCall(name=tc.function.name, args=args, id=tc.id))
        u = resp.usage
        usage = Usage(u.prompt_tokens or 0, u.completion_tokens or 0) if u else Usage()
        return LLMResponse(msg.content or "", calls, usage)


# -- Scripted (tests, offline) -----------------------------------------------------------

Step = LLMResponse | Callable[[list[dict[str, Any]], Sequence[ToolSpec]], LLMResponse]


class ScriptedLLM:
    """Replays a fixed list of replies. A reply may be a callable that looks at the
    conversation so far (e.g. to pick offer_ids out of the last tool result)."""

    def __init__(self, steps: list[Step], *, vision: bool = True, name: str = "scripted"):
        self.steps = list(steps)
        self.vision = vision
        self.name = name
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages, tools=(), *, system="", json_output=False) -> LLMResponse:
        self.calls.append(list(messages))
        if not self.steps:
            return LLMResponse("(script exhausted)")
        step = self.steps.pop(0)
        return step(messages, tools) if callable(step) else step


def last_tool_result(messages: list[dict[str, Any]], name: str | None = None) -> Any:
    """Helper for scripted steps: parsed content of the latest tool message."""
    for m in reversed(messages):
        if m["role"] == "tool" and (name is None or m["name"] == name):
            return json.loads(m["content"])
    return None


# -- factory ----------------------------------------------------------------------------

def available_providers() -> list[str]:
    """Providers that have credentials in the environment (names only, never values)."""
    out = []
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        out.append("gemini")
    for p, (key_envs, *_rest) in PRESETS.items():
        if _key(key_envs):
            out.append(p)
    return out


def get_llm(provider: str | None = None, model: str | None = None) -> LLM:
    """Provider from the argument, `TTA_LLM_PROVIDER`, or the first one with a key;
    falls back to the deterministic offline agent when no key is configured."""
    provider = (provider or os.getenv("TTA_LLM_PROVIDER") or "").lower()
    if not provider:
        found = available_providers()
        provider = found[0] if found else "offline"
    if provider == "gemini":
        return Gemini(model)
    if provider in PRESETS:
        return OpenAICompatible.from_preset(provider, model)
    if provider == "offline":
        from .buyer_agent.offline import OfflineBuyerLLM

        return OfflineBuyerLLM()
    raise LLMError(f"unknown provider {provider!r}")
