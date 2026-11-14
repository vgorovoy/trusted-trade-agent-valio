# Menu-draft evaluation, separate from transaction benchmarking

Three versioned **text-only** cases exercise the actual `draft_menu` extraction pipeline:

- Milk-free makaronilaatikko for 1,000 school children, with an explicitly supplied
  synthetic chef recipe and usable inventory. Six exact net ingredient quantities.
- The same request without recipe/inventory norms. Score recognizable ingredient coverage,
  explicit assumptions/missing facts and mandatory human review; **do not score numeric
  recipe accuracy** when no trusted norm was supplied.
- A restaurant's four services across two delivery batches. Seven exact net needs, including
  repeated oat drink on different dates and inventory applied only once in time order.

Numbers are synthetic test inputs, not recommended portions or an official Valio recipe.
Both exact cases explicitly supply all facts in the request, including relevant stock
expiry/reservation. This evaluates arithmetic and requirement extraction, not learned
attendance prediction. A separate [synthetic image slice](image-v1/README.md) now exercises real PNG-byte input;
it is not included in these three text cases and has its own suite version.

`menu_eval.py` validates ingredient/date/unit/quantity coverage, consolidation and the
review/mandate boundary. All results remain `draft`, `requires_review`, `requires_approval`,
`source=model_estimate`. A scored draft is **not** evidence of a complete purchased basket,
recipe suitability, nutrition compliance or allergen-safe product selection. No purchase
executor, catalog or Gateway is supplied to this evaluator.

Run only with an explicitly selected configured provider/model:

```bash
uv run python scripts/evaluate_menus.py --provider gemini --model gemini-3.5-flash
uv run python scripts/evaluate_menus.py --provider nvidia --model nvidia/nemotron-3.5-super-vl-120b-a12b
```

These commands make real model calls; preparing the fixtures and running the tests did
not. A unique finalized archive under ignored `bench/runs/menu/` contains pinned inputs,
source fingerprints, actual reply recordings, per-case results and a manifest. Errors
remain in attempted/coverage/pass-fraction denominators, with sensitive diagnostics
omitted. Cost is unknown; reported tokens and draft chat invocations do not enumerate
provider SDK retries. Reusing an archive ID fails rather than overwriting an old attempt.

`tests/test_bench_menu.py` uses explicitly named scripted replies as positive and negative
oracle controls, not as a claim that a live model passes. It checks gross/net mistakes,
merging delivery dates, reusing inventory, ignored reservations, missing assumptions,
wrong tool actions, model-initialization errors, archives and the absence of a purchasing
execution path. The main 42-case transaction suite is unchanged by this separate slice.
