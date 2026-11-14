# Empirical stress probes: held approval becomes stale

Executed 14 November 2026 against source commit
`fe12ff004dc77da7d99ecbedc15c6f203e4e7a28`, before any remediation.
These are deterministic synthetic gateway experiments, not live model trials or newly
admitted benchmark scores. No production orders, supplier API calls or provider calls
were made. `PYTHON_DOTENV_DISABLED=1` and `TTA_VALIO_REVIEW=off` were set explicitly;
`World` supplied mocked HTTP, temporary SQLite/signing keys and frozen time.

## Observed P1: held approval commits invalid delivery

Both probes used a signed hotel mandate, autonomous passport, named approver, and
1,000 L milk at EUR1.40/L. The autonomous spend limit was EUR1,000 and hard limit
EUR2,000. Initial submission at 09:59 Helsinki produced `pending_approval`, EUR1,400,
no backend submission, quote expiry 10:59 and hold expiry the next day at 09:59.
Requested delivery was 16 November; supplier cutoff was 12 November at 10:00.

| Probe | Change before human approval | Actual result | Fixed oracle |
|---|---|---|---|
| S01 cutoff wait | Advance clock to 12 November 10:01; stock remains 1,000 | `placed`, order number, signed receipt, one backend submission | No commitment for original delivery after cutoff; explain need for fresh date/decision |
| Stock-drop subcase | At 09:59:30, stock becomes zero; explicitly force-refresh availability to zero before approval | `placed`, order number, signed receipt, one backend submission | No commitment for unavailable quantity; explain shortage and request a fresh decision |

Neither failure is explained by expired quote/hold, an invalid signature, insufficient
hard limit, stale availability cache, or a model choosing the wrong product. The stock
probe deliberately refreshed the client cache before approval. Audit chains verified
successfully in both experiments: cryptographic integrity does not establish that the
business action was valid.

A repeated approval callback raised `GatewayError` because the order was already
`placed`; backend submissions remained one. Duplicate commitment was **not** observed.
Signed cart-confirmation and network-failure variants have **not** been executed here.

## Source inspection versus execution

Inspection identified the hypothesis: `Gateway._approve` calls `_held`, records the
approval, then `_commit`; `_held` checks expected status, approver and hold expiry,
while `_commit` submits the stored order. This path does not refresh/reassess delivery
or stock after the human wait. The table above records actual execution, not an
inference from that inspection.

Fix direction, not yet implemented by this probe: immediately before committing a held
order, revalidate current availability and delivery cutoff against trusted time, preserve
the locked price and required human authority, and block with an explicit reason when
the original basket can no longer be fulfilled. Test approvals and signed confirmations
separately. Do not silently reschedule under the old human decision.

## Minimal reproduction

From `trade-agents/` at the source commit above:

```bash
PYTHON_DOTENV_DISABLED=1 TTA_VALIO_REVIEW=off uv run python - <<'PY'
from datetime import datetime
from trade_agents.bench.scenario import Scenario, load_world
from trade_agents.bench.world import World, frozen
from trade_agents.service import OrderLineIn, GatewayError

for kind in ('cutoff', 'stock'):
    spec = load_world('dairy', {
        'now': '2026-11-12T09:59:00+02:00',
        'cutoff': '2026-11-12T10:00:00+0200',
        'products': {'1001': {'price': 1.4, 'comparison': 1.4, 'stock': 1000}},
    })
    scenario = Scenario(
        id='stress-probe-' + kind, category='probe', title=kind,
        buyer='hotel', request='1000 L milk Monday', world=spec, expect={},
        mandate={'auto_order_limit_eur': 1000, 'hard_limit_eur': 2000},
    )
    with World(scenario) as world:
        profile, gateway = world.profile(), world.gateway
        submissions, submit = [], gateway.backend.submit
        def counted(order):
            submissions.append(order['order_id'])
            return submit(order)
        gateway.backend.submit = counted
        offer = gateway.quote(['1001'], spec.delivery)[0]
        order = gateway.place_order(
            profile.mandate,
            [OrderLineIn(offer_id=offer['offer_id'], quantity=1000)],
            'stress', 'synthetic probe', agent_passport=profile.passport,
        )
        assert order['status'] == 'pending_approval' and not submissions
        if kind == 'stock':
            world.spec.products['1001']['stock'] = 0
        at = ('2026-11-12T10:01:00+02:00' if kind == 'cutoff'
              else '2026-11-12T09:59:30+02:00')
        with frozen(datetime.fromisoformat(at), 'approval-clock'):
            fresh = gateway.client.availability(
                ['1001'], spec.delivery, force_refresh=True)['1001']
            approved = gateway.approve(order['order_id'], profile.mandate.approvers[0])
            print(kind, fresh, approved['status'], approved['order_number'],
                  bool(approved['data'].get('receipt')), len(submissions))
            try:
                gateway.approve(order['order_id'], profile.mandate.approvers[0])
            except GatewayError as error:
                print('repeat:', error)
            assert len(submissions) == 1
PY
```

The actual run used the installed `.venv/bin/python` directly, avoiding dependency
downloads. The snippet uses `uv run` for the project's normal documented invocation.

## Remediation after these probes

The observations above remain the pre-fix record. Subsequent remediation now revalidates
held baskets immediately before commitment: fresh stock (including finite quantity for
an already-approved late delivery), cut-off, changed delivery, mandate/hold expiry and
network-latency boundaries. Unverified availability blocks rather than using stale data.
The held24hourprice is preserved; there is no silent repricing or rescheduling.

The initial order path also fails closed with a sanitized error before any order row if
current availability cannot be verified; raw provider diagnostics are not stored.
Regression tests cover approval and buyer-signed confirmation, late/partial stock,
unknown-terminal-stock controls, expiry, provider errors, duplicate commitment and all
initial autonomy modes. New audit events are `order.revalidated` / `order.commit_blocked`.

Validation after remediation:424passed,13PostgreSQLintegration tests skipped without
server; global Ruff clean. Independent Astra review verified58focused cases, including
the original failures. These are gateway regression experiments, not extra live model
successes or newly admitted42case benchmark scores.
