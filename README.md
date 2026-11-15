# Trusted Trade Agents

A gateway for external purchasing agents working with Valio Aimo: signed offers,
buyer mandates, human approval, deterministic policy checks and verifiable receipts.
Built during Junction 2026.

- **Live demo:** https://vladimir-gorovoy--junction-valio-demo-web.us-east.modal.run/
  (Valio's official challenge mock data, demo orders only). The buyer agent defaults to
  Nemotron 3.5 Super VL on Verda; Gemini 3.5 Flash, Kimi K3.1 Pro and DeepSeek 4.1 Flash are
  selectable. Gemini reads photos, menus and handwritten lists into drafts for review.
- **Evaluation Lab:** [/models](https://vladimir-gorovoy--junction-valio-demo-web.us-east.modal.run/models)
  shows every run with each failed check visible; [/live](https://vladimir-gorovoy--junction-valio-demo-web.us-east.modal.run/live)
  runs a benchmark scenario on a live model.
- **Video (1:50):** https://youtu.be/h5w2kXHI4P8

## Agent Readiness Bench results

Frozen 41-case synthetic procurement and safety suite, 5 runs per model per case.
pass^5 = passed in every run; pass@5 = passed at least once. Costs at public list prices
without cache discounts (see `trade-agents/bench/prices-20261114.json`).

| Model | Runs on | pass^5 | pass@5 | Cost per 41-case run | Median time per case |
| --- | --- | --- | --- | --- | --- |
| Nemotron 3.5 Super VL | Verda | 40 / 41 | 40 / 41 | $0.17* | 7.2 s |
| Kimi K3.1 Pro | Verda | 37 / 41 | 41 / 41 | $2.08 | 30 s |
| DeepSeek 4.1 Flash | Verda | 36 / 41 | 41 / 41 | $0.15 | 7.7 s |
| Gemini 3.5 Flash | Google | 36 / 41 | 39 / 41 | $2.45 | 16 s |

\*Public per-token price; our runs used a dedicated Verda container. Time is wall-clock
per case including provider rate-limit waits. Synthetic scenarios: evidence, not a ranking.
Reproduce with `trade-agents/scripts/eval_suite/` (`pin.py` → `run_model.py` → `publish.py`).

## Offline demonstration

```bash
cd trade-agents
uv sync --dev
uv run python scripts/demo_offline.py
uv run tta bench --tier smoke --provider offline --no-save --min-coverage 1
uv run pytest -q
```

The offline demonstration uses scripted orchestration and synthetic products, temporary
state and mocked API responses. It makes no LLM/network calls or production orders.
For model-based purchasing and live mock API usage, see the [project README](trade-agents/README.md).

- [Benchmark tiers and metrics](trade-agents/bench/README.md)
- [External-agent benchmark direction](trade-agents/docs/external-agent-bench-demo.md)
- [MIT license](LICENSE)

Valio provides no order API for this challenge. Our order backend demonstrates approval
and receipts without sending orders to Valio. Ed25519 signatures prove origin/integrity;
they do not guarantee stock or price fairness. AP2/VC alignment describes design mappings,
not certified interoperability.
