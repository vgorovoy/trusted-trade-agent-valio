# Public snapshot provenance

This release is a clean file snapshot of the Junction-built project.
Internal source commit: `25983cc51b7bf05961811e5111f4380075265e5b`.
Project files: 267; SHA-256 over the sorted path/content-hash manifest: `7fa27472a823091c7379b85b7609837221c6920674edd42880fa5269c50badb9`.
Prepared: 2026-11-14T13:10:03+00:00.

Only the explicit public allowlist was exported: source, web, scripts, tests, benchmark
fixtures/curated evidence, public research, schemas, selected public docs and project
configuration. Private Git history, briefs, internal PLAN/mentor notes, credentials,
runtime data, keys, generated scratch runs, logs and submission media are excluded.
The public repository's existing root README, MIT license, CI and Git history remain intact.

## Public-view documentation adjustments

- docs/ui-testing.md: replace internal release-workflow introduction with public provenance
- docs/ui-testing.md: remove stale pre-expansion offline baseline expectation
- project .gitignore: ignore generated scratch runs/results and local test caches

## Checks at export

- Known credential prefixes and PEM private-key scan: passed (full exported content).
- Relative Markdown file links: passed.
- Gitleaks: not installed; known-prefix scan used, not a claim of a Gitleaks pass.
- Isolated public-copy validation: Ruff passed; pytest424passed,13PostgreSQL integration
  tests skipped without a server. Two Google SDK deprecation warnings were reported.
- Offline signed-protocol demo passed (mandate/offer, cart confirmation, approval,
  receipt, audit chain and school allergen blocks); smoke benchmark12/12, full coverage,
  zero live model calls and zero violations in its limited safety-check scope.
- All16curated evidence manifests verified after export. No provider keys or Aiven
  connection were used for these checks. Publication still requires a reviewed diff
  and the public repository's own CI after an ordinary snapshot commit.

The ordinary public snapshot commit is separate from the internal development history;
no force-push or private-history transfer is used by the exporter.
