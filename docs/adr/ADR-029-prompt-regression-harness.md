# ADR-029: Prompt/Agent Regression Harness

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 28 (post-TCC product roadmap)

## Context

Sprint 21 (ADR-026) added deterministic sanity checks (`core/output_validation.py`) that flag
an internally-consistent-but-numerically-wrong Gold/Science result. That protects one run. It
does nothing to protect the *prompts and agent code that produced it* from silently regressing
over time: a Transformer/Analyst/Science prompt edit, a model swap (`AI_ETL_LLM_MODEL`), or an
unrelated refactor that changes how a prompt is assembled can all make output quality worse
without any existing test catching it — `tests/e2e/`'s 4 scenarios (Sprint 5) are a small, fixed
set exercised with **mocked** LLM responses (deterministic by design, so they cannot detect a
prompt-wording regression at all — they test plumbing, not prompt quality), and Sprint 8's
`model_comparison.py` is a manually-invoked experiment, not a gate anything runs automatically.

**Definition of done (roadmap):** a deliberately worse prompt change, injected in a test, is
caught by the harness before it reaches production.

**Investigated first:**
- `case_study/scripts/model_comparison.py` (Sprint 8) — real precedent: calls
  `pipeline_service.run_full_analysis` directly (bypassing Celery/Postgres), computes real cost/
  latency/an objective `score_quality()` metric, tags every row `data_source: real|mock|
  simulated|skipped_no_infra` depending on real credential/infra availability. Reused directly
  here rather than re-invented — see Decision 2.
- `core/output_validation.py` (Sprint 21, ADR-026) — `check_gold_output`/`check_science_output`
  are already wired into `pipeline_service.run_gold_analysis`/`run_science_analysis` and attached
  to every successful `GoldResult`/`ScienceResult` as `sanity_check`. This harness does not need
  to invoke them separately — `run_full_analysis`'s own return value already carries them.
- `tests/e2e/`'s 4 fixed scenarios (Sprint 5) — real confirmation of the gap this sprint closes:
  each scenario asserts pipeline plumbing (auth, async, sandbox, storage) with mocked, fixed LLM
  output. None of them compares a metric across two versions of a prompt; a strictly-worse
  prompt edit would leave all 4 green.
