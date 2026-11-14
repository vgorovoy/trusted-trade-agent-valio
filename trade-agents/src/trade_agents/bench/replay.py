"""Record / replay of model replies, so a run with a real LLM can be repeated exactly
(and shown in the video) without calling the model again.

The key is a hash of what the model saw: system prompt, tool names and the conversation
(roles, texts, tool calls and results; provider call ids left out). With the frozen clock
and seeded ids of the bench the same scenario asks the same questions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..llm import LLM, LLMResponse, ToolCall, ToolSpec, Usage


class ReplayMiss(RuntimeError):
    pass


def _norm(m: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m["role"]}
    content = m.get("content")
    out["content"] = content if isinstance(content, str) else repr(content)
    if m.get("images"):
        out["images"] = [{"sha256": hashlib.sha256(data).hexdigest(), "mime": mime}
                         for data, mime in m["images"]]
    if m.get("tool_calls"):
        out["tool_calls"] = [[tc.name, tc.args] for tc in m["tool_calls"]]
    if m.get("name"):
        out["name"] = m["name"]
    return out


def conversation_key(messages: list[dict[str, Any]], tools: Sequence[ToolSpec], system: str,
                     json_output: bool) -> str:
    doc = {"system": system, "tools": [t.name for t in tools], "json": json_output,
           "messages": [_norm(m) for m in messages]}
    blob = json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class ReplayLLM:
    """Wraps a model. mode `replay`: answers only from the tape; `record`: always asks the
    model and stores the reply; `auto`: tape first, the model on a miss."""

    def __init__(self, inner: LLM | None, path: Path, mode: str = "auto") -> None:
        if mode not in ("replay", "record", "auto"):
            raise ValueError(f"unknown replay mode {mode!r}")
        if inner is None and mode != "replay":
            raise ValueError("recording needs a model")
        self.inner, self.path, self.mode = inner, path, mode
        self.tape: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
        self.name = inner.name if inner else self.tape.get("__model__", "replay")
        self.vision = bool(inner and inner.vision)
        self.hits = self.misses = 0
        self.live_tokens = 0

    def chat(self, messages, tools=(), *, system="", json_output=False) -> LLMResponse:
        key = conversation_key(messages, tools, system, json_output)
        hit = self.tape.get(key)
        if hit is not None and self.mode != "record":
            self.hits += 1
            return LLMResponse(
                hit["text"], [ToolCall(c["name"], c["args"], c["id"]) for c in hit["tool_calls"]],
                Usage(*hit["usage"]))
        if self.mode == "replay" or self.inner is None:
            raise ReplayMiss(f"no recorded reply for this turn in {self.path.name}")
        self.misses += 1
        resp = self.inner.chat(messages, tools, system=system, json_output=json_output)
        self.live_tokens += resp.usage.input_tokens + resp.usage.output_tokens
        self.tape["__model__"] = self.inner.name
        self.tape[key] = {"text": resp.text,
                          "tool_calls": [{"name": c.name, "args": c.args, "id": c.id}
                                         for c in resp.tool_calls],
                          "usage": [resp.usage.input_tokens, resp.usage.output_tokens]}
        return resp

    def save(self) -> None:
        if self.misses or self.mode == "record":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.tape, indent=1, sort_keys=True,
                                            ensure_ascii=False, default=str))
