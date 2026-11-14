"""A saved response to one menu image must not answer a different image."""
import pytest

from trade_agents.bench.replay import ReplayLLM, ReplayMiss
from trade_agents.llm import LLMResponse, ScriptedLLM


def test_recorded_image_requires_same_bytes_and_mime(tmp_path):
    tape = tmp_path / "vision.json"
    message = [{"role": "user", "content": "Read menu", "images": [(b"recipe-A", "image/png")]}]
    recorded = ReplayLLM(ScriptedLLM([LLMResponse(text="A")]), tape, "record")
    assert recorded.chat(message).text == "A"
    recorded.save()
    replay = ReplayLLM(None, tape, "replay")
    assert replay.chat(message).text == "A"
    for data, mime in [(b"recipe-B", "image/png"), (b"recipe-A", "image/jpeg")]:
        with pytest.raises(ReplayMiss):
            replay.chat([{"role": "user", "content": "Read menu", "images": [(data, mime)]}])
    assert replay.hits == 1
