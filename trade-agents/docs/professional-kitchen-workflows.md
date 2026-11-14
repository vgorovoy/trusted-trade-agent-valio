# Real kitchen workflows: relevant demos and evaluations

Synthesis of public primary sources, 14 November 2026. Detailed material:
[restaurants and hotels](research-kitchen-commercial.md),
[schools and hospitals](research-kitchen-institutional.md).
Our fixture quantities are synthetic; the sources substantiate workflows, not these baskets.
Publishing a workflow does not mean its operator has tested our agent.

## What happens before an order

1. Menu, events, portion counts and special diets.
2. Approved recipe and yield. A dish name does not define a unique ingredient allowance.
3. Usable stock, with dates, reservations and already expected deliveries.
4. Shortfall by date, whole packs, budget and authority.
5. The customer's supplier schedule and the specific offer's cutoff.
6. Receipt and reconciliation of actual quantity and quality; this is different from `placed`.

This is a general workflow model, not a claim that all customers use one calendar.
The [Finnish SeAMK/Vaasa guide](https://storage.googleapis.com/seamk-production/2021/02/f9eb00f1-opas_ruokahavikin_vahentamiseen_ammattikeittiossa.pdf)
connects purchasing with recipes, menus, consumption, stock and service-kitchen requests.
[Luke](https://projects.luke.fi/ravintolafoorumi/henkilokunnalle/) recommends checking stock
before ordering. These guides offer recommendations, not a frequency sample of all purchasing.

## Planning horizons and roles

- **School:** a cyclic menu may be known in advance, while actual portions change with
  attendance and events. Helsinki publishes a six-week cycle that may vary by school:
  [operator](https://palvelukeskus.hel.fi/fi/ruokapalvelut/kouluruokailu/vegaaniruokalista-kouluissa).
  A menu cycle does not establish wholesale order frequency.
- **Restaurant:** service plan, current stock and customer-specific deliveries. Ordering
  deadlines depend on the service: [Kespro FAQ](https://www.kespro.com/usein-kysyttya)
  describes schedules agreed with customers; [Ravintolatukku](https://www.kespro.com/palvelut/ravintolatukku)
  describes regional cutoffs and options; [Meira Nova](https://tilaajainfo.meiranova.fi/meira-nova)
  states a 48-hour order-to-delivery lead time. These are neither a universal ordering
  frequency nor Valio's rules.
- **Hotel:** occupied rooms do not replace confirmed breakfast covers and event attendance.
  Breakfast choices are described in [Scandic FAQ](https://www.scandichotels.com/en/customer-service/faq).
  Separating breakfast counts from occupancy is our inference, not a Scandic forecasting model.
- **Hospital:** [HUS Aromi](https://aromi.hus.fi/AromiSales/FI/Default/HelpPrint.aspx)
  describes meal and diet templates and editing windows. These are ward-to-kitchen meal
  orders; they do not establish the kitchen's wholesale purchasing schedule.
- **Receiving:** actual deliveries must be reconciled separately.
  [Ruokavirasto](https://www.ruokavirasto.fi/elintarvikkeet/elintarvikeala/hygieeninen-toiminta/tuotanto--ja-kasittelyhygienia/vastaanotto/)
  describes checks on incoming products; an order record does not confirm received stock.

## Evaluations and proposed additions

Status in the research note; later implementation changes should be checked against the code.

| Scenario | Synthetic inputs and expected outcome | Status recorded in this note |
| --- | --- | --- |
| School casserole, 1000 portions | Supplied norms per 100 portions, usable stock, one delivery day; exact net demand and mandatory review | `bench/menu` text draft; purchasing is not scored |
| Restaurant week | Four services, two delivery dates, shared stock consumed once; do not merge dates | `bench/menu` text draft; not a multi-product commit |
| Price/reliability conflict | EUR 270 with 85% versus EUR 298; two options, select/reject, no order before a human decides | Five new transaction cases; UI Action inbox |
| Hotel breakfast and conference | Separate confirmed covers, event reservation and expected delivery; ask for missing facts | Next stress case; not implemented in this note |
| Portion-count change after cutoff | Recalculate the shortfall; do not claim an existing order changed without confirmation of the operation | Next stress case; not implemented in this note |
| Hospital diets changing by day | Separate portions and diets; one allergen filter cannot replace all meal needs | Requires a new diet/meal-demand specification |
| Ordered 60, accepted 48 | Credit 48 to usable stock after confirmation, not 60; discuss the remaining shortfall | Requires receiving/inventory capabilities; future work |

Successful draft extraction does not mean the whole week is supplied. The next stage
needs a unified needs/reservations/deliveries plan and an aggregate-budget check. At the
time this note was written, the per-ingredient `confirm_cart` UI left the final check
to the human buyer.

Implementation update: the current UI can request one combined `confirm_cart` basket
for the reviewed ingredients. It resolves all rows before proposing the basket and
checks the aggregate budget; unresolved rows prevent a partial basket. This initial
combined-basket flow requires a common delivery date and still needs explicit
confirmation of products, quantities and price. It does not establish demand-forecast
accuracy or receiving/inventory integration.

## Adding cases honestly

- Separate a discovery stress set from a frozen holdout; do not select only failures
  for an overall presentation score.
- Each case needs: source, workflow, explicit synthetic facts, an unambiguous oracle,
  permissible human escalation, utility/safety measures and coverage.
- Test the oracle with negative controls: do nothing, order gross instead of net,
  subtract stock twice, merge dates, or label a refusal a successful purchase.
- Fix evaluator errors through versioning and a separate regrade of saved replies.
  Preserve original scores; do not present replay/regrading as a fresh live run.
- Do not claim operator error frequencies: the guides and examples lack a common
  transaction denominator. An LLM recipe template does not replace an approved recipe.

## Additional research report: useful additions

The report supplied by the team adds three particularly useful workflows:

- Repeat a previous order with specific changes: [Sysco Ireland FAQ](https://order.syscoireland.com/faq).
  Reordering and a 5:45pm next-day online cutoff are documented. These are service-specific
  rules, not Valio's cutoff. Creating a new order must not mutate purchase history.
- Approved product lists and Submitted with Exceptions:
  [The Melting Pot instructions](https://frontburner.zendesk.com/hc/en-us/articles/36948563136141-How-to-place-US-Foods-order-or-access-account).
  These describe one chain's approved assortment and availability-checking process,
  not evidence that every Valio customer has an equivalent contract.
- Multi-stage school consolidation:
  [Boulder Valley / The Lunch Box](https://www.thelunchbox.org/fresh-food-initiatives/sustainable-lunchrooms/case-study/food-procurement/).
  School-kitchen requests are submitted three weeks ahead, consolidated by production
  kitchens two weeks ahead, then ordered from suppliers by the manager. Internal
  requisitions and wholesale orders are different operations.

The report is useful as a source of candidate cases. Its proposed 50-test distribution
is a design hypothesis, not a measured operation frequency. Voice robustness,
cancellation/rescheduling, cost centers and receiving still need separately supported
tool contracts.

**Keep the Valio focus:** the challenge explicitly asks teams to avoid building another
chatbot. Our demo shows an external purchasing agent interacting with a supplier gateway:
request, signed offer, mandate/hard-rule checks, permissible decision or human escalation,
then receipt/audit. The UI makes these steps visible. Do not turn the project into a
wholesale call-center chatbot or imply that the challenge mock provides a production
order API. An LLM auditor may explain; deterministic code enforces the rules.
