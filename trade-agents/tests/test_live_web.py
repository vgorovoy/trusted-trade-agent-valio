"""The live page executes the real isolated bench and exposes its evidence."""

import time

from fastapi.testclient import TestClient

from web import server


def _finish(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snapshot = client.get(f"/ui/live/runs/{run_id}").json()
        if snapshot["done"]:
            return snapshot
        time.sleep(0.02)
    raise AssertionError("live bench did not finish")


def test_live_pages_and_scenarios():
    client = TestClient(server.app)
    assert client.get("/live").status_code == 200
    assert client.get("/live.js").status_code == 200
    assert client.get("/live.css").status_code == 200
    assert client.get("/how-it-works").status_code == 200
    scenarios = client.get("/ui/live/scenarios").json()["scenarios"]
    assert len(scenarios) == 42
    assert sum(sc["recorded"] for sc in scenarios) == 3


def test_exchange_overview_explains_missing_catalogue(monkeypatch, tmp_path):
    monkeypatch.setenv("TTA_DATA_DIR", str(tmp_path))
    client = TestClient(server.app)
    response = client.get("/ui/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["setup_required"]
    assert data["orders"] == []
    assert data["audit"]["chain_valid"] is None
    assert client.get("/live").status_code == 200


def test_live_offline_run_streams_real_steps_and_checks():
    client = TestClient(server.app)
    started = client.post("/ui/live/runs", json={
        "scenario_ids": ["basic-milk"], "mode": "offline",
    })
    assert started.status_code == 202
    result = _finish(client, started.json()["run_id"])
    assert result["error"] is None
    assert result["report"]["scored"] == 1
    assert result["report"]["usage"]["live_model_calls"] == 0
    assert any(ev["kind"] == "tool" for ev in result["events"])
    finished = next(ev for ev in result["events"] if ev["kind"] == "scenario_finished")
    assert finished["data"]["checks"]
    assert finished["data"]["passed"]
    tail = client.get(f"/ui/live/runs/{started.json()['run_id']}?after={result['next']}")
    assert tail.json()["events"] == []


def test_live_recorded_run_and_rejections():
    client = TestClient(server.app)
    bad = client.post("/ui/live/runs", json={
        "scenario_ids": ["not-a-scenario"], "mode": "offline",
    })
    assert bad.status_code == 400
    unavailable = client.post("/ui/live/runs", json={
        "scenario_ids": ["cutoff-before"], "mode": "recorded",
    })
    assert unavailable.status_code == 400
    started = client.post("/ui/live/runs", json={
        "scenario_ids": ["basic-milk"], "mode": "recorded",
    })
    assert started.status_code == 202
    result = _finish(client, started.json()["run_id"])
    assert result["error"] is None
    assert result["report"]["scored"] == 1
    assert result["report"]["usage"]["live_model_calls"] == 0
    assert result["report"]["usage"]["replay_hits"] > 0
    assert any(ev["kind"] == "tool" for ev in result["events"])
