# Trusted Trade Agents

A gateway for external purchasing agents working with Valio Aimo: signed offers,
buyer mandates, human approval, deterministic policy checks and verifiable receipts.
Built during Junction 2026.

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
