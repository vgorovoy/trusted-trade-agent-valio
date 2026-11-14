# UI handoff: buyer, Valio perspective and interactive benchmark

This public snapshot includes the isolated interactive UI. See [snapshot provenance](../../PUBLIC_SNAPSHOT.md).

## One server, three screens

```bash
cd trade-agents
uv sync --dev
TTA_LOCAL=1 uv run uvicorn web.server:app --host 127.0.0.1 --port 8000
```

* `http://127.0.0.1:8000/` — Exchange: buyer request, cart, confirmation/approval inbox,
  signed-document checks, status and audit; switch Buyer/Valio perspectives.
* `http://127.0.0.1:8000/live` — interactive scenario execution and verifier evidence.
* `http://127.0.0.1:8000/how-it-works` — explanatory page, including a browser-only
  example.
* `/api/docs` — mounted gateway API documentation. `tta serve` starts a separate
  REST-only server; it is not the combined web UI.

For Exchange, create `.env` from the example, add your Valio/model API keys locally and
run `uv run tta ingest` once. Example explicit provider selection:

```bash
TTA_LOCAL=1 TTA_DEMO_WALLET=1 TTA_LLM_PROVIDER=nvidia \
TTA_LLM_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b \
uv run uvicorn web.server:app --host 127.0.0.1 --port 8000
```

Gemini uses `TTA_LLM_PROVIDER=gemini TTA_LLM_MODEL=gemini-3.5-flash` instead. The
request screen invokes the configured provider; it does not offer a per-request model
selector. Credentials stay in `.env`/server environment. Frozen time is optional via
`TTA_DEMO_CLOCK=2026-11-12T10:00:00+02:00`; it does not make live stock historical.
Local state, signing keys and evidence are isolated under `data/local-demo/`.

## Test checklist

1. Start without a catalogue and open Exchange: setup guidance must appear instead of a
   crash.
2. Open `/live`, select one offline case and run. Tool steps, checks and the report must
   come from execution, not fixed decorative numbers. Confirm model-call count is zero.
3. Select smoke/core/full and inspect actual scored/selected counts and checks.
   Preserve visible failures of the rule-based baseline; do not treat an older
   suite's score as the expectation for this snapshot.
4. Select Recorded Nemotron: this is replay, not live model reasoning. Check
   scored/selected, skips and replay-hit counts. File existence alone does not establish
   valid coverage.
5. While benchmark runs, refresh Exchange. Parent data/key directories and clocks must
   remain unchanged; this has an automated held-child concurrent-gateway regression
   test.
6. With configured catalogue/API, submit a kitchen need in Exchange. Inspect the chosen
   products, quantities, price and rejected alternatives; identify provider errors
   separately from refusals.
7. Use `confirm_cart`: no order should be committed before the buyer signs the actual
   cart. If the cart also exceeds its autonomous spend limit, confirmation must not
   bypass approval.
8. Confirm/approve through the demo wallet only in this local environment. Inspect
   receipt, original signed mandate/offers and cryptographic checks. Missing evidence
   must not be green.
9. Switch Buyer/Valio perspectives and inspect audit reasons/hash chain. This is a demo
   view switch, not production authentication/authorization or a separate Valio
   production system.
10. Check unavailable stock, passed cut-off and milk/may-contain rejection. Unknown
    terminal stock must not be described as verified unlimited physical stock.

For a fixed 540 EUR / 500 EUR-limit protocol walkthrough without any network, run
`uv run python scripts/demo_offline.py`. It explicitly labels scripted orchestration and
verifies confirmation → approval → receipt plus school milk/trace blocks.

## Current limits and deployment

The `/live` page currently supports only offline and recorded modes. Fresh model
comparisons are CLI-only; see `scripts/compare_models.py` and the evidence index.
Exchange supports text/photo ingredient drafts with one chef-review checkbox for the whole draft, cleared by any edit. Separate
text-menu and clean synthetic-image evaluations exist; they do not score complete
procurement or noisy photographs. A general third-party agent adapter is not implemented. A forecast
model is future work without real attendance/consumption data.

The UI and demo wallet have no production authentication. Keep localhost binding for
testing. Before deploying, choose a server/domain, add TLS and access control, use
isolated synthetic state and environment-only credentials, and retain local/video
fallback. Deployment is not completed by this handoff; do not expose an unauthenticated
wallet/model-call endpoint publicly.
