# ADR-037: Second-Pass LLM Output Review (Sprint 21 follow-up)

**Status:** Accepted
**Date:** 2026-08-23
**Sprint:** 21 follow-up (post-TCC product roadmap)

## Context

ADR-026 (Sprint 21) deliberately shipped deterministic/statistical checks only
(`core/output_validation.py`) and explicitly deferred a second LLM pass reviewing
`gold_df`/`predictions_df`/`narrative` against the original business question — real cost/
latency doubling for a failure class (a result that's internally consistent, numerically
plausible, but answers a subtly different question than what was asked) the deterministic
checks can't name in advance. That ADR's own "Alternatives considered and rejected" section
left the door open explicitly: *"a real, distinct next increment, not a blocker."*

Two things changed since then, both from this session's live 6-model comparison (PR #109):
a live, credentialed multi-provider LLM setup now exists to actually exercise this (previous
attempts at LLM-dependent features in this project were repeatedly blocked on "no
`OPENAI_API_KEY` available", e.g. Sprint 28's baseline), and the owner explicitly asked to
build this now rather than continue deferring it.

## Decision 1 — Additive second check, opt-in via one env var, not a config surface

`agents/analysis/reviewer.py` adds `review_gold_result`/`review_science_result`: one extra
`get_llm().invoke()` call per successful Gold/Science sub-task, asking the model whether the
narrative/result actually answers the original business question, given a **compact** preview
of the result (`narrative`, `gold_df.head()`/`.describe()` or `predictions_df.head()` plus
`model_info`, never the full DataFrame — same truncation posture as every other agent prompt,
ADR-012). Returns one `OutputSanityCheckEntry` (`check: "llm_review"`) appended onto the
existing `OutputSanityCheck.checks` list that `check_gold_output`/`check_science_output`
already produce — reusing the exact extension point ADR-026 reserved for this, not a parallel
mechanism. `core/output_validation.py` gains one small public function, `append_check`, to
recombine an appended entry's severity/summary — the smallest change that keeps that module's
existing `_summarize` logic as the single source of truth for how `severity`/`summary` are
derived.

**Opt-in via `AI_ETL_LLM_REVIEW_ENABLED` (default: unset/false), not per-pipeline, not a new
API endpoint.** Considered a per-`saved_pipeline` toggle (mirroring Sprint 27's
`require_approval`) — rejected for now: this is a global cost/quality trade-off applying
equally to every run on a deployment, not a per-pipeline policy choice like approval gating
or a notification destination. A deployment operator who wants this on pays roughly double
Gold/Science LLM cost per sub-task (one extra `.invoke()` alongside the existing up-to-3
code-generation attempts) — same one-env-var-first posture `AI_ETL_LLM_PROVIDER`/
`AI_ETL_LLM_MODEL` already established, and the same "measure before optimizing" reasoning
ADR-013 used elsewhere: ship the simplest on/off switch, revisit a per-pipeline surface only
if real usage shows deployments want to mix both policies at once. No migration, no new API
route — this is entirely a `services/pipeline_service.py`-layer wiring decision, same "no new
persisted state" posture ADR-026 itself used.

## Decision 2 — Never blocks, never escalates severity beyond `warning`

Same posture as ADR-026 Decision 1, unchanged: an `llm_review` finding is `severity: "warning"`
only, appended into the same list a deterministic check would have populated. A result already
returned to the user after up to 3 code-generation retries is not withheld by a 4th signal with
no further retry path — this ADR doesn't relitigate that call, it only adds one more entry that
can populate the list ADR-026 already built for exactly this.

## Decision 3 — Never raises; a review failure degrades to "no entry", not a crash

`review_gold_result`/`review_science_result` catch every exception internally (LLM call
failure, malformed JSON response) and return `None` rather than propagate — mirroring
`core/llm.py::extract_token_usage`'s own "cost tracking degrading to zero is preferable to
crashing" posture, and `core/output_validation.py`'s existing convention of skipping a
non-applicable check rather than emitting a false `"ok"`. A skipped review is different from a
review that ran and found nothing — same distinction ADR-026 already draws for its own
deterministic checks. Malformed JSON is parsed via `agents/_llm_codegen.py::strip_code_fences`
first, the same shared helper PR #109 already fixed to cover every other agent's
markdown-fence-wrapped JSON/code responses.

## Consequences

- Closes the real gap ADR-026 flagged and explicitly deferred, using the exact extension point
  that ADR reserved (`checks` list, `OutputSanityCheck` shape unchanged) — no breaking change to
  the frontend (`analysis-section.tsx` already renders every non-`"ok"` `checks` entry
  generically by `detail` text, confirmed no frontend change is needed for this ADR).
- Cost is opt-in and roughly doubles per sub-task when enabled — deliberately not the default,
  consistent with this project's repeated "ship the cheap default first, add the expensive tier
  behind an explicit switch" pattern (Sprint 28's manual-`workflow_dispatch`-only regression
  harness is the closest precedent).
- A per-pipeline (not just global) review toggle, and folding this into the pre-run cost
  estimate (`core/cost_estimation.py`, Sprint 35 — today's heuristic doesn't account for an
  optional extra LLM call), are both real, deferred follow-ups, not silently out of scope.

## Related

- ADR-026 — the deterministic checks this ADR adds a second, additive layer alongside, and the
  ADR whose own "Alternatives considered" section this decision directly resolves.
- ADR-012 — the schema/sample truncation posture this ADR's review prompt reuses.
- ADR-013 — the "measure before optimizing" / simplest-switch-first reasoning behind the
  single global env var instead of a per-pipeline toggle.
- PR #109 — the live, credentialed multi-provider LLM setup that made testing this for real
  possible, and the `strip_code_fences` fix this ADR's JSON parsing reuses.
