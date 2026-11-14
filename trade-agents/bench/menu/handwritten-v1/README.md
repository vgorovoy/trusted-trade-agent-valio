# Synthetic handwritten kitchen note: extraction quality v1

`kitchen-note.png` is an **AI-generated synthetic handwritten-style image**, not a real
chef's photograph or a representative sample of real handwriting. The prompt is retained
in `generation-prompt.txt`. The fixture's visible text was manually checked against the
reference below, including the synthetic-fixture label. PNG SHA-256 is pinned in JSON.

| Visible ingredient | Reference quantity | Normalized unit | Delivery date |
| --- | ---: | --- | --- |
| Makaronit | 10 | KG | 2026-11-16 |
| Naudan jauheliha | 5 | KG | 2026-11-16 |
| Kaurajuoma | 20 | L | 2026-11-16 |
| Sipuli | 2 | KG | 2026-11-16 |
| Kananmunat | 60 | ST (pieces / kpl) | 2026-11-16 |
| Rypsiöljy | 2 | L | 2026-11-16 |

The image also says `Maidoton keittiö` (milk-free kitchen). The evaluation's trusted buyer
profile independently carries a milk/trace exclusion; preserving this profile does not
prove the model read the dietary note or that chosen products would be allergen-safe.
No product selection or purchase occurs. The companion prompt does not repeat ingredient,
quantity or date facts outside the image. Guest count 1 is deliberately neutral: this is
an already quantified shopping list, not a recipe to scale.

## Interpreting measured quality

Per-attempt `line_metrics` and the run's micro-aggregated `quality` report:

- **Ingredient-line precision:** one-to-one matched reference ingredient lines divided by
  predicted lines; extras and duplicates lower it. No predicted rows means 0, not 1.
- **Ingredient-line recall:** matched ingredient lines divided by all reference lines;
  omissions and failed/no-op attempts reduce recall. Identity matching precedes fields,
  so a wrong unit is recorded as a unit error, not hidden as a lexical omission.
- **Exact amount+unit+date:** each reference line must have all three fields correct to
  count. Divide by all expected lines, including omissions. This prevents an ingredient
  name alone from appearing as a fully correct purchasing requirement.
- **Quantity/unit error lines:** matched rows with a wrong numeric quantity or unit.
  These are called critical transcription-field errors; date errors, omissions, extras
  and ambiguous identities are separately visible. Zero measured quantity/unit errors
  with six omissions is no success, and no value here establishes food safety.
- **Review boundary:** the actual draft must remain `draft`, `model_estimate`, requiring
  review and approval. The model's optional review-caveat keyword signal excludes the
  harness's automatic warning; it is explicitly not semantic understanding or consent.

Aliases are explicit whole Finnish/English names, matched after case, Unicode and
whitespace normalization. There is no fuzzy substring judge. Recognized contradictory
name/search-hint identities are ambiguous rather than counted correct. Repeated
ingredients across dates use one-to-one field-aware assignment. These rules remain
reference-dependent; a matching name is not proof that every possible synonym or
culinary distinction is understood.
The beef line requires a species-specific name: generic Finnish `jauheliha`
(minced meat) is insufficient to transcribe the visible `Naudan jauheliha` faithfully.

```bash
uv run python scripts/evaluate_menus.py --cases bench/menu/handwritten-v1 --provider gemini --model gemini-3.5-flash
uv run python scripts/evaluate_menus.py --cases bench/menu/handwritten-v1 --provider nvidia --model nvidia/nemotron-3.5-super-vl-120b-a12b
```

Those commands make real model calls. Fixture/oracle tests use labelled scripted controls;
no live model quality score was produced while implementing this slice. Real trials need
separate finalized archives, actual model/token/error coverage and the unchanged PNG hash.
One legible synthetic note cannot establish general handwriting/OCR performance. Real
photos, ambiguous digits, damaged paper and varied chef handwriting require separate,
predeclared cases. All previous suite versions and reports remain unchanged.
