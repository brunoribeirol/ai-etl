# ADR-026: Output Sanity-Check (Gold/Science Result Validation)

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 21 (post-TCC product roadmap)

## Context

`core/sandbox.py` (ADR-007) guarantees the LLM-generated Analyst/Science code *ran* without
raising — it says nothing about whether the *logic* is correct. Sprint 16 (ADR-023) validated
the quality of the *input* (Silver `df`, before Gold/Science ever runs). Nothing today checks
the *output* — `gold_df`/`predictions_df` can be internally consistent (right dtypes, right
shape, `fig`/`narrative` present) and still be wrong: a `groupby().sum()` that double-counts a
join key, a forecast that extrapolates three orders of magnitude past any historical value,
an `r2` the sandboxed code computed against the wrong column. `science.py` already has one
narrow example of this exact problem class fixed ad hoc —
`_validate_narrative_consistency()` — which only checks that the *narrative's* stated direction
matches `predictions_df`'s own trend, and only for `diagnostic`/`forecast` tasks. Roadmap Sprint
21 generalizes this: sanity-check the *result itself* (`gold_df`/`predictions_df`/`model_info`),
not just whether the narrative contradicts it.

**Definition of done:** a deliberately wrong result (injected in a test) is flagged before it
reaches the user as "trustworthy" with no caveat.

## Decision 1 — Deterministic statistical checks, no second LLM pass (for this sprint)

The roadmap explicitly frames this as a trade-off to resolve, not a given: a second LLM pass
reviewing the result (more expensive, more robust, catches semantic errors a fixed rule can't
name in advance) vs. deterministic/statistical checks over `gold_df`/`predictions_df` (cheaper,
covers fewer cases, but zero added latency/cost and fully reproducible).

**Chosen: deterministic checks only, mirroring `agents/quality.py`'s existing precedent exactly**
— same "whitelisted, declarative, no `exec()`/`eval()` of untrusted data" posture ADR-023 already
established for custom quality rules, applied to the output side instead of the input side.
New pure-function module `core/output_validation.py` (mirrors `core/drift.py`'s own "pure
functions, no I/O" shape): `check_gold_output(gold_df, silver_df)` and
`check_science_output(predictions_df, model_info, silver_df)`, each returning an
`OutputSanityCheck` TypedDict — `{"checks": [...], "severity": "ok"|"warning"|"error",
"summary": str}` — the same three-key shape `quality_report` already has, so both the JSON
persistence layer and the frontend badge/renderer reuse an established pattern instead of
inventing a new one.

**Checks implemented (`core/output_validation.py`):**

Gold (`check_gold_output`):
1. `sum_conservation` — for every numeric column `gold_df` and `silver_df` share by name,
   `sum(gold_df[col])` must not exceed `sum(silver_df[col])` by more than a small float-rounding
   tolerance (0.5%). An aggregation/filter over `silver_df` (Analyst's own prompt contract —
   "Group, filter, sort, or aggregate") can only ever produce a sum less than or equal to the
   total it was derived from; a sum that *exceeds* the source total means the generated code
   double-counted, joined wrong, or fabricated rows. This is the check that catches the roadmap's
   own worked example ("a soma bate com o total esperado do Silver?") and the one the injected
   wrong-result test below exercises directly.
2. `row_count_bound` — `gold_df` must not have more rows than `silver_df`. Analyst's code only
   ever aggregates/filters `df`, never joins in new rows; more output rows than input rows means
   the generated code did something the prompt never asked for (e.g. an accidental cross join).
3. `empty_result` — a non-trivial `narrative` (contains a digit) paired with an empty `gold_df`
   is flagged: the narrative claims a specific number that no data backs.

Science (`check_science_output`):
1. `metric_range` — the well-known valid range for each metric key already present in
   `model_info["metrics"]` (`r2` should not exceed 1.0 by more than rounding noise; `accuracy`/
   `silhouette` must be in `[-1, 1]`; `rmse`/`mae`/`mse` must be `>= 0`) — metrics outside their
   mathematically valid range mean the sandboxed code computed them against mismatched
   arrays/columns, not that the model fit badly (a bad-but-valid fit, e.g. `r2=-0.3`, is not
   flagged — that's a legitimate modeling outcome, not a sanity failure).
2. `prediction_range` — for `forecast`/`regression` tasks, every numeric column in
   `predictions_df` that isn't `actual`/`residual` (i.e. the model's own predicted values) is
   compared against `model_info["target"]`'s historical range in `silver_df` (when that column
   exists there): a predicted value more than 3x the historical `[min, max]` span outside that
   range is flagged — "previsão dentro de uma faixa razoável dado o histórico" from the roadmap's
   own scope note. The 3x multiplier is deliberately generous (a real trend or seasonal forecast
   can legitimately exceed historical bounds) — this catches an order-of-magnitude error, not a
   normal extrapolation.

