# Image oracle v2: raw beef wording

A lexical-only correction to image-v1 adds exact whole-name forms including `raw minced
beef`, `raw beef mince` and `raaka jauheliha`. It keeps the exact same image path/SHA,
text prompt, recipe/inventory facts, quantities, units, dates, review rules and constraints.
Each case points to its original image-v1 `source_case_id`; image-v1 is unchanged.

Regrading the saved original image drafts, with **zero new model calls**:

| Original image run | Image-v1 oracle | Image-v2 oracle on the exact same draft |
| --- | --- | --- |
| Gemini | 1/1 | 1/1 |
| Nemotron | 0/1 | 1/1 |

Nemotron's original 50 KG raw-beef quantity was correct; its ingredient label/hint was
not recognized by the original oracle. The correction is recorded separately, not
substituted for the failed original score. Both models now pass this one supplied-facts
synthetic image example; this does not distinguish their general vision or arithmetic
ability. The card is not a real customer photograph, and drafts are never orders.

The alias-only rescore helper verifies the original manifest, requires a newer version
in the image suite family, anchors the original manifest SHA and preserves original
reports. It cannot change the PNG, prompt or expected numerical result. No new model
reasoning occurred during rescoring; future harder stress cases require new experiments.
