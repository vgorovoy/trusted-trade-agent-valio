# Valio Aimo Agent Gateway

Junction 2026, challenge *Trusted Trade Agents* by Valio Aimo.

An interface through which an **external purchasing agent** can work with Valio Aimo:
find products, get price-locked offers, and place orders **within a mandate signed by
the buyer organisation**. Anything outside the mandate goes to a human. Every step is
written to a hash-chained audit trail that both buyer and Valio can verify.

The LLM proposes, deterministic code decides: guardrails never depend on a model's mood.

```
Buyer's agent (any LLM, MCP client)          Buyer's human approver (UI / REST)
            │ MCP tools / REST (OpenAPI 3)                │ approve / reject
            ▼                                             ▼
┌──────────────────────── Agent Gateway ────────────────────────────┐
│ catalogue   SQLite FTS5 trigram (fi/en/sv) + GS1 allergens/claims │
│ offers      live price + availability, cut-offs, unit conversion, │
│             price lock (offer_id, TTL)                            │
│ planner     cover a need: ranked options, split on partial stock, │
│             every ruled-out candidate with its reason             │
│ policy      signed mandate -> ok / approval / block per rule      │
│ orders      idempotent; autonomy mode x Agent Passport            │
│ signing     Ed25519 JWS: mandate, offers, cart, receipt           │
│ audit       append-only, SHA-256 hash chain                       │
└──────────────┬───────────────────────────────────────────────────┘
               │ X-API-Key (server side only)
               ▼
   Valio Aimo mock API: /api/price, /api/availability, data files
```

## Run

```bash
cd trade-agents
cp .env.example .env            # put the API key from the challenge page in VALIO_API_KEY
uv sync
uv run tta ingest               # downloads data files, builds data/catalog.sqlite (~5 s)
uv run tta search maito --claim FREE_FROM_LACTOSE
uv run tta plan maito 1000 L --date 2026-11-16 --claim FREE_FROM_LACTOSE
uv run python scripts/demo_scenarios.py      # four demo scenarios on live data (4: strict mode)
uv run tta serve                # REST API, docs at http://127.0.0.1:8000/docs
TTA_DEMO_WALLET=1 uv run tta serve   # same, plus the demo wallet endpoint for the UI
uv run tta mcp                  # MCP server on stdio for an agent
uv run pytest                   # offline tests (mocked API)
```

### One UI, different tasks

| Audience / purpose | Screen | What is ready | Dependencies |
|---|---|---|---|
| Buyer / chef | `/` (Exchange) | Kitchen request, cart, confirmation/approval inbox, signed documents, order status and audit | Static catalogue + Valio mock API key; a model API key for LLM purchasing |
| Jury: procurement demonstration | Same Exchange | Show the proposal, human decision, receipt and audit; demo wallet stands in for the buyer signing device | Same as Exchange; no production webshop orders |
| Jury: interactive benchmark | `/live` | Select scenarios; stream tool steps/checks and show a measured report | No catalogue ingestion or API credentials for offline/replay |
| Team: system explanation | `/how-it-works` | Standalone explanatory page; browser example only | No model or order execution |

A single local server serves these screens and mounts gateway REST at `/api`. The demo
UI/wallet has no production authentication; bind it to localhost. `tta serve` starts the
REST API only, **not** this web UI.

```bash
# Exchange and human approval (requires .env with Valio + chosen model credentials)
cd trade-agents
uv sync --dev
uv run tta ingest
TTA_LOCAL=1 TTA_DEMO_WALLET=1 uv run uvicorn web.server:app --host 127.0.0.1 --port 8000
# http://127.0.0.1:8000/ ; same server: /live and /how-it-works
```

For a benchmark-only demonstration, skip ingestion and credentials:

```bash
TTA_LOCAL=1 uv run uvicorn web.server:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000/live
```

The UI's benchmark modes are **offline** (rule-based baseline) and **recorded Nemotron**
(replay). They are animated real verifier/tool executions, not live model evaluations.
Live model runs are currently CLI-only; use explicit provider/model flags, e.g.
`tta bench basic-milk --provider nvidia --model nvidia/nemotron-3.5-super-vl-120b-a12b --replay record`.
A recording file may be stale: its existence does not guarantee complete replay coverage.
Always inspect scored/selected and skips.

Benchmark execution uses a separate **spawned process**, not an ASGI worker thread.
Temporary benchmark environment, clocks, UUIDs, keys and SQLite cannot alter the Exchange
process. A monitor thread only receives events/report through IPC. One run is allowed
per server process; failed or timed-out children are terminated/reaped and release the slot.
The default worker limit is 120 seconds for these offline/replay runs. A force-killed
child may leave synthetic temporary files for OS cleanup; no parent state is used.

The primary demo should be an actual buyer-agent workflow; the deterministic script below
is a separately labelled protocol walkthrough and a reproducible fallback. Do not equate
an offline score with model quality, or claim a general external-agent SDK or broad
real-world vision evaluation from a single synthetic recipe image.

### Human window (stream U)

```bash
cd trade-agents
uv run tta ingest                       # once; needs the Valio mock API key in .env
TTA_DEMO_WALLET=1 uv run uvicorn web.server:app --port 8000
# open http://127.0.0.1:8000
```

Without an Agent Passport the gateway caps the agent at `confirm_cart`, so the
`autonomous` choice ends in a cart confirmation and the screen says why. To show the
autonomous path locally, add `TTA_DEMO_SELF_ISSUE=1` (demo only, see Agent Passport
below) or set `TTA_AGENT_PASSPORT` from `tta passport issue`.

The standalone Russian explainer is at `http://127.0.0.1:8000/how-it-works` or
`web/how-it-works.html` opened directly. Its interactive example runs entirely in the
browser and never places an order.