Every check emits one entry per rule it evaluates, always (`severity: "ok"` on pass), same as
`_check_custom_rules`'s own choice (ADR-023) — so a run's manifest shows every check that ran,
not just the ones that fired. Non-applicable checks (e.g. `prediction_range` when
`model_info["target"]` isn't in `silver_df`, or when the task isn't diagnostic/predictive) are
skipped rather than emitted as false "ok" — an absent check is different from a check that ran
and passed, and the roadmap's own done-criteria only needs *fired* checks to be visible.

**Severity is always `"warning"`, never `"error"`, for every check in this module.** Unlike
`agents/quality.py`'s custom rules (which can legitimately block the pipeline via
`route_after_quality`), a sanity-check finding here never withholds the result from the user —
per the roadmap's own phrasing, "nem silenciosamente aceito, nem silenciosamente rejeitado". The
Analyst/Science agent already retried up to 3 times (and once more via
`run_gold_with_repair`/`run_science_with_repair`'s fallback question) before returning
successfully; a fourth automatic rejection at this layer, with no further retry path, would just
turn a flagged-but-visible result into an invisible failure with no explanation — worse for
trust, not better. A result that fails a sanity check is still returned, still usable, but is no
longer silently presented as unconditionally trustworthy — exactly what the definition of done
asks for.

**Alternatives considered and rejected:**

- **A second LLM pass reviewing `gold_df`/`predictions_df`/`narrative` against the original
  question before returning.** Rejected for this sprint, not forever: it roughly doubles
  Gold/Science's LLM cost and latency per sub-task (`core/pricing.py`'s `compute_cost_usd` and
  `stage_latencies`, ADR-012, would both need to account for a 4th LLM round-trip on top of the
  existing up-to-3 code-generation attempts) for a failure class — logically-wrong-but-internally-
  consistent output — that the deterministic checks above already catch directly for the
  roadmap's own two named cases (sum-vs-total, forecast-range). An LLM reviewer is strictly
  better at catching semantic errors no fixed rule can enumerate in advance (e.g. "this narrative
  answers a different question than what was asked"), but that is a real, distinct next
  increment, not a blocker for this sprint's definition of done. Left as a documented future
  extension (see Consequences) — the `OutputSanityCheck` shape has room to add an
  `"llm_review"`-sourced entry into the same `checks` list later without a breaking change,
  mirroring how `_check_custom_rules` slots into `quality_report.checks` alongside the fixed
  checks today.
- **A combination from day one** (deterministic checks + a cheap/cached LLM pass only on the
  sub-task that fails a deterministic check). Considered — it's a reasonable v2 — but rejected
  for this sprint specifically because it couples the new LLM-cost surface to a "did a
  deterministic check fire" branch that has no test coverage of its own yet; shipping the
  deterministic layer first, observing what it actually catches/misses in the case study, and
  then deciding whether an LLM-triage tier earns its cost is the same "measure before optimizing"
  posture ADR-013 (Sprint 12 scale) used for schema truncation and sandbox timeouts.
- **Blocking the result outright (severity can reach `"error"`, mirroring `quality_report`'s
  `route_after_quality`).** Rejected: Gold/Science already run inside a `run_analysis_tasks` loop
  with no equivalent to Quality's post-run `route_after_quality` gate — the analysis layer has no
  "END" state to route to short of dropping the sub-task's result entirely, which contradicts the
  roadmap's own "nem silenciosamente rejeitado" requirement.

## Decision 2 — Attached to the existing `GoldResult`/`ScienceResult` dict, no new table/migration

`core/analysis_types.py` gains `sanity_check: NotRequired[OutputSanityCheck]` on both
`GoldResult` and `ScienceResult` — `NotRequired` because it is only ever set on a successful
result (`result["error"] is None`); a failed sub-task has nothing to sanity-check. Populated in
`services/pipeline_service.py::run_gold_analysis`/`run_science_analysis`, immediately after
`run_analyst`/`run_science` return successfully and before the function's existing
`progress_callback` summary line — a `severity != "ok"` result gets its own visible progress
line (`"⚠️ Gold pronto com ressalva de sanity-check"`), the same "surfaced, not silent" treatment
Sprint 7's UI already gives a repaired sub-task.

**Persisted exactly like `model_info`/`code` already are** — `audit/db.py::_serialize_analysis_result`
gains one additive key (`serialized["sanity_check"] = result["sanity_check"]` when present) in
the existing `{run_id}_analysis.json` manifest. **No new migration, no new table.** Unlike
`quality_rules` (ADR-023, a per-pipeline *configuration* with no other source of truth, needing
persistence independent of any one run) or `consecutive_failures` (ADR-020, a cross-run rolling
aggregate), a sanity-check result is exactly the same kind of per-run, computed-fresh,
never-independently-queried value `narrative`/`code`/`model_info` already are — it belongs in the
same JSON manifest, for the same reason. `alembic/versions/0015_....py` was reserved for this
sprint but is **not used** — confirmed by inspection that no new column/table is needed before
writing any code, not assumed going in.

**Why not a `PipelineState` field, mirroring ADR-023's `custom_quality_rules`?** Rejected: Gold/
Science sub-tasks are not LangGraph nodes and never touch `PipelineState` at all — `run_analyst`/
`run_science` take a plain `df`/`business_question` and return a plain result dict
(`pipeline_service.py`'s own docstrings are explicit that the analysis layer is a separate stage
after the Silver LangGraph graph has already finished and produced `state["transformed_data"]`).
Threading a new state field through a graph that has nothing to do with this check would violate
the same "nodes are pure functions of `PipelineState`" property ADR-023 itself protects — this
check belongs at the layer that already owns `silver_df`/`gold_df`/`predictions_df` together,
which is `pipeline_service.py`, not `core/state.py`.

## Decision 3 — Frontend: an additive badge on the existing sub-task card, no new page

`frontend/src/lib/types.ts`'s `AnalysisEntry` gains
`sanity_check?: { checks: Record<string, unknown>[]; severity: "ok" | "warning" | "error";
summary: string }` (severity's `"error"` arm kept in the type for shape-parity with the backend
`OutputSanityCheck`/`QualityCheck` pattern, even though this module never actually emits it — see
Decision 1). `components/analysis-section.tsx` — the same per-sub-task Card that already renders
a `"reparado"` badge for an auto-repaired sub-task — gets one more conditional `Badge` when
`sanity_check.severity !== "ok"`, labeled "ressalva" (amber, same color family as `"reparado"`),
with the check summary as its native `title` tooltip. No new route, no new component, no new
endpoint: `GET /runs/{run_id}` already returns the full manifest `_serialize_analysis_result`
writes, this field is additive to a shape the frontend already deserializes.

**Why not fold it into `pipeline-tab.tsx`'s existing `QualityCheck` renderer?** Rejected: that
component renders `state.quality_report` — the *Silver-layer* quality report, produced once per
run before Gold/Science even starts. A sanity-check result is per Gold/Science *sub-task*
(there can be several, one per Planner sub-question), which only `analysis-section.tsx` already
iterates one-card-per-sub-task; reusing `pipeline-tab.tsx` would require it to reach across
layers it doesn't otherwise know about.

## Consequences

- Catches the roadmap's own two named failure shapes (aggregation sum exceeding the Silver
  total; a forecast/prediction wildly outside historical range) and the general shape a
  deliberately-fabricated `gold_df`/`predictions_df` takes in this project's own test suite (see
  `tests/unit/test_output_validation.py`) — without adding LLM cost or latency to every
  Gold/Science sub-task.