- `.github/workflows/ci.yml` — one `check` job (Python 3.11/3.12 matrix: lint/format/type/unit/
  integration/security) plus an isolated `e2e` job (its own Postgres/Redis containers). Both run
  automatically on every `push`/`pull_request` to `main`. Neither has real LLM cost today:
  `integration`'s/`e2e`'s `OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}` env only feeds tests
  that themselves mock every LLM call site (same "no real OpenAI dependency in a suite that runs
  on every push" rule `tests/e2e/conftest.py` documents explicitly) — the secret being wired in
  is not evidence any existing job spends real LLM money today.

## Decision 1 — Manual `workflow_dispatch` trigger, not automatic on every push/PR

The roadmap explicitly asks this question rather than assuming an answer: does this run on every
PR, only on agent/prompt changes, or only manually? Three trigger options were weighed:

**(a) Automatic on every `push`/`pull_request` (mirroring `check`/`e2e`).** Rejected outright —
this harness's entire value (catching a *prompt-wording* regression, not just a plumbing one)
only exists when it calls a real LLM. Running real LLM calls across a multi-scenario corpus on
every push would mean: (1) real money spent on every commit to every PR, including trivial
formatting fixups — this project's own working agreement (`docs/CURRENT_STATE.md`, "Sprint 6"
section: "batch local changes into fewer CI-triggering pushes — CI minutes cost money on this
account") already treats CI cost as a real constraint even for *free* compute; real per-token
spend is a harder constraint, not a softer one; (2) this repo's own sandboxed dev environment
frequently has **no `OPENAI_API_KEY` at all** (confirmed absent in this session, same as Sprints
8/12/22's sessions) — an automatic job that hard-fails without a key would either block unrelated
PRs on missing infra, or need a silent-skip escape hatch that defeats the "definition of done"
requirement (a check nobody can rely on catching a regression is not a real gate).

**(b) Automatic, but path-filtered to `src/ai_etl/agents/**`, `src/ai_etl/core/llm.py`,
`src/ai_etl/core/output_validation.py` only.** A real middle ground — considered seriously, not
dismissed. Rejected *for this sprint specifically*, not forever: it still spends real LLM money
on every push to any PR touching those paths, including work-in-progress pushes mid-development
(this project's iterate-locally-then-push-once discipline reduces but does not eliminate that).
With zero observed data yet on what this corpus actually costs per run (no real credentials
available this session to measure it — see Decision 3's `data_source` honesty requirement), gating
automatic real spend behind a number nobody has measured is the same "optimize before you
measure" mistake ADR-013 (Sprint 12 scale) deliberately avoided for schema truncation and sandbox
timeouts. Flagged as the natural **next** step once a few manual runs (Decision 1's chosen path)
establish real per-run cost.

**Chosen: manual `workflow_dispatch` only** (`.github/workflows/prompt-regression.yml`, new,
deliberately separate from `ci.yml` — mirrors `e2e`'s own "isolated job, isolated services"
reasoning, here isolated by *trigger* instead of by *infra*, so a missing/absent secret can never
block the `check`/`e2e` jobs every PR already depends on). A developer changing a prompt, an
agent's code, or `AI_ETL_LLM_MODEL` runs it by hand (`gh workflow run prompt-regression.yml`, or
locally via `uv run python case_study/scripts/regression_harness.py`) before opening/merging that
specific PR — the same discipline this project already applies to `make check`, just for the one
narrow class of change (prompts/agents/model) this harness actually protects. This is a **process
requirement documented here and in the harness's own CLI help**, not a GitHub required-status-
check — this repo has no branch-protection automation to enforce it against, same as every other
"run this before merging" rule already in `CLAUDE.md` (e.g. `make check`).

**When credentials genuinely aren't available** (this sandbox, most sessions): the harness still
runs in **mocked** LLM mode (Decision 2) — it cannot catch a prompt-*wording* regression that way
(mocked responses are fixed regardless of prompt content), but it still exercises and protects the
surrounding deterministic code the harness itself depends on (`score_quality`, `output_validation`,
extractor edge-case handling, the comparison/regression-detection logic) — same value split Sprint
8 already established and documents explicitly per run via `data_source`.

## Decision 2 — Reuse Sprint 8's harness shape and Sprint 21's sanity checks; do not invent a new quality metric

New `case_study/scripts/regression_harness.py`, structurally a sibling of `model_comparison.py`
(same direct-call-to-`run_full_analysis`, same audit-persistence bypass via monkeypatched
`save_run`/`save_analysis`/`save_stage_latencies`, same `data_source` tagging convention) rather
than a rewrite — imported the same way `tests/unit/test_model_comparison.py` already imports
`model_comparison.py` (`importlib.util.spec_from_file_location`, since `case_study/scripts/` is
not an installed package). `score_quality()` (Sprint 8) is reused unmodified, not duplicated.

**No new quality metric invented.** Per-scenario quality is the same `score_quality(state)` 0-100
Silver-layer score Sprint 8 already defined (status/quality-severity/completeness/transformer-
efficiency), plus a new **sanity-warning count** — the number of successful Gold/Science
sub-task results in the run whose `sanity_check["severity"] != "ok"` (ADR-026, already computed
and attached by `run_gold_analysis`/`run_science_analysis` with zero extra wiring needed — the
harness only has to read `result["gold"]`/`result["science"]` off `run_full_analysis`'s existing
return value). A regression is: the corpus's mean/min `score_quality` drops beyond a tolerance, OR
the total sanity-warning count increases, OR a scenario's expected pass/fail status flips — see
Decision 4 for the exact comparison function.

## Decision 3 — Corpus: `case_study/scenarios/*.json`, larger and more adversarial than the 4 fixed e2e scenarios, checked into version control

`tests/e2e/`'s 4 scenarios are fixed by design (Sprint 5, "one per source-type combination") —
correct for what they protect (plumbing), wrong shape for what this sprint needs (a corpus that
can grow independently, and is deliberately adversarial rather than one-happy-path-per-source-
type). New `case_study/scenarios/` directory, one JSON file per scenario (`id`, `category`,
`data` source, `spec_template`, `business_question`, `expect_failure`, and a `mock` block used
when no real LLM key is available):

1. `sales_revenue_by_region` — nominal descriptive, real 5000-row case-study dataset (Sprint 1's
   `generate_sales.py`), exercises ADR-026's `sum_conservation`/`row_count_bound` on a normal run.
2. `sales_daily_forecast` — nominal predictive, same dataset, exercises `prediction_range`.
3. `sales_empty_result_narrative` — adversarial: a Gold sub-task whose result is empty but whose
   narrative still cites a number — the exact shape ADR-026's `empty_result` check exists to catch,
   as a corpus scenario rather than only a `test_output_validation.py` unit test.
4. `dirty_semicolon_city_totals`, `dirty_latin1_city_totals`, `dirty_tab_city_totals` — three of
   Sprint 22's dirty-data fixtures (`tests/fixtures/dirty_data/`, encoding/delimiter edge cases
   already confirmed real), each paired with a business question, extending Sprint 22's
   extraction-only coverage into a full run's *quality*, not just "did extraction not crash".
5. `malformed_quoting_expected_failure` — Sprint 22's row-length-mismatch fixture, `expect_failure:
   true`: the harness asserts the pipeline still correctly *fails closed* (`status == "failed"`,
   ADR-022's `_validate_row_lengths`) rather than silently returning shifted, wrong data — a
   regression here (someone loosens that validation) is exactly as real a quality regression as a
   worse prompt, and belongs in the same harness.

Reusing Sprint 22's real fixture files (rather than authoring new corrupt CSVs) keeps this corpus
grounded in dirt this project already confirmed is real, instead of synthetic guesses. Adding a
new scenario is one new JSON file — no code change — matching the roadmap's "corpus de cenários
ampliado" deliverable literally.

## Decision 4 — Comparison against a committed baseline, pure-function regression detection (unit-testable without any LLM)

`case_study/results/sprint28/baseline_metrics.json` — one committed JSON file (aggregate +
per-scenario metrics from a known-good harness run), analogous to how `case_study/results/
sprint8/` already commits its own report. `regression_harness.py --update-baseline` overwrites it
deliberately (a human decision after reviewing a new run's output, never automatic).

A plain run (no flag) computes the current corpus's metrics, loads the baseline, and calls a
**pure function** `compare_against_baseline(current, baseline, quality_tolerance=5.0)` —
no I/O, no LLM, mirrors `core/drift.py`'s and `core/output_validation.py`'s own "pure functions"
shape — that flags: mean/min `score_quality` dropping more than the tolerance corpus-wide *or*
for any individual scenario by id, total sanity-warning count increasing, or any scenario's
pass/fail expectation flipping. Exits non-zero (fails the `workflow_dispatch` job, or a local
`echo $?`) when any regression is flagged.

**This function is the literal target of this sprint's definition of done, and is unit-tested
directly** (`tests/unit/test_regression_harness.py`) with a fabricated "current" report built from
the real `score_quality`/`check_gold_output` functions against a deliberately-corrupted `gold_df`
(same technique `tests/unit/test_output_validation.py` already uses) fed through `compare_against_
baseline` against a synthetic "good" baseline — proving the harness's own regression-detection
logic catches a deliberately-worse result, **without needing a real LLM call or CI credentials
to verify it**. This closes the gap Decision 1 leaves open: the detection *logic* is fully
verified in the standard `pytest tests/unit` suite (real gate, every push); only the *prompt-
wording* half of "does the real LLM's output get worse" needs the manual, credentialed run.

## Consequences

- The harness catches: (1) a real prompt-wording/model regression, when run manually with a real
  `OPENAI_API_KEY` — the roadmap's actual ask; (2) a regression in the surrounding deterministic
  code (`score_quality`, `output_validation`, extractor edge-case handling, or the comparator
  itself) even in mocked mode, i.e. on every `pytest tests/unit` run, no credentials needed.
- **What it does not do**: it is not a required GitHub status check (no branch protection exists
  in this repo to attach one to) and does not run automatically on every prompt/agent-touching
  PR — a developer who forgets to run it manually before merging a prompt change is not stopped by
  CI. This is the direct, accepted cost of Decision 1; revisit with the path-filtered-automatic
  option (Decision 1, alternative b) once a handful of manual runs give a real per-run cost figure
  to budget against.
- `case_study/results/sprint28/baseline_metrics.json` committed this session is generated in
  **mocked** mode (`data_source: "mock"` throughout — this sandbox has no `OPENAI_API_KEY`, same
  documented limitation as Sprints 8/12/22) — flagged explicitly, not presented as a real-quality
  baseline. A meaningful real baseline needs one `--update-baseline` run with real credentials
  before this harness can catch a real prompt regression end-to-end; until then it still exercises
  and protects its own detection logic (see above).
- `alembic/versions/0016_....py` was reserved for this sprint and is **not used** — confirmed by
  inspection before writing any code that this sprint needs no schema change (pure CI/test
  infrastructure, no new persisted state) — flagged here so a future renumbering pass doesn't
  mistake a missing migration file for a bug, same pattern ADR-026 already used for its own
  reserved-but-unused `0015`.

## Related

- ADR-026 — the `sanity_check` result this harness reads directly off `run_full_analysis`'s
  return value, with zero new wiring.
- ADR-013 — the "measure before optimizing" posture behind deferring the path-filtered-automatic
  trigger until real per-run cost is observed.
- Sprint 8 (no ADR — CLI/script-only scope) — `model_comparison.py`'s harness shape, `score_
  quality`, and `data_source` honesty convention, all reused directly here.
- Sprint 22 (PR #61, no dedicated ADR) — the dirty-data fixtures this corpus's adversarial
  scenarios reuse.
- Vault: `artefact/product-roadmap-post-tcc.md`, Sprint 28.
