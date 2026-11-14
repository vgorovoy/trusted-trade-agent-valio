# Text oracle v3: explicit recipe vocabulary

This final lexical correction extends accepted whole ingredient names for the forms
already present in the trusted recipe: `dry macaroni`, `raw minced beef`, and legitimate
Finnish dry/raw variants such as `kuivamakaroni` and `raaka jauheliha`. It also retains
the oil aliases introduced in v2. Matching remains exact after case/Unicode/whitespace
normalization; no loose substring matching or numerical tolerance change was introduced.

Every case points to its **original v1** `source_case_id`. Prompts, norms, usable inventory,
quantities, units, delivery dates, required review and dietary checks are unchanged.
Original text v1/v2 fixtures and every finalized archive remain intact.

Regrading saved original drafts gives the following, with **zero new model calls**:

| Original model run | Original text-v1 oracle | Text-v3 oracle on the exact same drafts |
| --- | --- | --- |
| Gemini | 1/3 | 3/3 |
| Nemotron | 2/3 | 3/3 |

This corrects false-negative ingredient-name recognition; it is not a model improvement,
new trial or held-out score. Two text cases check supplied recipe/inventory arithmetic;
the underspecified case checks review protocol rather than numeric recipe accuracy.
The tiny set does not differentiate the models' numerical ability. Both remain untrusted,
review-required draft generators; no purchased basket or recipe safety is established.
Harder compound/stress cases remain planned work, not measured evidence of failures.

`menu_rescore` accepts only strictly newer versions in the same text or image suite
family, and only alias supersets. Changes to prompts, quantities, dates, images, ingredient
rows or other requirements are rejected. New archives retain original manifests/hashes,
reports and the original scores. Wrong quantities still fail the unchanged quantity oracle.
