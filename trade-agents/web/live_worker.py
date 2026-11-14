"""Spawned benchmark worker: all frozen clocks/environment/state remain in this process."""
from __future__ import annotations

from dataclasses import asdict
from multiprocessing.connection import Connection
from typing import Any

from trade_agents.bench.runner import run_bench
from trade_agents.bench.scenario import Scenario
from trade_agents.buyer_agent.offline import OfflineBuyerLLM

RECORDED_MODEL = 'nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b'


class RecordedOnly:
    """The replay wrapper must never invoke a live model."""
    name = RECORDED_MODEL
    vision = False

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError('recorded demo attempted a live model call')


def run_worker(scenarios: list[Scenario], mode: str, channel: Connection) -> None:
    """Only serializable events cross the pipe; no gateway/SQLite objects are shared."""
    try:
        factory = OfflineBuyerLLM if mode == 'offline' else RecordedOnly
        report = run_bench(
            scenarios, factory, replay='replay' if mode == 'recorded' else 'auto',
            save=False, baseline=False,
            on_start=lambda sc: channel.send(('scenario_started', {
                'id': sc.id, 'title': sc.title, 'category': sc.category,
                'buyer': sc.buyer, 'request': sc.request,
            })),
            on_trace=lambda sc, step: channel.send(('tool', {'scenario_id': sc.id, **asdict(step)})),
            progress=lambda result: channel.send(('scenario_finished', asdict(result))),
        )
        channel.send(('report', report.to_dict()))
    except Exception as exc:
        channel.send(('error', f'{type(exc).__name__}: {exc}'[:400]))
    finally:
        channel.close()
