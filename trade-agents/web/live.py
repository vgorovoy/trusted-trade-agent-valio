"""Isolated benchmark UI: offline, recorded, or explicitly enabled bounded live models.

Supplier/catalogue/orders remain synthetic in interactive live-model mode. Credentials
stay server-side; the single spawned worker isolates clocks and temporary state.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from trade_agents.bench.scenario import BENCH_DIR, Scenario, load_scenarios
from web.live_models import LIMITS, capabilities, validate_selection
from web.live_snapshot import configured as official_configured
from web.live_snapshot import dataset_capabilities, diagnostic_scenarios
from web.live_worker import run_worker

router = APIRouter(prefix="/ui/live", tags=["live demo"])
RECORDED_DIR = BENCH_DIR / "replays" / "nvidia-nvidia-nemotron-3.5-super-vl-120b-a12b"


class StartIn(BaseModel):
    scenario_ids: list[str] = Field(min_length=1, max_length=50)
    mode: str = Field(default="offline", pattern="^(offline|recorded|live)$")

    provider: str | None = None
    model: str | None = None
    dataset: str = Field(default="synthetic", pattern="^(synthetic|official_snapshot)$")


@dataclass
class LiveRun:
    id: str
    mode: str
    scenario_ids: list[str]
    created_at: str
    provider: str | None = None
    model: str | None = None
    dataset: str = "synthetic"
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    report: dict[str, Any] | None = None
    error: str | None = None


class LiveRuns:
    def __init__(self, *, worker_timeout: float = 120, worker=run_worker) -> None:
        self.worker_timeout = worker_timeout
        self.worker = worker
        self.lock = threading.RLock()
        self.runs: dict[str, LiveRun] = {}
        self.active: str | None = None
        self.live_attempts = 0

    def emit(self, run: LiveRun, kind: str, data: dict[str, Any]) -> None:
        with self.lock:
            run.events.append({"seq": len(run.events) + 1, "kind": kind,
                               "at": datetime.now(UTC).isoformat(), "data": data})

    def start(self, scenarios: list[Scenario], mode: str,
              provider: str | None = None, model: str | None = None,
              dataset: str = "synthetic") -> LiveRun:
        with self.lock:
            if self.active is not None:
                raise HTTPException(409, "a live benchmark is already running")
            if dataset not in {"synthetic", "official_snapshot"}:
                raise HTTPException(400, "Unknown benchmark dataset")
            if dataset == "official_snapshot":
                if mode != "live" or not official_configured():
                    raise HTTPException(400, "Official snapshot needs live mode and server configuration")
                permitted = {scenario.id for scenario in diagnostic_scenarios()}
                if any(scenario.id not in permitted for scenario in scenarios):
                    raise HTTPException(400, "Select an official mock diagnostic")
            if mode == "live":
                try:
                    validate_selection(provider, model)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
                if len(scenarios) != 1 or any(sc.world.name == "live" for sc in scenarios):
                    raise HTTPException(400, "Live model mode permits one synthetic scenario only")
                if self.live_attempts >= LIMITS["max_live_runs_per_container"]:
                    raise HTTPException(429, "Live benchmark budget exhausted for this server instance")
                self.live_attempts += 1
            run = LiveRun(uuid.uuid4().hex, mode, [s.id for s in scenarios],
                          datetime.now(UTC).isoformat(), provider=provider, model=model, dataset=dataset)
            self.runs[run.id] = run
            self.active = run.id
            # Keep recent completed runs for a browser refresh, without unbounded memory.
            for old_id in list(self.runs)[:-5]:
                if old_id != self.active:
                    del self.runs[old_id]
        try:
            threading.Thread(target=self._work, args=(run, scenarios), daemon=True).start()
        except Exception as exc:
            with self.lock:
                run.error = f"{type(exc).__name__}: {exc}"[:400]
                self.emit(run, "run_failed", {"error": run.error})
                run.done = True
                self.active = None
        return run

    def _work(self, run: LiveRun, scenarios: list[Scenario]) -> None:
        # spawn (never fork): no SQLite connections, cached gateways or mutated Python
        # globals from the ASGI process are inherited by the benchmark worker.
        receive = send = process = None
        try:
            context = multiprocessing.get_context("spawn")
            receive, send = context.Pipe(duplex=False)
            args = (scenarios, run.mode, send)
            if run.mode == "live":
                args += ({"provider": run.provider, "model": run.model, "dataset": run.dataset},)
            process = context.Process(target=self.worker, args=args, daemon=True)
            self.emit(run, "run_started", {"mode": run.mode, "total": len(scenarios),
                                           "provider": run.provider, "model": run.model,
                                           "dataset": run.dataset,
                                           "limits": LIMITS if run.mode == "live" else None})
            process.start()
            send.close()  # only the child keeps the sending end; crash becomes EOF
            deadline = time.monotonic() + (LIMITS["worker_timeout_seconds"]
                                          if run.mode == "live" else self.worker_timeout)
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("benchmark worker exceeded its time limit")
                if not receive.poll(0.1):
                    if not process.is_alive():
                        raise RuntimeError("benchmark worker exited without a report")
                    continue
                try:
                    kind, data = receive.recv()
                except EOFError:
                    raise RuntimeError("benchmark worker closed without a report") from None
                if kind == "error":
                    raise RuntimeError(data)
                if kind == "report":
                    with self.lock:
                        run.report = data
                    self.emit(run, "run_finished", {"passed": data["passed"],
                                                    "scored": data["scored"],
                                                    "coverage": data["coverage"]})
                    break
                self.emit(run, kind, data)
        except Exception as exc:
            with self.lock:
                run.error = f"{type(exc).__name__}: {exc}"[:400]
            self.emit(run, "run_failed", {"error": run.error})
        finally:
            try:
                for channel in (send, receive):
                    if channel is not None:
                        channel.close()
                if process is not None:
                    if process.pid is not None:
                        process.join(timeout=1)
                        if process.is_alive():
                            process.terminate()
                            process.join(timeout=1)
                        if process.is_alive():
                            process.kill()
                            process.join(timeout=1)
                    if process.pid is None or not process.is_alive():
                        process.close()
            except Exception as exc:
                with self.lock:
                    if run.error is None:
                        run.error = f"Worker cleanup failed: {type(exc).__name__}: {exc}"[:400]
                        self.emit(run, "run_failed", {"error": run.error})
            finally:
                with self.lock:
                    run.done = True
                    if self.active == run.id:
                        self.active = None

    def snapshot(self, run_id: str, after: int) -> dict[str, Any]:
        with self.lock:
            run = self.runs.get(run_id)
            if run is None:
                raise HTTPException(404, "unknown live run")
            return {"id": run.id, "mode": run.mode, "scenario_ids": run.scenario_ids,
                    "created_at": run.created_at, "done": run.done, "error": run.error,
                    "provider": run.provider, "model": run.model, "dataset": run.dataset,
                    "events": run.events[after:], "next": len(run.events),
                    "report": run.report if run.done else None}


manager = LiveRuns()


@router.get("/capabilities")
def get_capabilities() -> dict[str, Any]:
    return {**capabilities(), "live_attempts_used": manager.live_attempts,
            "datasets": dataset_capabilities(),
            "data_scope": "Dataset-specific supplier inputs; orders are always simulated locally",
            "default_dataset": "official_snapshot" if official_configured() else "synthetic"}


@router.get("/scenarios")
def scenarios(dataset: str = Query(default="synthetic", pattern="^(synthetic|official_snapshot)$")
              ) -> dict[str, Any]:
    if dataset == "official_snapshot":
        return {"scenarios": [{"id": sc.id, "category": sc.category, "title": sc.title,
                "buyer": sc.buyer, "request": sc.request, "expected": "capture-dependent",
                "tier": "diagnostic", "recorded": False} for sc in diagnostic_scenarios()],
                "modes": ["live"], "dataset": dataset}
    smoke = {s.id for s in load_scenarios(tier="smoke")}
    core = {s.id for s in load_scenarios(tier="core")}
    return {"scenarios": [{"id": s.id, "category": s.category, "title": s.title,
                           "buyer": s.buyer, "request": s.request,
                           "expected": s.expect.get("status"),
                           "tier": "smoke" if s.id in smoke else
                                   "core" if s.id in core else "full",
                           "recorded": (RECORDED_DIR / f"{s.id}.json").exists()}
                          for s in load_scenarios()],
            "modes": ["offline", "recorded", "live"]}


@router.post("/runs", status_code=202)
def start_run(body: StartIn) -> dict[str, Any]:
    ids = body.scenario_ids
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "duplicate scenario id")
    try:
        if body.dataset == "official_snapshot":
            selected = [sc for sc in diagnostic_scenarios() if sc.id in ids]
        else:
            selected = load_scenarios(only=ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if len(selected) != len(ids):
        raise HTTPException(400, "select scenario ids, not categories")
    if body.mode == "recorded":
        unavailable = [s.id for s in selected if not (RECORDED_DIR / f"{s.id}.json").exists()]
        if unavailable:
            raise HTTPException(400, f"no model recording for: {', '.join(unavailable)}")
    run = manager.start(selected, body.mode, body.provider, body.model, body.dataset)
    return {"run_id": run.id, "scenario_ids": run.scenario_ids, "mode": run.mode,
            "provider": run.provider, "model": run.model, "dataset": run.dataset}


@router.get("/runs/{run_id}")
def get_run(run_id: str, after: int = Query(default=0, ge=0)) -> dict[str, Any]:
    return manager.snapshot(run_id, after)


@router.get("/runs/{run_id}/events")
def stream_events(run_id: str, after: int = Query(default=0, ge=0)):
    manager.snapshot(run_id, after)  # Validate before starting the response.

    async def stream():
        cursor = after
        while True:
            snapshot = manager.snapshot(run_id, cursor)
            for event in snapshot["events"]:
                yield f"id: {event['seq']}\nevent: {event['kind']}\ndata: {json.dumps(event)}\n\n"
            cursor = snapshot["next"]
            if snapshot["done"]:
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
