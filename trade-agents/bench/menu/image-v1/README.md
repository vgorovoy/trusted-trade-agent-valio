# Image-only recipe input, synthetic card v1

`school-recipe.png` is a clearly labelled synthetic recipe/inventory card, rendered from
the exact school text fixture as readable HTML through Playwright. It is not a real
school menu, a customer photo, an official recipe or a test of photographic noise.

This case sends actual PNG bytes with `image/png` through the vision path of `draft_menu`.
The accompanying text asks the model to read the card; it does **not** repeat recipe or
inventory quantities outside the image. The requested guest count and buyer constraints
still appear in ordinary trusted context. The pinned asset SHA-256 is in the fixture;
the archive copies the exact PNG as a hashed artifact.

The deterministic oracle checks the six exact net ingredient quantities and dates,
consolidation and review/mandate boundaries. The synthetic recipe and stock are supplied
facts, not a model's learned recipe or attendance forecast. The result remains an editable
`model_estimate` requiring human review, never a purchase.

The new image-case vocabulary accepts `dry macaroni`/`kuivamakaroni`, matching the card's
wording, and the oil aliases from text v2. Frozen text v1/v2 fixtures and original scores
are untouched. NVIDIA's original text v1 run also used a correct 52 KG quantity but an
unrecognized pasta name; this is a lexical oracle limitation, not a demonstrated numeric
mistake. Do not silently change that old run or call this image case its corrected score.

```bash
uv run python scripts/evaluate_menus.py --cases bench/menu/image-v1 --provider gemini --model gemini-3.5-flash
uv run python scripts/evaluate_menus.py --cases bench/menu/image-v1 --provider nvidia --model nvidia/nemotron-3.5-super-vl-120b-a12b
```

These commands make real model calls; preparing the fixture and scripted tests does not.
Non-vision models produce a visible error with zero scored cases; no text fallback is
scored as vision. Scripted tests verify bytes, MIME, no recipe-text bypass, image hash,
archive contents and quantity-oracle sensitivity. They do not establish a live model's
visual reasoning performance. The main transaction suite is unchanged.

Image replay keys include the SHA-256 of each image and its MIME type. A changed image
cannot reuse an answer to the same text with different pixels. Existing text-only tape
keys remain unchanged; recordings made before this image-key fix need fresh vision replies.