The English live benchmark is at `http://127.0.0.1:8000/live`. It runs the current
buyer agent and gateway against isolated benchmark worlds, streams tool calls and
independent checks, and offers one case or the 12/25/37-scenario tiers. Offline mode
uses the rule-based agent; Recorded Nemotron mode replays three saved model trajectories
through today's code. Neither mode calls a live model, the Valio API, or the production
state store. The page needs the local web server and cannot run as a standalone file.
To show this page without catalogue ingestion or API credentials, start the server with
`TTA_LOCAL=1 uv run uvicorn web.server:app --port 8000` from `trade-agents/`.
If `uv` is not on your PATH but the project `.venv` already exists, use
`TTA_LOCAL=1 .venv/bin/python -m uvicorn web.server:app --port 8000` instead.

The web screen shows the agent ↔ Valio exchange, signed documents, policy outcome,
approval inbox, and buyer/Valio audit perspectives. It starts the existing buyer
agent with a kitchen request; choose `autonomous`, `confirm_cart`, or `advise` to
demonstrate the mandate modes. Cart confirmation uses the **demo wallet** with the
local buyer key, then calls the gateway's human-only cart endpoint. This UI and
wallet are for a local demo, without user authentication. Do not expose them on a
public network. The demo order backend is still a mock, as described below.

The UI records the original signed mandate and offers under
`data/web_evidence/<order_id>.json` so it can verify them against buyer JWKS and
Valio JWKS after the order. Older orders without that sidecar show missing evidence
instead of claiming verification. The signed receipt remains in the order itself.

MCP client config (Claude Desktop / Claude Code / any MCP host):

```json
{"mcpServers": {"valio-aimo": {"command": "uv", "args": ["run", "--directory", "<path>/trade-agents", "tta", "mcp"]}}}
```

## Reproducible offline protocol demo

```bash
uv run python scripts/demo_offline.py
uv run python scripts/demo_offline.py --json
```

This is **scripted orchestration**, not Gemini output: no API key, catalogue download,
LLM calls or network are needed. It creates a synthetic world with temporary SQLite and
keys, verifies the buyer mandate and Signed Offer, then exercises a 540 EUR hotel cart
against a 500 EUR limit: cart confirmation → pending approval → human approval → Signed
Receipt and audit verification. School examples independently block both milk and traces.
Production state is untouched and temporary demo data is removed. It is a protocol/safety
walkthrough; use the buyer agent separately when demonstrating model-based product choice.

## Explicit demo runtime

Aiven/PostgreSQL remains the configured state store. Connection attempts use libpq
`connect_timeout=3` (per host; this does not bound total DNS/multi-host time). Failure
raises a sanitized error without switching databases or exposing connection credentials.

```bash
uv run tta --local serve
uv run tta --demo-clock '2026-11-12T10:00:00+02:00' serve
# Equivalent for direct Python/ASGI/MCP entrypoints:
TTA_LOCAL=1 uv run tta serve
TTA_DEMO_CLOCK='2026-11-12T10:00:00+02:00' uv run tta serve
```

`--local` / `TTA_LOCAL=1` ignores PostgreSQL URLs and uses `data/local-demo/` for
orders, offers, audit, signing keys, buyer registry and web evidence. Static catalogue
and raw files are shared with the normal data directory; run `tta ingest` first.
Frozen demo time **also implies local mode**, covers delivery cut-offs, offer expiry,
mandates and passports, and is printed by the CLI. It does not make live stock historical
or remove the need for Valio API access: use benchmark world/cassette replay for offline
runs. An ISO timestamp with timezone is required. Without these flags, time is real.
Start a fresh process when changing runtime modes.

Benchmark tiers are fixed and nested (`smoke` ⊆ `core` ⊆ `full`):

```bash
uv run tta bench --tier smoke --provider offline --no-save
uv run tta bench --tier core --provider gemini --replay replay --min-coverage 1
uv run tta bench --tier full --provider gemini --replay record
```

Recording makes paid/live model calls; replay does not evaluate a new model or prompt.
A replay miss is reported as skipped, so use `--min-coverage 1` before quoting a score.
See [bench/README.md](bench/README.md) for scenario membership, metrics and limitations.

## Interface

