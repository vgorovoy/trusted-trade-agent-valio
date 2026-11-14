# Menu text and image evidence —14November2026

These are **separate draft-only development evaluations**, not the42case transaction
suite, completed procurement, nutritional validation or a model leaderboard. All exact
quantities come from synthetic chef-supplied recipe/inventory facts. The underspecified
text case checks explicit assumptions/review only, not recipe accuracy. The image is a
clean synthetic card, not a noisy customer photo.

Each model made3fresh text calls and1fresh image call. Both had100%coverage and zero
provider/parser errors. Each source archive pins inputs/source fingerprints, real reply
tapes, reports and manifests; image archives also contain the actual PNG.

| Model | Text original v1 | Text alias oracle v3 | Image original v1 | Image alias oracle v2 |
| --- | --- | --- | --- | --- |
| Gemini3.5Flash |1/3 |3/3 |1/1 |1/1 |
| Nemotron3.5SuperVL |2/3 |3/3 |0/1 |1/1 |

**The corrected columns are zero-call rescoring of the same saved drafts.** They are
not new model runs or evidence of model improvement. Original machine scores are retained.
Missing vocabulary (`cooking oil`, `dry macaroni`, `raw minced beef` and Finnish equivalents)
caused lexical false negatives despite correct quantities/dates. Neither model demonstrated
a numeric error on these tiny supplied-input cases; this set does not distinguish their
arithmetic ability. Do not present the original0/1 as a demonstrated vision failure.

Intermediate oil-only text-v2 rescoring is also preserved (Gemini3/3, Nemotron2/3).
Final text-v3 and image-v2 revisions extend only aliases: prompts, facts, quantities,
units, dates, image identity and other requirements are unchanged. Wrong quantities
still fail tests. The rescorer verifies source manifests, rejects cross-family/backwards
versions and records source manifest hashes plus original reports.

Archives named `gemini-menu-20261114-v1` and `nemotron-menu-20261114-v1` are the original
text runs. Names including `menu-image-20261114-v1` are original image runs. Suffixes
`alias-rescore-v2/v3` denote regrading, with `new_model_calls=0`. Never overwrite them.

Provider pricing is not configured, so cost is unknown. Original reports record reported
tokens and draft chat invocations; invocation counts do not enumerate SDK retries.
These saved sources also contain lexical limitations deliberately not corrected in place.
Next discriminating tasks need real workflow complexity and exact supported tool contracts,
not a score increase obtained by silently changing expected quantities.
