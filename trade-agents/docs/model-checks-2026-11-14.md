# Model access checks — 14 November 2026

AGY's development-agent quota and the project's provider API quotas are separate.
The earlier report that Gemini API tokens had run out was not verified. Direct checks
now confirm both provider paths work; neither check proves remaining quota or future uptime.

## NVIDIA Nemotron: live agent scenario

Model: `nvidia/nemotron-3.5-super-vl-120b-a12b`, NVIDIA Build endpoint.
The actual buyer agent completed `basic-milk` in the synthetic frozen benchmark world:
**1/1 scenario passed**, no skipped scenarios, final status `placed`, all checks passed.
Four live model calls consumed 17,302 reported tokens. No production Valio API/order calls.
Cost is unknown because provider pricing is not configured. This is one text scenario,
not a full benchmark score or a validation of image/menu understanding.

The real response tape for `basic-milk` was refreshed from this run. Other tapes may
still mismatch current prompts/tool results; do not report skipped runs as complete.
This recording may be replayed without model calls. It is not a newly scored model on replay.

NVIDIA challenge narrative: Nemotron reasons over a kitchen's purchasing request and
uses gateway tools to select candidates, obtain offers, propose an order and report the
outcome. Deterministic signed mandates, allergen rules and spend checks bound its actions.
Grounded tool results, structured calls, a constrained agent prompt and deterministic
benchmark checks are the mechanisms used to improve output quality. The next demonstration
should include budget escalation and a menu image if claiming visual capabilities.

## Gemini: direct availability check

Model: `gemini-3.5-flash`. A direct google-genai API request, without AGY, returned `OK`.
Reported usage: **8 input tokens and 1 output token**. No buyer-agent tools were exercised
by this check. Therefore Gemini API is currently accessible; this is not an end-to-end
agent evaluation and does not establish the size of the remaining allowance.

Use explicit provider and model flags when comparing; a shared TTA_LLM_MODEL override
must not accidentally send the NVIDIA model id to the Gemini API or vice versa.

## Submission status

The offline protocol demonstration remains available regardless of provider quotas.
Choose the primary model based on a verified end-to-end demo; do not imply that scripted
or cached responses are live model reasoning. Both adapters remain supported.

## Later diagnostic comparison (same day)

Both models were subsequently evaluated live on the same four known offline-failure cases:
Gemini 2/4 and Nemotron 2/4, both100%coverage/zero skips. Each used22provider calls;
reported tokens84,785 and90,554 respectively. These four selected development cases are
not a representative full-suite estimate. See [curated evidence](../bench/evidence/README.md).
Both passed butter/clean beef, escalated the unreliable milk and were blocked on school
milk traces. The last two remain utility failures despite safe enforcement.

## Explicit-constraints harness revision

A fresh v2 diagnostic run after exposing trace rules/reliability to the model produced
3/4 for both Gemini and Nemotron (100% coverage, no skips). Both now decline the unsafe
school request before attempting an order. The reliability case remains a failure and
exposes ambiguous premium-baseline precedence; see the preserved
[v1/v2 evidence and limitations](../bench/evidence/README.md).

## Menu text and clean-image draft evaluations

Both providers completed3live text-draft cases and1live synthetic-image case with no
provider/parser errors. Original machine grades had lexical false negatives despite
correct quantities/dates. Corrected alias-only oracles applied to the exact saved drafts
produce3/3 text and1/1 image for both, with zero new calls. All raw grades and intermediate
corrections remain preserved. This small set does not distinguish numeric ability, prove
real-photo robustness or score completed procurement. See [detailed evidence and boundaries](../bench/evidence/menu-20261114/README.md).
