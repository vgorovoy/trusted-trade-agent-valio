# Synthetic menu and technological recipe cards v1

Three newly written **synthetic, unvalidated** cards: beef/carrot pasta, vegetable
soup, oat/berry porridge. These are not official recipes, nutritional or clinical
advice. Only supplier: Valio Aimo. No real SKU, price, stock or delivery promise.

The kitchen supplies six dish/portion counts across two dates; the LLM does not
forecast attendance. `raw_purchase` coefficients need no correction; `edible_net`
coefficients are divided by the explicit usable yield once. Shared ingredients,
reserved/expired stock and date-dependent receipts create 18 positive net rows,
within the current menu draft limit of 20. Stock precedes all listed receipts.

- `01-three-techcards.json`: executable review-only menu evaluator fixture.
- `inputs/menu.json`: requested service dates and explicit portions per dish.
- `inputs/recipe-cards.json`: authoritative synthetic coefficients and yield basis.
- `inputs/inventory.json`: opening stock, reservations/expiry and receipts.
- `inputs/oracle-ledger.json`: gross/resource consumption/net per ingredient/date.
- `inputs/recipe-cards-missing.json`: missing oat-drink coefficient variant.
- `specifications/02-missing-techcard-clarification.json`: **capability gated**;
  excluded from executable cases and scores until a typed clarification contract
  exists. Neither inventing a ratio nor a generic parser error is a passing clarification.

The model prompt contains the input JSON, not the expected answers. Only the top-level
case JSON is loaded by the evaluator. Input/oracle/specification JSON remains nested.

```bash
python bench/menu/techcards-v1/validate_fixture.py
uv run pytest -q tests/test_bench_techcards.py
uv run python scripts/evaluate_menus.py --cases bench/menu/techcards-v1 --provider gemini --model gemini-3.5-flash
```

The first command is stdlib-only. It uses Decimal arithmetic checked against independent
hand-worked frozen answers, verifies the late Monday receipt remains unused, and rejects
missing quantities/yields. It never rewrites inputs. The third command makes actual
model calls; record every attempt in a new immutable archive. Scripted controls do not
measure live model quality. Outputs are chef-review drafts, not completed purchases,
nutrition assessments or demand forecasts.
