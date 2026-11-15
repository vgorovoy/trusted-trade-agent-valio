# Public snapshot provenance

This release is a clean file snapshot of the Junction-built project.
Internal source commit: `023a949b0f010714387c3ddb39ed9ef595056655`.
Project files: 319; SHA-256 over the sorted path/content-hash manifest: `a81dd67c8ae6d7b91609e76a982cc7c6b5d568641ecc7adf2c11e0b2eed84cb2`.
Prepared: 2026-11-15T07:12:34+00:00.

Only the explicit public allowlist was exported: source, web, scripts, tests, benchmark
fixtures/curated evidence, public research, schemas, selected public docs and project
configuration. Private Git history, briefs, internal PLAN/mentor notes, credentials,
runtime data, keys, generated scratch runs, logs and submission media are excluded.
The public repository's existing root README, MIT license, CI and Git history remain intact.

## Public-view documentation adjustments

- docs/professional-kitchen-workflows.md: reviewed English public translation; internal original unchanged
- docs/research-kitchen-commercial.md: reviewed English public translation; internal original unchanged
- docs/ui-testing.md: replace internal release-workflow introduction with public provenance
- docs/ui-testing.md: remove stale pre-expansion offline baseline expectation
- tests/test_bench_custdev.py: explicit missing-private-fixture skip; tests retained, not counted as passes
- tests/test_bench_handwritten_vitaly.py: explicit missing-private-fixture skip; tests retained, not counted as passes
- tests/test_bench_restaurant_note.py: explicit missing-private-fixture skip; tests retained, not counted as passes
- project .gitignore: ignore generated scratch runs/results and local test caches

## Checks at export

- Known credential prefixes and PEM private-key scan: passed (full exported content).
- Cyrillic filenames and UTF-8 text gate: passed; source text is not silently dropped.
- Internal photos and restricted fixture families: excluded by explicit policy.
- Relative Markdown file links: passed.
- Gitleaks: passed on complete staged export.
- Runtime tests/offline demo: run separately after this export; do not infer completion
  from preparation of this file. Publication requires those checks and a reviewed diff.

The ordinary public snapshot commit is separate from the internal development history;
no force-push or private-history transfer is used by the exporter.