- Genuinely *semantic* errors a fixed rule can't name in advance (e.g. correct sum, correct
  range, but the code answered a subtly different question than what was asked) are **not**
  caught by this sprint's checks — flagged explicitly, not silently assumed solved. An LLM-review
  second pass (Decision 1's rejected-for-now alternative) is the natural next increment if the
  case study or real usage shows this gap matters in practice.
- `sanity_check.severity` never reaches `"error"` in this module's own output — a result is
  always still returned to the user, only ever with or without a caveat badge. If a future sprint
  wants a genuinely blocking sanity check, that is a new decision (a Gold/Science-layer
  equivalent of `route_after_quality`), not an extension of this ADR's checks.
- `check_gold_output`'s `sum_conservation`/`row_count_bound` checks only apply when `gold_df` and
  `silver_df` share a numeric column name / when `silver_df` is available at all — a sub-task
  whose `gold_df` renames every column (a real, common LLM behavior — Analyst's own prompt allows
  "Include only the most relevant columns") produces fewer applicable checks, not false
  positives. Documented as a real coverage gap, not silently worked around.
- `alembic/versions/0015_....md` slot reserved for this sprint by the orchestrating session was
  confirmed unnecessary and deliberately left unused — flagged here so a future renumbering pass
  doesn't assume a missing migration file is a bug.

## Related

- ADR-007 — the sandbox execution guarantee ("ran without error") this ADR explicitly does not
  extend to "produced a correct result", which is the gap this ADR closes.
- ADR-023 — the declarative, no-`exec()`/`eval()`, "checks emit one entry per rule always" pattern
  this ADR reuses on the output side.
- ADR-018 — `core/drift.py`'s "pure functions, no I/O" module shape, mirrored by
  `core/output_validation.py`.
- ADR-013 — the "measure before optimizing" posture behind deferring an LLM-review tier until
  the deterministic layer's real coverage is observed.
- Vault: `artefact/product-roadmap-post-tcc.md`, Sprint 21.