| MCP tool | REST | What it does |
|---|---|---|
| `search_products` | `GET /v1/products/search` | static catalogue search with hard filters (claims, allergens, labels) |
| `get_product` | `GET /v1/products/{id}` | ingredients, nutrition, allergens, official substitutes |
| `get_offers` | `POST /v1/offers` | live price + availability per product, each a **Signed Offer** (`offer_id`, `valid_until`, `signature`) |
| `plan_need` | `POST /v1/plans` | "1000 L lactose-free milk by Monday" → up to 3 options, good / better / best (lines are Signed Offers) + ruled-out list; `optimize` = `price` \| `reliability` \| `co2` \| `balanced`, `max_premium_pct` |
| `find_substitutes` | `GET /v1/products/{id}/substitutes?requested_date=&amount=&unit=&exclude_allergens=&exclude_may_contain=&require_claims=&limit=` | allergen-safe substitutes, official Valio ones first, each with `differences`; with `requested_date` also a Signed Offer per option (`offer`, `orderable`). An unknown allergen name is a 400 |
| `place_order` | `POST /v1/orders` | commit under a signed mandate (+ `agent_passport`, `alternatives`) → `placed` / `pending_approval` / `awaiting_cart_confirmation` / `proposal` / `rejected` |
| `get_order`, `explain_order` | `GET /v1/orders/{id}`, `GET /v1/audit` | state, signed `receipt`, human-readable trail |
| `add_note` | `POST /v1/audit/notes` | agent's own reasoning in the trail; `order_id` files it under an order |
| `get_valio_keys` | `GET /.well-known/valio-agent-keys` | Valio's public keys (JWKS, Ed25519) |
| — | `GET /schemas`, `GET /schemas/{name}` | JSON Schemas of the protocol documents (see [Standards](#standards)) |
| — (humans only) | `GET /v1/carts`, `POST /v1/carts/{id}/confirm\|decline` | buyer confirms the cart the agent filled (signed by the buyer key) |
| — (humans only) | `POST /v1/approvals/{id}/approve\|reject` | sign-off on exceptions; agents have no approval tool by design |
| — (demo only) | `POST /demo/wallet/cart-confirmations` | stand-in for the buyer's wallet, signs a cart confirmation; off unless `TTA_DEMO_WALLET=1` |

CLI: `tta plan maito 200 L --claim FREE_FROM_LACTOSE --optimize balanced --max-premium 5`,
`tta substitutes 464201 --exclude milk --date 2026-11-16 --amount 200 --unit L`.

**Plan result.** Each option in `options` carries:

- `rank`: `best_price` | `most_reliable` | `lowest_co2` | `balanced` (objective winners,
  the buyer's `optimize` first), then `runner_up` / `third` (next cheapest) so there are up
  to three distinct options whenever the candidates allow;
- `objectives`: every objective this option wins (one option may win all three; runner-ups
  have `[]`). Unknown metrics never win an objective;
- `trade_off` against the cheapest option, in absolute values:
  `+4.0 % price (202.00 → 210.08 EUR); reliability 0.92 → 0.99; CO₂ 1.20 → 0.95 kg CO₂e/kg
  (category avg)`; missing data is said, e.g. `CO₂ 5.12 kg CO₂e/kg (category avg), cheapest
  option: no data`;
- `optimization_note` when the requested objective has no data within the premium cap.

Options above `max_premium_pct` are **not shown** (the buyer set the cap); each appears in
`ruled_out` with the reason (`over max_premium_pct 5 %: 312.00 EUR is +20.0 % vs the cheapest
260.00 EUR`). `explanation` names the chosen option and the alternatives with their
trade-offs; `live_api` says how many products were priced live and the cap.

**Allergens.** Filters accept the GS1 codes that occur in the Valio data (`AM`, `ML`, `AW`,
`SH`, also unnamed ones such as `NC`) and names or synonyms in English and Finnish. One
predicate, `catalog.AllergenFilter`, is used by search, `plan_need` (also with the agent's
own `product_ids`), substitutes and the mandate check, for "contains" and (on request)
"may contain" alike. A name stands for a set of codes, because products are tagged with
either the umbrella or the specific code:

| Name | Codes excluded |
|---|---|
| `milk`, `maito`, `AM` | AM, ML |
| `lactose`, `laktoosi`, `ML` | ML, plus AM unless the product claims `FREE_FROM_LACTOSE` or `LOW_LACTOSE` |
| `gluten`, `AW` | AW, UW (wheat), NR (rye), GB (barley), GS (spelt), GK (kamut); oats (GO) only if `oats` is named |
| `wheat`, `UW` | UW, AW |
| `nuts`, `tree nuts`, `AN` | AN, SA, SC, SH, SM, SP, SR, ST, SW (peanuts AP separate; `nuts and peanuts` adds AP) |

An unknown name or code is an error everywhere (400 over REST, a validation error on a
mandate) instead of silently excluding nothing.

**Substitutes.** Valio's own `substitutions` list covers only about 200 of 17.5k products,
so the gateway finds substitutes itself. Each option has a `tier`: `official` (Valio's list)
first, then `category` (the most similar products in the same category), and, when fewer
than 3 safe options are found there (e.g. milk excluded and the category is milk), `family`
(same category family, e.g. `104xx` dairy and plant drinks), marked "related category …
check with the kitchen" and `needs_kitchen_check: true`. A substitute must pass the allergen filter,
carry the required claims, and (unless official) keep the original's dietary claims
(lactose-free, gluten-free, egg-free, wheat-free, vegan…). `VEGAN` with no milk or lactose
among contains / may contain implies lactose-free; `FREE_FROM_MILK` is never inferred. The
first 20 ruled-out candidates are returned with `ruled_out_count`. Marketing claims a
substitute loses are listed in `differences`.
Ordering one still goes through the mandate: the line carries `substitutes_for` and
`substitution_policy` decides whether a human signs off (a `family` one says "check with the
kitchen" in the reason).

**Price delta on replacements** (Valio mentors: a kitchen needs 100 kg of meat, only 50 kg
of it is in stock, the other 50 kg is a dearer product; that breaks a school's or
restaurant's economics). The mandate's `max_substitute_price_increase_pct` (default 10)
caps how much more per kg / litre (per sales unit without a comparison unit) a replacing line
may cost than the product it replaces; above it a human approves even under
`official_auto`. Replacing lines are those with `substitutes_for` and the top-up lines of a
split plan (the gateway marks them `fills_shortfall_of` when it locks the offers). The
original's price never comes from the agent: a signed offer for the original in the same
order, else the price the gateway quoted for the original when it priced the substitutes or
built the split (stored with the price lock), else one live price lookup for the original at
order time. No verified price, or prices in different units: a human compares.

**Provenance plus declaration.** The substitution and price rules depend on where the offer
came from as well as what the agent declares. An offer from `find_substitutes` carries the
original it was priced against (`price_reference`, stored with the price lock): ordered
without `substitutes_for`, the line is still treated as replacing that original (both
rules apply, the order line records `substitutes_for_inferred: true`); declared with a
different original, a human approves (rule `substitution.provenance`). A split plan's
top-up keeps its own provenance (`fills_shortfall_of`, price-checked as above). A plainly
quoted product (`quote`, or a plan's main pick) ordered without a declaration is a normal
order, with no substitution or price-delta check.

**Re-sign mandates after a field is added.** The mandate signature covers every field,
defaults included, so mandates signed before `max_substitute_price_increase_pct` existed no
longer verify (rule `mandate.signature` blocks them). Re-sign them; the demo profiles sign
fresh mandates on every run. Dropping default-valued fields from the signed payload was
rejected: it would break every mandate signed since the field was added and makes the
signed bytes depend on code defaults.

### Valio API usage

Valio asks to query the live APIs only when needed, never for all products. The gateway:

- searches the **static catalogue** first (built once by `tta ingest`); text search and
  structured filters never touch the live API;
- asks for live price + availability only for **narrowed candidates**, at most
  `TTA_MAX_LIVE_PRODUCTS` (default 40) per call: `get_offers`, `plan_need` with
  `product_ids` and `find_substitutes` above the cap return an error asking to narrow with
  `search_products`; search-driven plans use at most 25 candidates;
- caches live answers for 120 s (`TTA_API_CACHE_S`) and batches up to the API's limit;
- re-checks availability only for the lines of an order, right before committing;
- counts live product lookups per gateway (`Gateway.live_lookups`, logged at INFO);
- the tests use mocked answers only, and the bench (stream E) runs on recorded / mocked
  answers, never on the live API.

## Trust protocol

Signed documents, named after Google AP2 (Intent / Cart Mandate) and Mastercard Verifiable
Intent. All are JWS compact, `alg: EdDSA` (Ed25519, RFC 8037), `kid` = RFC 7638 thumbprint,
payload = canonical JSON (sorted keys, no whitespace). Code: `src/trade_agents/signing.py`.

| Document | Signed by | `typ` | Where |
|---|---|---|---|
| Mandate (Intent Mandate) | buyer key | `valio.mandate.v1`, **detached** (`header..sig`) over the mandate without `signature` | `mandate.signature` |
| Signed Offer | Valio | `valio.offer.v1`, claims inside | `offer.signature` on every quote result and plan line |
| Cart confirmation (Cart Mandate) | buyer key | `valio.cart_confirmation.v1`: `order_id, buyer_id, cart_hash, decision, confirmed_by, note` | body of `/v1/carts/{id}/confirm\|decline` |
| Order Receipt | Valio | `valio.receipt.v1`: order number, lines, totals, `rules_fired`, approvers, `audit_head` | `order.data.receipt = {claims, jws}` |
| Agent Passport | Valio | `valio.agent_passport.v1`: `agent_id, model, harness, max_mode, issued_at, exp, bench_score?` | `place_order(agent_passport=...)` |

Keys: Valio's key is created once in `data/keys/valio-signing.pem`; buyer public keys are
registered in `data/buyers/<buyer_id>.json` (JWKS). Both directories are gitignored.

```bash
uv run tta buyer-keys hotel-chain-001                      # demo buyer key pair + registry entry
uv run tta mandate --buyer hotel-chain-001 --mode confirm_cart --approver chef@hotel.example
uv run tta passport issue --agent buyer-agent --model gemini-2.5-flash --max-mode autonomous --days 7
uv run tta cart-confirmation <order_id> --by chef@hotel.example   # demo wallet, prints JWS
```

Agents verify what Valio signed with nothing but the JWKS:

```python
from trade_agents.signing import verify_offer, verify_receipt
jwks = httpx.get(f"{base}/.well-known/valio-agent-keys").json()
claims = verify_offer(offer_or_plan_line, jwks)   # raises SignatureError on any mismatch
```

`verify_offer` checks the signature and that the visible `offer_id`, `product_id`, price,
delivery date, cut-off, status and `valid_until` match the signed claims. Any malformed
token (bad base64, header or payload not a JSON object, unknown `kid`) is a
`SignatureError`, never a server error.

An Agent Passport expires: `exp` is an RFC 7519 NumericDate, 7 days after issue by default
(`--days`). An expired passport, or one without `exp`, counts as no passport.

The demo wallet (`BuyerRegistry.demo_key`, `TTA_DEMO_WALLET=1`) only signs for buyers whose
private key it created itself; a buyer registered with just a public key signs on its own
device, and the demo wallet refuses instead of minting a new key for it.

### Autonomy modes

`mandate.autonomy_mode` ∈ `advise` < `confirm_cart` < `autonomous` (default). The gateway
uses `effective = min(mandate.autonomy_mode, passport.max_mode)`; without a valid passport
for this `agent_id` (missing, other agent, not signed by Valio, expired) the cap is
`confirm_cart`.
Guardrails always run first and a `block` is `rejected` in every mode. Then:

| Effective mode | `place_order` returns | Next step |
|---|---|---|
| `advise` | `proposal` + `data.cart` | nothing is ordered; the agent only recommends |
| `confirm_cart` | `awaiting_cart_confirmation` + `data.cart` (lines, total, rationale, alternatives, `after_confirmation`, `exceptions`, `cart_hash`) | buyer confirms with a signed confirmation → `placed`, or `pending_approval` if guardrails still need a sign-off (e.g. over the spend limit), then approve as usual; or declines → `cart_declined` |
| `autonomous` | `placed` or `pending_approval` | approval inbox as before |

Cart confirmation says *what* to buy; approval authorises an *exception* to the mandate.
They stay separate steps, so a confirmed cart over the limit still needs an approver.
Confirmation and approval windows hold the price for 24 h (then `expired`).
`data.autonomy` records `requested`, `passport_max`, `effective` and the reason.

### Mandate (buyer → gateway)

A buyer organisation signs what its agent may do on its own:

```json
{
  "mandate_id": "mandate-hotel-demo", "buyer_id": "hotel-chain-001", "buyer_name": "Demo Hotel Chain",
  "agent_id": "hotel-buyer-agent", "valid_until": "2026-12-13T21:00:00+02:00",
  "auto_order_limit_eur": 1000, "hard_limit_eur": 20000,
  "excluded_allergens": ["milk"], "exclude_may_contain": false,
  "required_claims": ["FREE_FROM_LACTOSE"],
  "substitution_policy": "approval", "max_substitute_price_increase_pct": 10,
  "min_delivery_reliability": 0.9, "allow_late_delivery": false,
  "approvers": ["chef@hotel.example"],
  "autonomy_mode": "confirm_cart",
  "signature": "eyJhbGciOiJFZERTQSIsImtpZCI6Ii4uLiIsInR5cCI6InZhbGlvLm1hbmRhdGUudjEifQ..<Ed25519 signature>"
}
```

In code: `Mandate(...).sign(BuyerRegistry(settings.buyers_dir).demo_key(buyer_id))`, or
with the buyer's own `Ed25519PrivateKey` after registering its public key.

### Guardrails

| Rule | Outcome |
|---|---|
| mandate signature invalid, mandate expired | block |
| offer price lock expired, no price | block |
| quantity not a multiple of the lot size | block |
| out of stock, sales-blocked, no availability data, order cut-off passed | block |
| more than available | block (the planner proposes a split instead) |
| contains an excluded allergen (or "may contain", if the mandate says so) | block |
| total above `hard_limit_eur` | block |
| total above `auto_order_limit_eur` | human approval |
| this order + what the agent placed on its own (no approval, no cart confirmation) under the same mandate in the last 24 h above `auto_order_limit_eur` (split-order protection, rule `spend.cumulative`) | human approval |
| required claim missing (e.g. not marked lactose-free) | human approval |
| substitution (unless `official_auto` and on Valio's own substitution list) | human approval |
| a replacing line (substitute, or a split plan's top-up of a partial stock) costs more per kg / litre than the original by more than `max_substitute_price_increase_pct` (default 10 %), whatever `substitution_policy` says; also when the original's price cannot be verified (rule `substitution.price`) | human approval |
| supplier delivery reliability below the mandate minimum | human approval |
| earliest delivery after the requested date (unless allowed) | human approval |

Availability is re-checked right before commit; the quoted price stays locked.
Orders are idempotent on `(buyer_id, idempotency_key)`: a key never returns another buyer's
order. Reading the 24 h spend, deciding and recording an order happen under one lock, so
parallel `place_order` calls cannot both fit into the same remaining budget (one gateway
process; several processes on one database would need `BEGIN IMMEDIATE`). Pending
approvals hold the price for 24 h. `add_note` with `order_id` is accepted only from the
agent that placed the order.

## Agent (kitchen purchasing agent)

`src/trade_agents/buyer_agent/` is our own buyer's agent. It is an external client of the
gateway: it works only through the same tools the MCP server exposes, never touches the
database, and cannot see or change the mandate.

```bash
uv run tta agent "1000 litres of lactose-free milk by Monday" --buyer hotel
uv run tta agent "20 kg margarine for Wednesday" --buyer school --json   # full trace
uv run tta agent --menu path/to/menu.jpg --guests 80 --buyer school      # needs a vision model
TTA_GATEWAY_URL=http://127.0.0.1:8000 uv run tta agent "..."            # via REST (tta serve)
```

Flow: parse the need (product, amount, unit, date, constraints) → `search_products` with
the mandate's filters → the model drops near-misses itself (coffee milk, cocoa drinks,
quark, powder…) → `plan_need(product_ids=…)` (good / better / best with trade-offs, the
buyer's `optimize` / `max_premium_pct` from the profile) → if the product is unavailable or
blocked by the mandate, `find_substitutes` (mandate filters applied by the tool layer) and
a substitute line with `substitutes_for`, never a silent switch → `place_order` once, with a
rationale →
`finish` with `{status, order_id, chosen, alternatives, dropped_candidates,
explanation_for_chef}` and a step trace (tool, arguments, one-line result) for the UI.

Agent-side guards on top of the gateway's rules:

- the signed mandate is attached by the tool layer; extra arguments such as a "raised
  limit" are ignored;
- one `place_order` per request: after `pending_approval` / `rejected` any further attempt
  (resubmit, split, shrink) is refused and shows in the trace;
- `plan_need` requires the agent's own `product_ids`, so relevance is always judged;
- instruction-like text in product data is flagged to the model as untrusted and written
  to the audit trail (`agent.note`);
- step and token budget; the final status comes from the gateway, not from the model.

Demo buyers (`buyer_agent/profiles.py`, signed via one `sign_mandate` helper), each with an
explicit `strictness` (Valio mentors: schools and hospitals need a strict mode):

| Profile | Strictness | Mandate |
|---|---|---|
| `hotel` | lenient | autonomous limit 1000 EUR, requires `FREE_FROM_LACTOSE`, "may contain" traces allowed, a replacement may cost up to +15 % per kg / litre; ranks `optimize=balanced` within 5 % of the cheapest |
| `school` | strict | 500 EUR, excludes the milk allergen, every substitute with a human, replacements up to +5 %; cheapest first |
| `hospital` | strict | 300 EUR, excludes gluten including "may contain" traces, every substitute with a human, no dearer replacement without a human (0 %), reliability at least 0.95 |

The 24 h cumulative spend rule applies to every profile. Strict profiles add one line to the
agent's prompt; the hotel's prompt and `substitution_policy` (`approval`) are unchanged so
the recorded bench replays stay valid. `sign_mandate` signs with the demo buyer key from `data/buyers/`
(standing in for the buyer's wallet). A gateway reached over `TTA_GATEWAY_URL` must share
that data directory.

Agent Passport: the agent does **not** issue its own. Valio issues it and the agent gets
it from the environment, `TTA_AGENT_PASSPORT_<AGENT_ID>` (e.g.
`TTA_AGENT_PASSPORT_HOTEL_BUYER_AGENT`) or `TTA_AGENT_PASSPORT`:

```bash
export TTA_AGENT_PASSPORT_HOTEL_BUYER_AGENT=$(uv run tta passport issue \
    --agent hotel-buyer-agent --model gemini-3.5-flash --max-mode autonomous)
uv run tta agent "1000 litres of lactose-free milk by Monday" --buyer hotel
```

Without a passport the gateway caps the agent at `confirm_cart`, so every cart waits for
the buyer. For a quick local demo only, `TTA_DEMO_SELF_ISSUE=1` lets the agent mint a
passport (max `autonomous`) with Valio's local key from `data/keys/`; it logs a warning,
because a real agent never holds the issuer's key. The agent's final status can also be `awaiting_cart_confirmation` or
`proposal` (mandate / passport mode), and `error` with `error` set if the model fails.

Menu photo (`buyer_agent/menu.py`): the vision model turns a weekly menu or handwritten
list plus the number of guests into needs (ingredient, amount, unit, date), then the agent
runs once per need. `--vision-provider gemini` lets a text-only agent model use Gemini
for the photo.

### Providers (`src/trade_agents/llm.py`)

One adapter with tool calling; pick with `TTA_LLM_PROVIDER` / `--provider`, model with
`TTA_LLM_MODEL` / `--model`. Without any key the agent falls back to `offline`, a
rule-based stand-in that speaks the same tool protocol (for UI and bench work).

| Provider | Env | Default model |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` (AI Studio) | `gemini-3.5-flash` (`-latest` aliases are not served on API v1) |
| `openai` | `OPENAI_API_KEY`, optional `OPENAI_BASE_URL` | `gpt-5-mini` |
| `verda` | `VERDA_API_KEY` or `VERDA_INFERENCE_API_KEY`; `VERDA_BASE_URL` defaults to `https://inference.verda.com/v1`. The model must be enabled in the Verda project | `kimi-k3-1-pro` (also `deepseek-4-1-flash-1017`) |
| `nvidia` | `NVIDIA_API_KEY` (build.nvidia.com), optional `NVIDIA_BASE_URL` | `nvidia/nemotron-3.5-super-vl-120b-a12b` |
| `offline` | — | keyword rules, no LLM |

Transient provider errors (429, 5xx, e.g. Gemini 503 "high demand") are retried with backoff (2, 5, 10 s).
Live smoke run 13.11 ("200 litres of lactose-free milk by Monday", hotel): Gemini 3.5 Flash, Kimi K3.1 Pro on Verda and Nemotron 3.5 Super VL all reached `placed` through the gateway tools.

Other knobs: `TTA_LLM_TEMPERATURE` (default 0 for OpenAI-compatible, model default for
Gemini 3), `TTA_LLM_VISION=0` to disable images for a text-only model (on by default for all presets), `TTA_GATEWAY_URL`.

## Storage

Gateway state (price-locked offers, orders, the hash-chained audit trail) sits behind one
contract, `store.StateStore`, with two implementations:

- **SQLite** (default): `data/state.sqlite`. Writes and the "read cumulative spend ->
  decide -> insert order" section are serialised by an in-process lock, so it is safe
  across threads of **one** gateway process only.
- **PostgreSQL, e.g. Aiven for PostgreSQL** (`store_pg.PgStore`, psycopg 3): set
  `TTA_STORE_URL` or `AIVEN_PG_URL` to the service URI (`postgres://…?sslmode=require`;
  never committed, never logged). Same schema semantics (`UNIQUE(buyer_id,
  idempotency_key)`, identical audit hashes); tables are created on first start. The order
  section takes a transaction-level advisory lock and audit appends take another, so
  **several gateway processes** (REST + MCP + workers) can share the database without
  overspending the cumulative autonomous limit or forking the audit chain.
  `next_order_number` stays count-based like SQLite (the mock backend's numbering).
  Inside the lock, writes commit when the block ends even if Python raises (as in SQLite);
  only a database error rolls back. A dropped connection (maintenance, idle timeout) is
  reopened on the next call; `sslmode=require` is appended when the URL has no `sslmode`
  (except localhost).

Contract tests (`tests/test_store_contract.py`) run against SQLite always and against
Postgres when `TTA_TEST_PG_URL` is set (each test in its own schema, dropped afterwards;
the user needs `CREATE` on the database):

```bash
TTA_TEST_PG_URL='postgres://avnadmin:…@<service>.aivencloud.com:<port>/defaultdb?sslmode=require' \
  uv run pytest tests/test_store_contract.py
```

They include a race of six gateways, each with its own connection, against one mandate:
only the orders that fit the limit are placed. Checked on a local PostgreSQL 14; Aiven
credentials are not configured yet.

## Valio review agent (seller side)

`src/trade_agents/valio_agent/` is Valio's own agent. It is **advisory only**: it never
places, approves or confirms an order; humans and the deterministic policy engine decide.
After every order event (created by `place_order`, placed after an approval or a cart
confirmation) it reviews the order for Valio's account manager:

1. **Deterministic checks**, each `ok | note | flag` with evidence: order line == locked
   offer (product, name, unit and price equal the offer Valio stored when it signed it;
   the JWS the agent received is not stored, the agent verifies it), total = sum of lines,
   Order Receipt verifies against Valio's JWKS; allergen / may-contain / required-claim
   double check against the mandate and the catalogue with the same `AllergenFilter` as
   search, plan and the policy engine (gluten -> wheat, rye..., nuts -> tree nuts, lactose
   -> milk without a lactose-free claim); substitutions vs Valio's official list; delivery status, historical
   reliability, hours left to the cut-off while the order waits for a human; this buyer's
   own history (new product, quantity vs median of earlier lines, unit price vs last paid);
   mandate signature, effective autonomy mode, policy outcome, human decisions.
2. **Explanation** by an open-weight model: the order facts and check results go into the
   prompt as compact JSON; the model returns `{summary, risk, flags[], suggested_action}`,
   validated; it may not invent facts and cannot lower the rules' risk level. If the model
   fails, the deterministic review stays and `llm.status = unavailable`.
3. Optional **suggestion** (proactive selling): at most one official substitute that keeps
   the mandate's rules (same `AllergenFilter`) and is greener or more reliable, marked
   `auto_order: false`.

The review is stored in its own table `valio_reviews` (SQLite and Postgres), not in
`order.data`, so it can never overwrite an approval, a cart confirmation or a receipt; the
audit trail gets `actor=valio-agent, event=valio.reviewed`. Approve / reject / cart
confirm / decline / commit run their read-modify-write of `order.data` under
`store.lock`; the review hook runs after the lock is released (the model call never holds
it). Order reads for buyers are unchanged.

| Env | Default | |
|---|---|---|
| `TTA_VALIO_REVIEW` | `async` | `async` (background thread; REST server / UI never wait), `sync` (review before the call returns, a Kimi call adds ~10 s; default of `tta agent` and `scripts/demo_scenarios.py`, whose process would exit before a background review), `off` (forced by `tta bench` and the tests) |
| `TTA_VALIO_LLM_PROVIDER` | `verda` | any provider above; `offline` = template text, no model |
| `TTA_VALIO_LLM_MODEL` | `kimi-k3-1-pro` | |
| `TTA_VALIO_LLM_TIMEOUT` | `60` | seconds per model call |

Valio view (internal, not an MCP tool for buyer agents): `GET /v1/valio/orders?status=&risk=`
(orders with reviews, newest first), `POST /v1/valio/orders/{order_id}/review` (re-run),
`uv run tta valio-review <order_id> [--no-llm] [--json]`.

**Privacy.** With `TTA_VALIO_LLM_PROVIDER=verda` Valio's review runs on an open-weight
model (Kimi K3.1 Pro) on Verda's inference API in the EU: the order data Valio processes
for its own review does not leave the EU. This covers only the Valio review agent; the
buyer's purchasing agent is the buyer's choice and may use other providers (Gemini,
OpenAI, NVIDIA). The review prompt holds only the order lines, totals, the buyer's display
name and the check results, not keys or signatures. Errors never reach the buyer flow (they are logged).

## Standards

We build on open, documented standards and publish our own documents as open schemas
rather than a proprietary format.

| Our document / layer | Standard | How it maps |
|---|---|---|
| Buyer Mandate (`autonomy_mode`, limits, allergens, approvers) | Google **AP2** Intent Mandate | the user's (buyer organisation's) signed intent and constraints for an agent; `autonomy_mode` covers AP2's human-present vs. human-not-present |
| Cart Confirmation | AP2 **Cart Mandate** | the human's signed approval of exact cart contents (`cart_hash`) before anything is ordered |
| — | AP2 Payment Mandate | n/a: payments are out of scope (B2B invoicing on Valio's side) |
| Mandate, Agent Passport | W3C **Verifiable Credentials** 2.0 | VC-shaped: issuer, subject (`buyer_id` / `agent_id`), claims, expiry, signature. Next step: wrap them in a VC envelope (VC-JOSE) without changing the claims |
| Every signature | **JWS** RFC 7515 (compact; detached for the mandate, appendix F) | `typ` per document, e.g. `valio.offer.v1` |
| Algorithm, keys | **EdDSA / Ed25519** RFC 8037, **JWK** thumbprint `kid` RFC 7638 | |
| Key discovery | **JWKS** RFC 7517 at `/.well-known/valio-agent-keys` (RFC 8615 well-known URI) | an agent verifies offers, receipts and passports with nothing else |
| Passport expiry | RFC 7519 `exp` NumericDate | |
| Document shapes | **JSON Schema** draft 2020-12 | `schemas/*.schema.json`, served at `GET /schemas/{name}` |
| Agent interface | **MCP** (Model Context Protocol) | `uv run tta mcp`, any MCP host |
| REST interface | **OpenAPI 3** | `GET /openapi.json`, docs at `/docs` |
| Product data | **GS1** allergen and nutritional-claim codes | as delivered by the mock API |

Schemas (generated from the pydantic models with `uv run tta schemas`; a test checks the
files are current and that real documents produced by the gateway validate):

| Schema | JWS `typ` | Document |
|---|---|---|
| [`buyer-mandate`](schemas/buyer-mandate.schema.json) | `valio.mandate.v1` (detached) | the mandate itself, `signature` included |
| [`signed-offer`](schemas/signed-offer.schema.json) | `valio.offer.v1` | claims of `offer.signature` |
| [`cart-confirmation`](schemas/cart-confirmation.schema.json) | `valio.cart_confirmation.v1` | claims the buyer signs |
| [`order-receipt`](schemas/order-receipt.schema.json) | `valio.receipt.v1` | claims of `order.data.receipt.jws` |
| [`agent-passport`](schemas/agent-passport.schema.json) | `valio.agent_passport.v1` | claims of the passport |

Each schema carries `$id` (`urn:valio-aimo:trade-agents:schema:<name>:v1`) and `x-jws-typ`.

**What is our proposal.** The mock API has prices and availability but no order endpoint.
The order and approval layer (`place_order`, autonomy modes, cart confirmation, approvals,
receipts, the hash-chained audit trail) is our proposal of how agent orders could work.
It sits behind a swappable `OrderBackend` adapter (`service.py`): `MockOrderBackend`
issues order numbers today, a real Valio order API plugs in by implementing `submit`.

## Agent bench

`uv run tta bench` runs 37 fixed buyer requests in frozen synthetic worlds (catalogue,
prices, stock, cut-offs at Fri 13.11 20:00) and checks the gateway's final state
deterministically: status, products, quantity covered, total, allergens, substitution
marks, flagged injections, plus invariants (one order per request, mandate unchanged,
audit chain intact, price / availability asked only for the agent's own candidates). It prints accuracy per category (basic, cut-off, partial stock,
budget, allergens, substitution, preferences, autonomy modes, ambiguity, attacks); model
replies can be recorded so a run repeats exactly. The smoke and core subsets contain
12 and 25 scenarios respectively. Format and checks:
[`bench/README.md`](bench/README.md).

## Assumptions about the mock API

Observed on 13.11 and clarified by Valio Aimo on 14.11.2026 (Tommi, Discord).
Each interpretation is also returned to agents in `offer.assumptions`.

- `availableAmount` ≥ 999 999 means **unknown stock**, usually terminal products not stocked
  by Valio. The mock permits any quantity; this is not verified unlimited stock or a
  delivery guarantee. The explanation is shown in offers, plan lines and the order UI.
- Amount 0 without `availableDate`, absent product records and empty schedule lines
  mean not available (`out_of_stock`), and cannot be ordered.
- `availableDate` is the earliest delivery date and does not follow `requestedDate`.
- `closingTime` is the order cut-off for that delivery; past cut-off means not orderable.
  For Monday 16.11 most `Terminal` products closed on 12.11, `Stock` products close 15.11 17:00.
- Prices are per sales unit. Litres/kg per sales unit = `catalogPrice / comparisonPrice`,
  snapped to a round size and cross-checked with the size in the product name.
- `allowedLotSize` is 0.01 for many boxes; countable units are ordered whole.
- There is no order endpoint in the challenge; Valio confirmed that preparing an order
  for human approval is sufficient. The available webshop is production: do not place
  test orders there. `MockOrderBackend` produces demonstration order numbers.
- Current Valio systems do not enforce approval gates: customer limits are internal
  instructions or manager-only purchasing. Our mandates/limits are proposed agentic
  controls, not existing customer workflows. Human approval is the present target;
  autonomous mode illustrates a possible buyer-authorized future.
- Mock dates remain fixed for the weekend. Cut-offs do not shift automatically.
- A Valio API 429 triggers a 30-second client cooldown (also across subsequent calls
  after exhausted retries). A longer `Retry-After`, seconds or HTTP-date, is honored.
  Retries are bounded to four attempts; 5xx/transport backoff starts at 1/2/4 seconds.
  The policy covers price, availability, ping and streamed downloads; avoid request bursts.
  This is separate from the LLM provider retry policy described above.

## Next steps

- Production authentication and role enforcement for the existing approval/audit UI.
- Recipe-aware substitution (school/hospital kitchens: allergens + nutrition), standing
  order drift detection.
- W3C Verifiable Credential envelope for mandates and passports, key rotation and
  revocation (status list), passport issuance from a bench score; per-buyer customer numbers,
  rate-limit aware batching.

## Research and planned hard cases

See [research notes and primary references](research/README.md) and the
[hard-case / harness-contribution blueprint](research/hard-case-design.md). These distinguish
implemented transaction/text-menu/synthetic-image coverage from proposed complete
yield, inventory, multi-item purchasing and receipt tasks. Demand
forecasting remains future work because no real consumption/attendance history was supplied.

UI testing handoff: [screens, launch commands and checklist](docs/ui-testing.md).
Actual diagnostic model comparison: [curated immutable-run evidence](bench/evidence/README.md).


### Synthetic price/reliability choice demo

`uv run python -m scripts.serve_choice_demo` starts Exchange on http://127.0.0.1:8000
with two ready baskets (EUR270 / EUR298). Open Action inbox, select one or decline both.
No order exists before selection. Restart to reset. This standalone temporary synthetic
world fixes its own process clock, uses no Valio/provider network calls and disables
background model reviews. It is distinct from the live-provider Exchange setup above.
Human selection retains hard policy guards and signed cart confirmation requirements.

Full suite now has42versioned scenarios; smoke/core remain12/25. New human-resolution
cases report scripted human actions separately from the agent's autonomous outcome.

### Dish / weekly menu / photo → reviewed ingredient draft

In Exchange, choose **A dish, recipe or menu image**. Enter the portion count and text,
or attach a JPEG/PNG up to5MB. A configured model creates an editable draft with assumptions
and warnings. Recipe quantities are estimates unless the request supplies trusted recipe,
yield and usable-inventory facts; this is not demand forecasting. Offline rules cannot
interpret menu images or estimate recipes.

Review each ingredient, amount, unit, delivery date and dietary constraint. One explicit chef-review checkbox covers the entire draft. Any edit clears that review.
Each ingredient still has its own **confirm-cart** request; extraction itself creates
no orders and never purchases the entire menu automatically. After edits, review must be
repeated. Requests may partially fail: inspect individual results and existing carts before
retrying. A complete multi-ingredient budget/supply optimization is future work.

Separate text-menu evaluation: [bench/menu/README.md](bench/menu/README.md), including
school makaronilaatikko for1000children and a restaurant's weekly delivery batches.
Text cases score draft arithmetic/review. The separate synthetic-image slice sends
recipe facts only as PNG bytes. Neither proves completed purchasing, clinical diet safety
or robustness to real photographs. See [menu evidence](bench/evidence/menu-20261114/README.md)
for raw grades, lexical oracle corrections and zero-call rescoring.
