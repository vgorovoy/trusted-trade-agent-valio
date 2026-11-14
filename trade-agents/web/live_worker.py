"""Spawned benchmark worker: all frozen clocks/environment/state remain in this process."""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from trade_agents.bench.runner import BenchReport, run_bench, run_scenario
from trade_agents.bench.scenario import Scenario
from trade_agents.buyer_agent.offline import OfflineBuyerLLM

RECORDED_MODEL = 'nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b'


class RecordedOnly:
    """The replay wrapper must never invoke a live model."""
    name = RECORDED_MODEL
    vision = False

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError('recorded demo attempted a live model call')


def run_worker(scenarios: list[Scenario], mode: str, channel: Connection,
               selection: dict[str, str] | None = None) -> None:
    """Only serializable events cross the pipe; no gateway/SQLite objects are shared."""
    try:
        if mode == 'live' and (selection or {}).get('dataset') == 'official_snapshot':
            from web.live_snapshot import run_official_worker
            run_official_worker(scenarios, channel, selection or {})
            return
        if mode == 'live':
            run_live_worker(scenarios, channel, selection or {})
            return
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
        channel.send(('error', f'{type(exc).__name__}: benchmark failed; diagnostics omitted'))
    finally:
        channel.close()


def run_live_worker(scenarios: list[Scenario], channel: Connection, selection: dict[str, str]) -> None:
    from web.live_models import LIMITS, PublicModel, bounded_model, validate_selection

    provider, model = selection.get('provider'), selection.get('model')
    validate_selection(provider, model)
    if len(scenarios) != 1 or scenarios[0].world.name == 'live':
        raise ValueError('Live model mode requires one synthetic scenario')
    os.environ['TTA_VALIO_REVIEW'] = 'off'
    sc = scenarios[0]
    channel.send(('scenario_started', {'id': sc.id, 'title': sc.title, 'category': sc.category,
                                      'buyer': sc.buyer, 'request': sc.request,
                                      'data_scope': 'synthetic supplier and orders; real model call',
                                      'human_stage': 'scripted' if sc.human else 'none'}))
    inner = bounded_model(provider, model)
    wrapped = PublicModel(inner, lambda kind, data: channel.send((kind, {'scenario_id': sc.id, **data})))
    try:
        # record means always fresh model calls. Only temporary replies are written;
        # shipped replay files and the interactive buyer database are untouched.
        with tempfile.TemporaryDirectory(prefix='tta-live-model-') as directory:
            result = run_scenario(sc, lambda: wrapped, replay='record', bench_dir=Path(directory),
                                  max_steps=LIMITS['max_steps'],
                                  on_trace=lambda step: channel.send(('tool',
                                      {'scenario_id': sc.id, **asdict(step)})))
        if result.skipped:
            result.skipped = 'Provider/model request unavailable; diagnostics omitted'
        if result.status == 'error':
            result.explanation = 'Agent or provider execution failed; no successful task is claimed'
        payload = asdict(result)
        payload['human_stage'] = 'scripted' if sc.human else 'none'
        channel.send(('scenario_finished', payload))
        report = BenchReport(wrapped.name, datetime.now(UTC).isoformat(), [result]).to_dict()
        report['execution'] = {'mode': 'live', 'provider': provider, 'model': model,
                               'limits': LIMITS, 'actual_model_calls': wrapped.calls,
                               'data_scope': 'Synthetic supplier/catalogue/orders; real provider calls',
                               'human_stage': 'scripted decisions are not model autonomy',
                               'harness_attribution': 'Observed checks; no causal save inferred'}
        channel.send(('report', report))
    finally:
        inner.client.close()
