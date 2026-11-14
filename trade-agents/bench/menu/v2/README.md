# Menu draft oracle v2: accepted oil names

These fixtures are a transparent alias-only correction to v1. They add `cooking oil`,
`ruokaöljy` and its unaccented form `ruokaoljy` as accepted names for oil. Prompts,
trusted recipe/inventory facts, quantities, units, dates and other expectations remain
unchanged. Every new case ID points to its original `source_case_id`.

The missing aliases caused false negatives in two Gemini v1 cases despite the correct
quantity/date. Keep the original report and score intact. Regrading its saved drafts is
an **oracle correction with zero new model calls**, not a new model run, held-out result
or evidence of an improved model. Apply the same revised oracle to every compared model.

`menu_rescore.rescore_menu_archive` first verifies the source manifest and checks that
only aliases were extended. It creates a separate finalized archive including original
inputs/report/manifest, the original manifest hash and the new checks. Wrong quantities
still fail; altered source evidence is rejected. Do not overwrite either archive.
