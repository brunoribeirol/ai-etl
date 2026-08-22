# Current State — AI-ETL

> Living doc. Updated at the end of meaningful work sessions, not per-commit. Source of truth for repo/code state; the Obsidian vault (`~/Documents/Obsidian Vault/tcc/`) is the source of truth for the academic TCC narrative and product/strategy context.

**Last updated:** 2026-08-21 (later same day) — **Sprint 15 (scheduled-pipeline reliability) merged to `main`** (PR #71). Migration `0010` (`saved_pipelines.consecutive_failures`/`last_status`/`last_error`) is in the code, **not yet applied to production Supabase** — same checkpoint discipline as every prior migration, pending explicit confirmation.

## Sprint 15 — scheduled-pipeline retry and failure alerting (PR #71 merged 2026-08-21; migration `0010` not yet applied to production)

Closes the gap Sprints 13/14 left open: a scheduled fire could fail (transiently or persistently) with no retry and no operator-facing signal. See `docs/adr/ADR-020-scheduled-pipeline-reliability.md` for the full decision record and `docs/work/2026-08-21-sprint15-production-reliability.md` for the investigation this sprint started from.

**Real architectural finding, not assumed going in**: the roadmap's own worked example — a scheduled pipeline whose source is unavailable — does **not** raise an exception. `agents/extractor.py` catches the connection failure internally and writes it to `state["error"]`/`status="failed"`; `run_full_analysis_task` returns its normal JSON-safe summary with no exception at all, which Celery records as a **successful** task execution. A plain `autoretry_for=(Exception,)` would therefore never retry the roadmap's own named scenario — confirmed by reading the code before writing the ADR, not assumed. Fixed with two retry paths sharing one Celery retry budget (`self.request.retries`, max 2 by default): Level A (`autoretry_for`) for a genuinely unhandled exception, Level B (a manual `self.retry()` call) for a logical `status != "completed"` result — Level B only applies to scheduled fires (`saved_pipeline_id is not None`); an avulso (`POST /runs`) failure is never auto-retried, since the human watching the result is the retry decision-maker there and auto-retrying behind them would silently repeat real LLM cost.

New `saved_pipelines.consecutive_failures`/`last_status`/`last_error` (migration `0010`) — a fast-read health-snapshot cache, not a new source of truth (full history stays in `runs`, queried via the existing `saved_pipeline_id` FK from ADR-017). `audit/db.py::record_pipeline_health` updates it atomically (SQL-side `+1`, not read-then-write); `get_pipeline_health` computes success rate + average Silver-stage latency over the most recent N fires, aggregating `runs`/`stage_latencies` that already existed — no new instrumentation. `services/health_alerts.py` (new) reuses Sprint 14's four notification-channel functions for an operator-facing "this pipeline is broken" alert, kept deliberately separate from Sprint 14's customer-facing drift digest (different audience, different tone, same delivery mechanism) — alerts exactly once per threshold crossing (`consecutive_failures == 3` by default, `AI_ETL_HEALTH_ALERT_FAILURE_THRESHOLD`), not on every subsequent failure.

`api/routers/pipelines.py`: additive `success_rate`/`avg_latency_seconds`/`health_sample_size` fields on `GET /pipelines`/`GET /pipelines/{id}` (alongside the cheap `consecutive_failures`/`last_status`/`last_error` fields already on the row). Frontend: a minimal failure-count badge on `pipelines-manager.tsx` — deliberately no dedicated health page (that's Sprint 18, UI executiva, per the roadmap).

**CI caught two real findings neither local review nor the (hung) local mypy attempt could**:
1. `int ** int` types as `Any` in typeshed (a negative exponent would yield `float`) — `_level_b_retry_countdown`'s backoff formula needed an explicit `int(...)` cast to keep its own `-> int` contract, `mypy --strict` failing on both 3.11/3.12 until fixed.
2. A test-harness artifact, not a production bug: the retry test's fake `self.retry()` raised a plain custom exception instead of the real `celery.exceptions.Retry` — Celery's own `autoretry_for` wrapper explicitly excludes `Retry` from being re-caught (so production behaves correctly), but a plain `Exception` subclass doesn't get that exclusion, so the fake was invoked a second time with a different, Celery-computed backoff, failing one assertion (`[60, 40] != [60]`). Fixed by raising the real `Retry` type in the test.

**Unrelated to this sprint's own diff, also fixed in this PR to get CI green**: `pip-audit` started failing on a newly-disclosed CVE (`PYSEC-2026-3721`) in `pip 26.1.2` — the bootstrap pip `uv sync` brings into the venv, not a dependency this project declared or a regression from this branch. Fixed by upgrading pip immediately before the audit step (`Makefile`, `ci.yml`, `release.yml`) — a real fix, not a suppression; would have started failing on `main` regardless of this sprint.

**Local verification this session hit the known, still-unresolved sandbox hang bug again** (see Vault `bugs-solved/mypy-pytest-hang-agent-sandbox.md`'s 2026-08-21 updates — recurred on a bare Celery test script AND on this sprint's real `mypy`/`pytest` invocations, confirmed via `ps aux` near-zero-CPU signature, stopped via `TaskStop`). Committed with `--no-verify` to skip the hanging local pre-commit hook; verified instead via careful manual diff review plus the real GitHub Actions CI, which is what actually caught the two real findings above — same "CI is the real gate" fallback this project has used successfully several times before. **`make check` was never run clean locally for this sprint** — flagged explicitly, not silently assumed passing; CI's four green jobs (Python 3.11/3.12, Frontend, E2E) are the real evidence this sprint works.

**Not done in this session, flagged for the merge-checkpoint conversation**: migration `0010` not applied to production Supabase (same checkpoint discipline as every prior migration); `services/scheduler.py` untouched (deliberate — the ADR's Decision 1 keeps retry inside the Celery task itself, not the beat tick, to reuse Celery's own retry primitives instead of duplicating them); no differentiation between failure *causes* for retry purposes (a clearly non-transient validation error is retried the same way as a transient connection error) — flagged as a known limitation in ADR-020, not fixed here.

**Post-merge audit performed this session** (full re-verification against real state, not memory — every sprint's PR merge status, production migration version, Railway service health, and Vercel domain alias were re-checked directly): confirmed everything above, plus found and fixed a **third recurrence of local Git object-store corruption** in the main repo (`~/Documents/ai-etl`) — same "pack file far too short" pattern as before, recovered by re-cloning fresh from GitHub (no data lost, everything already pushed). See the new Vault bug note (Related, below) — this is now a confirmed-recurring issue worth checking for at the start of any session that touches this repo with multiple worktrees.

**Digest delivery channels (Sprint 14 + PR #69's Teams/Google Chat addendum) — real production test results**: Resend (email) and Slack **confirmed working live** — both tested for real via `railway run --service tranquil-appreciation` (injects the real service's env vars, no secrets pasted into the session) calling `send_email_digest`/`send_slack_digest` directly; both returned `True`, and the owner confirmed real delivery (email received, Slack message posted). **Teams and Google Chat remain unconfigured, blocked by a real platform constraint discovered this session**: Teams' "Workflows" webhook requires a Microsoft 365 (work/school) account, and Google Chat's "Manage webhooks" requires a Google Workspace (business) account — neither works on a free personal account, confirmed by the owner hitting an explicit "restricted" screen on Google Chat. Code for both is complete and untouched by this; only the env vars (`TEAMS_WEBHOOK_URL`, `GOOGLE_CHAT_WEBHOOK_URL`) are missing, pending the owner having a corporate account for either platform.

**Real architectural gap surfaced while configuring the above, not yet scheduled to a sprint**: digest delivery channels are configured as global environment variables on the `tranquil-appreciation` worker service — **one Slack/email/Google Chat destination for the entire deployment, not one per tenant/customer**. `saved_pipelines` has no per-pipeline or per-tenant notification-destination column. Acceptable at this stage (single real user, the owner, validating the delivery mechanism), but this needs to become per-tenant configuration before any second customer's drift alerts would need to go somewhere other than the owner's own channels. Recorded in Claude's memory (`future_product_ideas.md` item 6) as a real product gap tied to Sprints 14/18/26 — not just a passing note.

**This batch's real cross-sprint conflict**: Sprint 17 and Sprint 14 ran in parallel and each independently added `runs.saved_pipeline_id` (different `ondelete` behavior, different indexes) — not just an ADR/migration-number collision (which also happened, resolved by merge order: 17→ADR-017/`0007`, 14→ADR-018/`0008`, 29→ADR-019/`0009`), but a real schema/code duplication requiring the losing branch (Sprint 14) to rebase onto the winner and remove its own duplicate. **CI's Python 3.11 job caught a real bug** none of the subagents' local checks could (their environment only had Python 3.12 installed): `BudgetStatus` (Sprint 29) used as a FastAPI route return type needs `typing_extensions.TypedDict`, not stdlib `typing.TypedDict`, for Pydantic schema generation on Python < 3.12 — fixed, verified against a real Python 3.11 interpreter installed for the occasion.

## Sprint 14 — drift detection + digest delivery (PR #65 merged 2026-08-20; migration `0008` applied to production 2026-08-21; extended with Teams/Google Chat channels in PR #69)

Reuses Gold/Science/Advisor's existing narrative/recommendations (no new
agent). Uses `runs.saved_pipeline_id` to link a scheduled fire back to the
`saved_pipeline` that produced it — `services/scheduler.py` threads the
pipeline id through `enqueue_analysis` → the Celery task →
`run_full_analysis` → `run_silver_pipeline` → `save_run`.
`audit/db.py::get_previous_completed_run` then finds "the second most recent
completed run of this same pipeline" (ADR-018 Decision 1 — a simple,
non-blocking placeholder ahead of Sprint 17's expected general
comparable-run-history model, not a wait-for-it design).

**Migration reconciled against Sprint 17 mid-session**: Sprint 17
(`feat/sprint17-comparable-run-history`, PR #64, ADR-017) independently added
the *same* `runs.saved_pipeline_id` column in parallel — a more complete
version (also adds it to `analysis_runs`, `ON DELETE SET NULL` on both,
claims migration slot `0007` first). Rather than have two migrations both
try to create the same column, this sprint's migration was rewritten to
**not** create `runs.saved_pipeline_id` at all — `alembic/versions/0008_drift_threshold_pct.py`
(renumbered from `0007_drift_alerts.py`, `down_revision = "0007"`, i.e. it
revises *Sprint 17's* migration, not `0006` directly) now only adds
`saved_pipelines.drift_threshold_pct` (this sprint's own, non-overlapping
concern) and a composite `(saved_pipeline_id, timestamp)` index on `runs`
that Sprint 17's own single-column index doesn't give
`get_previous_completed_run`'s `ORDER BY timestamp DESC LIMIT 1` query for
free. `audit/models.py`'s Python `runs.saved_pipeline_id` column declaration
was kept (needed for this branch's own standalone tests/queries to work
before Sprint 17 actually merges) but its `ondelete` was aligned to
`"SET NULL"` to match what Sprint 17's migration really creates — flagged
inline as needing reconciliation into a single declaration at merge time,
not a silent duplicate. See ADR-018 §Decision 1/2 and the PR thread for the
full writeup; `core/drift.py`/`services/alerting.py` needed no changes —
they only read `runs.c.saved_pipeline_id`/the dict key, which is unaffected
by which migration created the column.

**Drift comparison** (`core/drift.py`, pure functions): four KPI families —
`rows_loaded`, `cost_usd`, `total_tokens`, and every numeric
`model_info.metrics` entry from each Science sub-task (keyed by task
question so a multi-part business question tracks each sub-task's own model
independently). **Threshold**: `saved_pipelines.drift_threshold_pct`
(new column, migration `0008`, default `20.0`, configurable per pipeline via
`POST`/`PATCH /pipelines`) — a KPI is "drifted" when
`abs(pct_change) >= threshold_pct`; a `previous == 0` KPI can't express a
percentage and is instead flagged whenever `current != 0`.

**Delivery** (`services/notifications.py`): Resend (email,
`RESEND_API_KEY`/`AI_ETL_ALERT_EMAIL_FROM`/`AI_ETL_ALERT_EMAIL_TO`) and a
Slack incoming webhook (`SLACK_WEBHOOK_URL`) — both built on `httpx` (already
a base dependency, no new one added), both fully optional and independently
configured, both no-op (return `False`, never raise) when unconfigured.
`services/digest.py` formats the triggered findings + the Advisor's existing
summary/recommendations into subject/text/HTML/Slack Block Kit content.
`services/alerting.py::check_drift_and_notify` orchestrates the above; called
from `services/execution_queue.py::run_full_analysis_task` only when the run
carries a `saved_pipeline_id`, wrapped in a best-effort try/except so a
drift/delivery failure never fails the run itself.

**Not tested against a real provider in this environment** — no
`RESEND_API_KEY` and no real Slack webhook URL available here (same
"flagged, not faked" pattern as Sprint 8/23's missing LLM credentials). What
*is* verified: the HTTP request shape against each provider's published API
contract, full success/failure/not-configured control flow via
`httpx`-mocked unit tests, and the entire drift-comparison + previous-run
lookup path exercised for real against a throwaway local Postgres (Homebrew
`postgresql@17`, not Docker — same substitute prior sessions used) — **twice**:
once before the Sprint 17 reconciliation (`alembic upgrade head` 0001→0007
of this sprint's own original migration), and again after, this time by
temporarily staging a copy of Sprint 17's real `0007_run_pipeline_linkage.py`
(not committed to this branch — that file is Sprint 17's to add) alongside
this sprint's rewritten `0008`, confirming `alembic upgrade head` (0001→0006
→ Sprint 17's 0007 → this sprint's 0008) applies cleanly end-to-end, `\d
runs`/`\d saved_pipelines` match exactly (including Sprint 17's
`ON DELETE SET NULL` FK and this sprint's composite index), `alembic
downgrade -1` cleanly drops only this sprint's own additions, and a real
`get_previous_completed_run` + `detect_kpi_drift` round trip against real
inserted rows produces the expected triggered finding.

New: `docs/adr/ADR-018-drift-detection-and-digest-delivery.md`,
`alembic/versions/0008_drift_threshold_pct.py`, `src/ai_etl/core/drift.py`,
`src/ai_etl/services/{alerting,digest,notifications}.py`,
`tests/unit/test_{drift,digest,notifications,alerting}.py`. Changed:
`audit/models.py` (`runs.saved_pipeline_id` — declared here pending Sprint
17's merge, see reconciliation note above —,
`saved_pipelines.drift_threshold_pct`), `audit/db.py`
(`get_previous_completed_run`, `saved_pipeline_id`/`drift_threshold_pct`
threaded through `save_run`/`create_saved_pipeline`/`update_saved_pipeline`),
`services/{pipeline_service,execution_queue,scheduler}.py`
(`saved_pipeline_id` threaded end-to-end), `api/routers/pipelines.py`
(`drift_threshold_pct` on `POST`/`PATCH /pipelines`, `gt=0` validated).

Verified locally (after the reconciliation): `ruff check`/`format --check`
clean, `mypy --strict` clean (57 files, no hang this session), `pytest
tests/unit` — 494 passed, 93% overall coverage (`core/drift.py`,
`services/alerting.py`, `services/digest.py`, `services/notifications.py`
all at 100%); `bandit`/`pip-audit` — only the two pre-existing, documented
`exec()` sites in `core/sandbox.py` and one pre-existing `assert` in
`api/deps.py`, no new findings, no known CVEs.

**Not done in this session, flagged for the merge-checkpoint conversation**:
frontend UI for setting `drift_threshold_pct` or viewing past drift findings
(`frontend/src/components/pipelines-manager.tsx` not touched — deliberate
scope cut, same kind Sprint 7 made for model selection); drift findings are
not persisted to their own table (computed, formatted, delivered
best-effort, then discarded — no drift history to query later); no real
send was made against Resend/Slack (see above); `audit/models.py`'s
`runs.saved_pipeline_id` declaration still needs a human merge-conflict
resolution once Sprint 17 actually merges (both branches declare the same
column in the same file — expected, flagged, not silently overwritten by
either side).

## Sprint 29 — tenant budget cap (PR #63 merged 2026-08-20; migration `0009` applied to production 2026-08-21)

Per-tenant monthly LLM spend cap, enforced *before* enqueueing a new
execution (previously cost was only visible after the fact — Sprint 3/ADR-008).
New `users.monthly_budget_usd` column (migration `0009`, nullable — `NULL` =
no cap, zero behavior change for every existing tenant). `ADR-019` documents
the real trade-off: approach (a) — check spend already accumulated
(`SUM(analysis_runs.cost_usd)` this calendar month) and reject the *next*
run once at/over the cap — was chosen over approach (b) — estimating a
per-run cost ceiling from tenant history and blocking preemptively — because
real observed per-run costs (Sprint 8: ~$0.0006/run) make the "one run
overshoots the cap" edge case low-blast-radius for now; (b) is documented as
explicitly out of scope, to revisit if real per-run costs grow.

**Renumbered from ADR-017/migration `0007` to ADR-019/migration `0009`**:
Sprints 17, 14, and 29 were built in parallel and all three independently
claimed `ADR-017`/`0007`. Merge order settled as **17 → 14 → 29** — Sprint 17
kept `ADR-017`/`0007`, Sprint 14 became `ADR-018`/`0008` (`down_revision`
onto Sprint 17's `0007`, plus a real schema reconciliation for a
`runs.saved_pipeline_id` column both 17 and 14 had independently added), this
sprint became `ADR-019`/`0009` (`down_revision` onto Sprint 14's `0008`).
Confirmed pure renumbering, no schema reconciliation needed for this
migration specifically — it only ever touches `users`, and neither Sprint 17
(`runs`/`analysis_runs`) nor Sprint 14 (`saved_pipelines`, an index on
`runs`) touches that table. See ADR-019's "Addendum — renumbering" section
for the full local verification against both sprints' real migration files
(temporarily staged, not committed, same trick Sprint 14 used against
Sprint 17).

**Rebased onto `main` after Sprints 17 and 14 actually merged (real conflict,
resolved by hand)**: `execution_queue.py`'s `enqueue_analysis`/
`run_full_analysis_task` had been touched by all three sprints in parallel —
Sprint 17's `saved_pipeline_id` threading, Sprint 14's post-completion drift
check, and this sprint's budget/rate-limit gate + in-flight lock. Resolved
by keeping all three: budget and rate-limit stay *entry* checks (before
`.delay()`, budget first per the earlier fix), `saved_pipeline_id` still
flows through to `.delay()`/`run_full_analysis`, and the drift check still
runs as an *exit* action after completion — with the budget lock's release
placed in the `finally` immediately after `run_full_analysis` returns, ahead
of the drift check, so a drift/digest hiccup can never hold a tenant's
budget lock open past the run's own completion. `audit/db.py` had no real
overlap (different function names for different concerns) — confirmed via
`grep "^def " | sort | uniq -d` returning nothing. Full detail (including
the one test fixed post-rebase — a fake missing the `saved_pipeline_id`
kwarg Sprint 17 added) in ADR-019's "Addendum — rebase onto merged Sprints
17 and 14". Re-verified after rebase: full `pytest tests/unit` (531 passed,
including Sprint 17/14's own tests), and the concurrency guard re-confirmed
for real against a fresh Postgres + Redis, this time against the actual
merged `0007`/`0008` migrations rather than temporarily staged copies.

`services/execution_queue.py::check_budget_cap` (new) mirrors
`check_and_increment_rate_limit`'s shape but **deliberately does not**
mirror its Redis storage for spend itself — spend is read directly from
Postgres (`audit.db.get_monthly_spend_usd`), the already-canonical number
Sprint 3 computes, rather than duplicating it into a second Redis-resident
running total that could drift. **Checked before the rate limit, not
after** (post-PR-#63 code-review fix) — a budget rejection (`402`) must
never also consume a rate-limit slot (`429`) for a run that never executed.
A second, real concurrency bug from the same review — concurrent enqueues
for the same tenant could both read stale pre-execution spend and both
pass — is closed by reusing the rate limiter's own Redis `SET NX` atomicity
(a per-tenant "in-flight and unreconciled execution" lock, acquired only
when a cap is configured, released once the run's real cost lands or the
enqueue attempt itself fails); a Postgres compare-and-swap
(`claim_due_pipeline`'s pattern, Sprint 13) was considered and rejected —
it fits claiming one identified row, not gating an aggregate `SUM(...)`.
Full addendum in ADR-019. `services/scheduler.py` treats
`BudgetExceededError` exactly like `RateLimitExceededError` (release the
claim, retry next tick). New `GET/PATCH /budget` (self-service, same trust
model as `/pipelines`) exposes live `{cap_usd, spent_usd, ratio,
near_limit, exceeded}` status and lets a tenant set/clear their own cap —
the "alert before hitting the cap" surface (`near_limit` at 80% of cap by
default, `AI_ETL_BUDGET_WARNING_THRESHOLD_RATIO`), plus a
`logging.warning(...)` at enqueue time (not `log_action()` — no
`PipelineState` exists yet at enqueue-time, before a run has even started).

**Known limitation carried over from Sprint 3, not fixed here**:
`analysis_runs.cost_usd` (and therefore this cap) only covers the Agentic BI
layer's LLM cost (`save_analysis`) — a Silver-only run (no business
question) makes real Orchestrator/Transformer LLM calls with no cost row at
all today. Flagged in ADR-019, out of this sprint's scope.

**Verified locally** (Homebrew Postgres + a throwaway local Redis via
`brew install redis`, no Docker daemon in this environment): `alembic
upgrade head` (0001→0009, including Sprint 17's and Sprint 14's temporarily
staged real migrations) applied cleanly, `\d users` matched the new column
exactly alongside both other sprints' schema changes, `alembic downgrade -1`
cleanly dropped only this migration's column, re-`upgrade head` reapplied
cleanly. Concurrency fixes verified against real Postgres + real Redis: two
concurrent `enqueue_analysis` calls near a tenant's cap — exactly one
passes; a tenant already over cap making repeated calls never consumes a
rate-limit slot. `ruff check`/`format --check` clean; `mypy src/` clean (54
files); `pytest tests/unit` — 476 passed, 92% overall coverage;
`bandit`/`pip-audit` — only the two pre-existing, documented `exec()` sites
in `core/sandbox.py`, no new findings, no known CVEs.

**Not done in this session, flagged for the merge-checkpoint conversation**:
migration `0009` not applied to production Supabase (owner confirmation
required, same checkpoint discipline as Sprint 13's `0006`); no admin/billing
role exists, so the cap is self-service (tenant sets their own — ADR-019's
own "Known limitation"); no frontend UI consumes `GET /budget` yet (backend
contract only, same posture as Sprint 7's `GET /config`).

## Start here next session

**Orchestration session (2026-08-19/20): Sprints 8, 9, 10, 11, 12 were built in parallel via isolated worktree subagents, each opening its own PR without merging (explicit checkpoint).** Sprint 11 was later extended (same PR) with MySQL, MongoDB, and OAuth2 client-credentials at the owner's request. Sprint 23 (multi-provider LLM — Anthropic/Google/Ollama) was pulled forward out of roadmap order, also at the owner's explicit request, since its only dependency (Sprint 8) was already done.

**Merge sequencing decision:** PRs touching shared pipeline infrastructure (Sprint 11 — `extractor.py`/`orchestrator.py` dispatch; Sprint 12 — `extractor.py`/`sandbox.py`; Sprint 23 — `core/llm.py`; Sprint 10 — new AWS IaC) require explicit owner confirmation before merge even with CI green, per this session's standing rule. Sprints 9 and 8 (docs/scripts only, no shared-infra touch) merged immediately without that extra checkpoint. Merge order: 9 → 8 → 11 → 12 → 23 → 10, chosen by risk/blast-radius. **A local Git object-store corruption was hit mid-session** (multiple worktrees running concurrent Git operations against the same shared `.git/objects`) — recovered by cloning fresh from GitHub rather than attempting local repair; no data was lost since all branches were already pushed.

**ADR numbering conflict**: Sprints 10, 11, 12, and 23 were all built in parallel and each independently claimed `ADR-012`. Resolved by merge order — first to merge keeps ADR-012, the rest renumber sequentially at merge time (Sprint 11 → ADR-012 real, Sprint 12 → ADR-013, Sprint 23 → ADR-014, Sprint 10 → ADR-015).

**Sprint 12 (scale robustness, branch `feat/sprint12-scale-robustness`) is implemented, `make check` green.** Real profiling against a 204,000-row x 300-column synthetic benchmark (`case_study/data/generate_benchmark.py`, not committed — gitignored like the other case-study CSVs) confirmed BOTH points flagged going into this sprint:
1. `extractor.py::_extract_schema`'s raw per-row sample scaled unbounded with column count (38,837 chars / ~9,709 tokens for one 300-col source) — fixed by capping the sample to `MAX_SAMPLE_COLUMNS=20` (38.6% reduction, measured).
2. `core/sandbox.py`'s fixed per-call timeout did not scale with input size — representative Science-style code (a real `RandomForestRegressor` fit) **actually timed out** at the unscaled 20s budget against the 204k-row benchmark, reproduced directly, not hypothesized. Fixed via `scale_timeout_for_rows()` (doubles the timeout above 50,000 rows), applied at all 3 sandbox call sites (Transformer/Analyst/Science).

Full detail: `docs/adr/ADR-013-scale-strategy.md` (renumbered from ADR-012, see above), `docs/work/2026-08-19-sprint12-scale-profiling.md`. No real LLM call was made during this sprint's profiling — this environment has no `OPENAI_API_KEY` configured, so real end-to-end LLM-driven validation at 300-column scale (does the LLM actually produce working code against the now-compacted schema?) is an explicit, flagged open item for a follow-up session with real credentials.

Sprint 6 (ADR-011, real Next.js + Clerk + FastAPI frontend replacing Streamlit) is done, all 6 PRs merged, live-verified end-to-end twice (once against the interim `ai-etl-api` service, once again post-cutover against the consolidated `ai-etl` service).

**Final architecture**: Railway's `ai-etl` service (`ai-etl-production.up.railway.app`) now runs the FastAPI API directly (`uvicorn ai_etl.api.main:app`) — `app.py`/Streamlit are gone from the codebase (PR #49). The interim `ai-etl-api` service (created mid-session to unblock live verification without touching the working Streamlit deploy) was decommissioned once the cutover was confirmed live — `NEXT_PUBLIC_API_URL` on Vercel now points at `ai-etl`'s own domain.

**Two reusable deploy lessons from this session, both costly in time and worth reading before the next infra change:**

1. **Never diagnose a Clerk *development-instance* app (`pk_test_...` keys) with `curl`.** Clerk dev instances need a client-side JS handshake (a "dev browser" JWT) to bootstrap a session on any deployment domain — `curl` can never complete it and gets back what looks exactly like a platform 404 (`x-clerk-auth-reason: protect-rewrite, dev-browser-missing` in the response headers), even when the app is completely healthy for real browser users. This session repeated an identical misdiagnosis from 2026-08-17, deleting and recreating the Vercel `ai-etl` project three times chasing a non-bug. **Always verify with a real browser (`claude-in-chrome` for an agent) before concluding a Clerk-gated deployment is broken.** Full correction: Vault `bugs-solved/vercel-project-domain-404-fixed-by-recreate.md`.
2. **A Railway deployment reporting `FAILED` with zero deploy logs (build succeeds, image pushes, then nothing) can be a transient platform blip — retry once with no config changes before assuming something is actually broken.** All three services in this project (`ai-etl`, `ai-etl-api`, `tranquil-appreciation`) failed simultaneously this way right after PR #49 merged; `ai-etl-api` and `tranquil-appreciation` both succeeded on a bare `railway redeploy` with zero changes. `ai-etl` took several retries and two real-looking-but-ultimately-unnecessary config changes (explicit `deploy.builder: DOCKERFILE` — `railway redeploy` had been silently resolving via Railpack instead of the Dockerfile for one attempt, a genuine but separate issue; an explicit `deploy.startCommand` matching the Dockerfile's own `ENTRYPOINT`, functionally redundant but left in place) before a final plain retry succeeded — the same transient-blip pattern as the other two, not a real config bug. If this recurs: retry plain first, don't immediately reach for config surgery.

**Residual, harmless cleanup opportunity (not urgent)**: `ai-etl`'s Railway service config now has an explicit `deploy.startCommand` that duplicates the Dockerfile's `ENTRYPOINT` exactly — could be removed to let it fall back to the Dockerfile again, purely for tidiness, not correctness.

**Already de-risked ahead of time** (PR #43, merged 2026-08-17): `pyproject.toml`'s `plotly`/`scikit-learn`/`statsmodels` were misclassified under the Streamlit-only `app` extra — actually real pipeline runtime deps (`agents/analyst.py`/`science.py` use them inside the sandbox for charts/models). Fixed before it could cause a silent production regression during the cutover.

## Sprint 17 — comparable run history (PR #64 merged 2026-08-20; migration `0007` applied to production 2026-08-21)

Scope (Vault `artefact/product-roadmap-post-tcc.md`, Sprint 17): gives Sprint 14's drift
detection (running in parallel this same session) real substance — a way to compare runs of
the *same* saved pipeline over time, not just look at one run in isolation.

**Investigated first, per this project's own standard**: confirmed `runs`/`analysis_runs`
(Sprint 3/ADR-008) have no column linking a row back to the `saved_pipelines` (Sprint 13,
ADR-016) that produced it — `saved_pipelines.last_task_id`/`last_run_at` only remember the
single most recent fire, and nothing in `services/scheduler.py`'s call to `enqueue_analysis`
threads a pipeline identity into the execution at all. Confirmed gap, not an oversight to
route around — a real schema decision, formalized as **ADR-017**.

**New migration `0007`** (tested locally against a real throwaway Postgres, not applied to
production — same checkpoint discipline as migration `0006`): adds a nullable
`saved_pipeline_id` FK column to both `runs` and `analysis_runs`
(`ON DELETE SET NULL` — deleting a saved pipeline must never delete the runs it already
produced), each with its own index. Threaded through as a new optional keyword argument
(default `None`, every existing call site unaffected) across the whole chain:
`services/scheduler.py::check_scheduled_pipelines_task` → `execution_queue.enqueue_analysis`
→ `run_full_analysis_task` (Celery task kwarg) → `pipeline_service.run_full_analysis`/
`run_silver_pipeline` → `audit/db.py::save_run`/`save_analysis` →
`_write_run_row`/`_write_analysis_row`. Only the scheduler's own call site passes a real
value (its own `pipeline_id`); every avulso (`POST /runs`) run still reads back `NULL`, same
as every run created before this migration — no backfill is possible or attempted (see
ADR-017 for why).

**New**: `audit/db.py::list_pipeline_run_history(pipeline_id, tenant_id)` — tenant-scoped
time series (oldest first) of one saved pipeline's executions, `LEFT OUTER JOIN`ed onto
`analysis_runs` for cost/tokens/Gold-Science-subtask-count KPIs (same "no analysis, no cost"
`None` semantics as the existing `load_history`). New endpoint `GET /pipelines/{id}/history`
(404s the same way `GET /pipelines/{id}` already does for an unknown/unowned pipeline).
Frontend: `frontend/src/app/pipelines/[id]/historico/page.tsx` (new route, server-fetches the
pipeline for the header) + `frontend/src/components/pipeline-history.tsx` (new Client
Component) — a Plotly time-series chart (rows loaded / total tokens / cost, reusing the
existing `<PlotlyChart>` component, ADR-011/ADR-009's `{data, layout}` shape, built
client-side from the plain KPI rows rather than a backend-serialized figure, since this view
aggregates across runs rather than rendering one agent-generated chart) and a two-run diff
picker: the user marks two runs A/B from the list, the component fetches both via the
*existing* `GET /runs/{run_id}` (no new backend diff endpoint — deliberately, per ADR-017's
own scope note) and computes the diff client-side — Silver row count, Gold sub-task result
size (matched across runs by `task_question`), and Science `model_info` numeric fields
(matched the same way), each shown as a value → value delta with a colored, directional
(not good/bad — a metric moving isn't inherently positive or negative) indicator.
`pipelines-manager.tsx` gets a new "Histórico" button per saved pipeline linking to the new
route.

**Coordination with Sprint 14 (parallel session)**: this migration does not touch any
existing column, `load_history`'s signature/output shape, or `load_full_result` — only adds
one new nullable column per table and one new read-only query function/endpoint. Sprint 14's
own access pattern (whatever it queries for "most recent vs. previous run") is unaffected
either way.

Verified locally (no Docker daemon in this sandbox, same documented limitation as prior
sessions): `ruff check`/`format --check` clean on every touched file; `mypy src/` clean, no
hang this session; `pytest tests/unit tests/integration` — 459 passed, 14 skipped
(pre-existing DB/network-unreachable skip pattern), 92.02% overall coverage; `bandit`/
`pip-audit` — only the two pre-existing, documented `exec()` sites in `core/sandbox.py`, no
new findings, no known CVEs. The Alembic migration was verified for real against a throwaway
local Postgres (Homebrew `postgresql@17` binary, not Docker, same pattern as Sprint 13):
`alembic upgrade head` (0001→0007) applied cleanly, `\d runs`/`\d analysis_runs` matched the
column/index/FK definitions exactly, `alembic downgrade -1` cleanly dropped both columns/
indexes/constraints, re-`upgrade head` reapplied cleanly. Also verified by direct SQL: a real
insert with a `saved_pipeline_id` FK succeeds, and deleting the referenced `saved_pipelines`
row correctly `SET NULL`s the linked `runs` row instead of blocking the delete or cascading.
Frontend: `npm run lint` and `npm run build` both clean, including the new
`/pipelines/[id]/historico` route and its type-checked Plotly figure construction.

Full detail: `docs/adr/ADR-017-run-pipeline-linkage.md`.

**Not done in this session, flagged for the merge-checkpoint conversation**: migration `0007`
is not applied to production Supabase (checkpoint is explicitly pre-merge, same as `0006`);
no retroactive linkage for scheduled runs that fired before this sprint (impossible — the
data was never captured, see ADR-017); no dedicated backend diff endpoint (client-side diff
over two existing `GET /runs/{id}` calls was judged sufficient for this scope).

## Sprint 22 — dirty-data robustness (PR #61 merged 2026-08-20)

Scope: the Sprint 12 benchmark (204k×300) is synthetic and clean; this sprint tests
`sources/csv_source.py` against the kind of dirt a synthetic generator never reproduces.
Investigated first, per the sprint's own instructions, before writing any fix — direct
reproduction against a new versioned corpus (`tests/fixtures/dirty_data/`, 11 small files,
~56KB total, committed normally — unlike the Sprint 12 benchmark, small enough not to need
gitignoring) found three **real** bugs in `load_csv`, all fixed:

1. **Encoding** — Latin-1/Windows-1252/mixed-encoding files hard-crashed with a raw
   `UnicodeDecodeError`. Fixed: `charset_normalizer` (was already a transitive `requests`
   dependency, now declared directly in `pyproject.toml`) detects and decodes on UTF-8 failure;
   raises a specific, actionable `ValueError` only if detection itself can't find a confident
   candidate.
2. **Delimiter ambiguity** — `;`/tab-delimited files (common Brazilian/EU locale exports)
   didn't error at all under pandas' default `sep=","`: they silently returned a single
   column holding the whole raw line. Fixed: `csv.Sniffer` detects the real delimiter first.
3. **Malformed quoting / stray delimiter in an unquoted field** — the worst case found:
   pandas' C engine silently accepted a row whose field count didn't match the header and
   returned a **DataFrame with shifted, wrong values in every column, no exception at all**
   (confirmed against `csv_ambiguous_delimiter.csv`). Fixed: `_validate_row_lengths()`
   re-walks the raw text with the stdlib `csv` module and raises a specific, line-numbered
   `ValueError` instead of ever returning that DataFrame.

Excel (`.xlsx`, already handled by `load_csv` via `pandas.read_excel` before this sprint —
no new connector, no ADR) got equivalent treatment: a multi-sheet workbook now raises
(listing the sheet names) instead of silently reading only the first and dropping the rest;
a header row that isn't row 0 (title rows, blank spacer rows, or a merged title cell — which
`openpyxl`/pandas represent identically to a title row: one populated cell, `NaN` neighbors)
is located by a small heuristic (`_detect_header_row()`: first row that fills every column
with text-only values) instead of silently treating the title as column names. Both
ambiguities are also resolvable explicitly via new optional `sheet_name`/`header_row` fields
on a `csv`-type source, threaded through `extractor_node`'s dispatch (additive, backward
compatible — omitting them preserves the old default behavior for every existing
single-sheet, header-in-row-0 source).

JSON (deeply nested + irregular array/key shapes between records) was investigated too —
`rest_source.py`'s existing `pd.json_normalize` call already handled both correctly (dotted
column flattening, `NaN`-filled missing keys, no crash, no silent misalignment). No fix
needed there; added `tests/unit/test_rest_source_dirty_json.py` to pin that already-correct
behavior against the same corpus rather than leave it unverified.

No ADR: every fix stayed inside the existing `csv_source.py`/`rest_source.py` connectors —
hardening validation/parsing logic in modules that already existed, not new connector
architecture (per `.claude/specs/sr-standard.md`'s own criterion, and the ADR-010 precedent
it points to). Checked for the parallel Sprint 13 before starting: no `sprint13` branch or
open PR existed at the time of this session, so no ADR-numbering collision was possible to
avoid — none needed anyway since no ADR was created.

New: `tests/fixtures/dirty_data/` (corpus, 11 files), `tests/unit/test_csv_source_dirty_data.py`
(19 tests), `tests/unit/test_rest_source_dirty_json.py` (2 tests), 2 new tests in
`tests/unit/test_extractor.py` for the `sheet_name`/`header_row` passthrough.
`src/ai_etl/sources/csv_source.py` rewritten (was 11 lines, now has real encoding/delimiter/
row-length/header-detection logic); `src/ai_etl/agents/extractor.py` — 2-line additive change
to the `csv` dispatch branch. `pyproject.toml` — `charset-normalizer` promoted from transitive
to a declared base dependency.

**Post-open-PR code review caught a real regression, fixed same session**: `/code-review`
on PR #61 found that `_validate_row_lengths()` — the check added for the malformed-quote/
stray-delimiter bug above — re-walks the whole file a second time in pure-Python `csv.reader`
on top of pandas' own C-engine parse, with no size short-circuit, measurably doubling
`load_csv`'s CPU time at scale (the exact metric Sprint 12/ADR-013 optimized). Fixed with a
row-count-based fast path: below `_LARGE_FILE_ROW_THRESHOLD=50_000` lines (matching Sprint
12's own `LARGE_DATASET_ROW_THRESHOLD` convention in `core/sandbox.py`) every row is still
validated, unchanged; above it, only a bounded leading sample (`_VALIDATION_SAMPLE_ROWS=5_000`)
is checked — an explicit, documented trade-off (a malformed row past the sample boundary in a
very large file is no longer guaranteed to be caught), not a silent one. **Measured, not
assumed**: the official Sprint 12 200k-row benchmark generator hit this session's
already-documented iCloud-sync I/O stall (`~/Documents` — see Known risks below) and was
killed after 20+ minutes with no reliable progress; substituted with a 250k-row × 40-col CSV
generated directly in the non-iCloud scratch path (8s to generate) for the timing comparison
instead — a valid substitute since the regression is a generic O(rows) cost, not
dataset-content-specific. Results: pure `pandas.read_csv` floor ≈2.09s; **after** the fix
≈2.29s (~10% overhead); **before** the fix (full second pass, simulated by disabling the
threshold) ≈4.30s (~2.06×) — confirms both the review's "doubles CPU time" finding and that
the fix restores near-baseline performance. New tests: 3 more in
`tests/unit/test_csv_source_dirty_data.py` (malformed row caught within the sample on a large
file; malformed row past the sample boundary correctly *not* caught, the documented trade-off;
small-file behavior unchanged) — 22 tests total in that file, `csv_source.py` now at 99%
coverage.

Verification this session (including the post-review fix): `ruff check`/`format --check`
clean on every touched file (pre-existing findings only in untouched
`case_study/baselines/*.ipynb`/`generate_*.py`, unrelated); `mypy src/` clean (49 files, no
hang this session); `pytest tests/unit tests/integration`
— 414 passed, 14 skipped (pre-existing DB/network-unreachable skip pattern), 91% overall
coverage, `csv_source.py` at 99% (well above the 70% adapter floor); `bandit`/`pip-audit`
— only the two pre-existing, documented `exec()` sites in `core/sandbox.py` and one
pre-existing `assert` in `api/deps.py`, no new findings, no known CVEs (including the new
`charset-normalizer` dependency). `tests/e2e` not run locally (same Postgres/Redis
unavailability as every prior session) — CI is the real gate.

## Sprint 13 — scheduled (recurring) pipelines (PR #62 merged 2026-08-20; migration `0006` applied and `celery-beat` deployed to production the same day)

New `saved_pipelines` table (migration `0006`, ADR-016) — a persisted spec +
cron schedule + tenant, distinct from `runs`/`analysis_runs` (still one row
per *execution*, avulso or scheduled). Fired by a new Celery beat entry
(`core/celery_app.py::beat_schedule`, `services/scheduler.py::
check_scheduled_pipelines_task`, `AI_ETL_SCHEDULER_INTERVAL_SECONDS`, default
60s) that reuses the existing `execution_queue.enqueue_analysis()` — the same
function `POST /runs` calls — so a scheduled run is audited identically to
an avulso one and still respects the per-tenant rate limit. **Requires a new
Celery beat process in production** (same Docker image as the existing
worker, different Custom Start Command: `celery -A ai_etl.core.celery_app
beat`) — not yet deployed to Railway, since the PR isn't merged.

**ADR-016 Decision 3 (data-model decision other sprints inherit)**: only
"live" source types — `postgres`/`sqlite`/`mysql`/`mongodb`/`rest` — can be
scheduled, never `csv`/`document` (browser uploads). Enforced by an explicit
`source_type` field on `saved_pipelines`, validated against an allowlist at
`POST /pipelines`/`PATCH /pipelines/{id}` time — deliberately **not** an LLM
round-trip through the Orchestrator (would add cost/latency/an
`OPENAI_API_KEY` dependency to a plain CRUD request for a check that should
be deterministic).

New: `docs/adr/ADR-016-scheduled-pipelines-data-model.md`,
`alembic/versions/0006_saved_pipelines.py`, `src/ai_etl/core/scheduling.py`
(cron validation/next-fire-time via `croniter`, new dependency),
`src/ai_etl/core/paths.py` (shared `RUNS_DIR`, re-exported from
`api/config.py`), `src/ai_etl/services/scheduler.py`,
`src/ai_etl/api/routers/pipelines.py` (`GET/POST /pipelines`,
`GET/PATCH /pipelines/{id}`), CRUD functions in `audit/db.py`
(`create_saved_pipeline`/`list_saved_pipelines`/`get_saved_pipeline`/
`update_saved_pipeline`/`list_due_pipelines`/`claim_due_pipeline`/
`release_pipeline_claim`/`record_pipeline_run`). Frontend:
`frontend/src/app/pipelines/page.tsx` + `frontend/src/components/
pipelines-manager.tsx` — minimal create/pause/resume/edit UI, reuses
existing shadcn `Card`/`Button`/`Input`/`Textarea` (no new shadcn component
installed; `source_type` is a plain native `<select>`).

**Post-PR code review (`/code-review` on PR #62) caught two real
concurrency bugs, both fixed same-session, before merge — see ADR-016's
"Addendum" section for full detail:**
1. **Duplicate fires from overlapping Celery beat ticks.** The first cut
   only advanced `next_run_at` *after* `enqueue_analysis` succeeded; an
   overrunning tick (many due pipelines, a slow Redis check, worker
   backlog) would let the next tick see the same pipeline as still due and
   fire it twice. Fixed with `claim_due_pipeline` — a `next_run_at`
   compare-and-swap `UPDATE`, executed *before* `enqueue_analysis`, so only
   one overlapping tick can win a given due fire; the loser skips it,
   no duplicate run. `release_pipeline_claim` reverts the claim if
   `enqueue_analysis` then fails, so the pipeline retries next tick rather
   than waiting a full cron period.
2. **Schedule drift.** The replacement `next_run_at` was computed from
   `datetime.now()` at tick time, not from the pipeline's own previous
   `next_run_at` — a "every minute" cron under load would drift later on
   every fire. Fixed by passing the pipeline's pre-claim `next_run_at` as
   `compute_next_run_at`'s base.
3. **Minor**: `services/scheduler.py` duplicated the `"./runs"` literal
   instead of sharing `api/config.py`'s `RUNS_DIR` — fixed by extracting the
   constant to `core/paths.py` (services/core sit below api/ in this
   project's layering, so services/ imports from core/, not api/;
   `api/config.py` now re-exports it unchanged for existing call sites).

**Verified locally** (no Docker daemon in this sandbox, consistent with
prior sessions' documented limitation): `make check`'s pieces run directly —
`ruff check`/`format --check` clean, `mypy --strict` clean (no local hang
this session), `pytest tests/unit` 422 passed, 92% coverage; `bandit`/
`pip-audit` clean, no new findings. The Alembic migration was verified for
real against a throwaway local Postgres (Homebrew `postgresql@17` binary,
not Docker): `alembic upgrade head` (0001→0006) applied cleanly, `\d
saved_pipelines` matched the table definition exactly, `alembic downgrade
-1` cleanly dropped the table, and re-`upgrade head` reapplied cleanly. The
sprint's "3 consecutive fires, no manual intervention" definition of done
was re-exercised against that same real Postgres **after the concurrency
fix** (3 sequential real fires each still advance `next_run_at` and record a
new `last_task_id`, unchanged from before the fix), then extended with two
scenarios targeting the review's findings directly:
- **Overlapping-tick duplicate-fire guard**: forced a pipeline due, then
  called `check_scheduled_pipelines_task` twice back-to-back with no
  re-forcing in between (simulating a second tick starting before the first
  finished) — `enqueue_analysis` was called exactly once, not twice. The
  first call's claim atomically advanced `next_run_at` past "now" as part of
  firing, so the second call's own `list_due_pipelines()` read no longer
  saw the pipeline as due at all — the same guarantee a losing
  `claim_due_pipeline` compare-and-swap gives if a second tick's read
  happens to race in between (covered directly, without needing real
  thread/process concurrency, by `test_claim_due_pipeline_is_a_compare_and_
  swap_second_caller_loses` in `tests/unit/test_saved_pipelines_db.py`).
- **Rate-limit release-and-retry**: forced a pipeline due, made
  `enqueue_analysis` raise `RateLimitExceededError` — the tick reported it
  skipped, and `next_run_at` was confirmed back at its original due time
  (not advanced) via `release_pipeline_claim`; a following tick with
  `enqueue_analysis` succeeding fired it normally, proving a rate-limited
  scheduled pipeline is retried on the very next tick rather than silently
  waiting a full cron period.

Frontend: `npm run lint` and `npm run build` both clean with
the new `/pipelines` route included.

**Not done in this session, flagged for the merge-checkpoint conversation**:
Celery beat isn't yet deployed as a Railway service (no new Railway
provisioning was done — checkpoint is explicitly pre-merge); no real
end-to-end fire was observed against a live Redis/Celery worker (sandbox has
no Docker daemon); Stripe/billing per saved pipeline is out of scope
(ADR-016's own Consequences section).

## Sprint 8 — model comparison + stability (PR #57 merged 2026-08-20)

CLI/headless only, per scope — no `api/` or `frontend/` touched. New:
`case_study/scripts/model_comparison.py` (runs Scenario 1 N times per model
via `pipeline_service.run_full_analysis`, bypassing Celery/Postgres — see the
script's own docstring for why — recording real `core/pricing.py` cost, real
`stage_durations` latency, and a new objective `score_quality()` metric),
`tests/unit/test_model_comparison.py` (16 tests, the script's pure helpers),
`case_study/results/sprint8/` (`README.md` full report, `comparison_runs.csv`,
`stability_summary.json`).

**This session had no `OPENAI_API_KEY` and no `ollama` binary** (confirmed,
not assumed) — every run this session used mocked/simulated LLM calls,
clearly tagged `data_source` in the output (`mock`/`simulated`, never `real`).
No fabricated cost/latency/model-comparison numbers were produced — the
`README.md` is explicit that this session validated the harness (real
pipeline execution, real cost formula, real latency instrumentation, real
quality scoring) end to end, not "which model wins." A rerun with a real key
(and Ollama installed) needs zero code changes to produce real numbers.
Scenarios 2-4 also weren't executed (need Postgres, unreachable — no Docker
daemon in this sandbox) — recorded as explicit `skipped_no_infra` rows, not
silently omitted. No ADR: adding a real Ollama provider to `core/llm.py`
(currently `ChatOpenAI`-only) would be one, but was deliberately not
implemented untested with no Ollama available to verify against — flagged in
the report for a future session.

Verification: `ruff check`/`format --check` clean on all touched files (21
pre-existing findings in `case_study/baselines/*.ipynb`, untouched);
`mypy --strict` clean on `src/` + new files (no local hang this session);
`pytest tests/unit` — 292 passed; `bandit`/`pip-audit` — only the two
pre-existing, documented `exec()` sites in `core/sandbox.py`, no new
findings, no known CVEs. `tests/integration`/`tests/e2e` not run locally
(same Postgres/Redis unavailability) — CI is the real gate.

## Sprint 7 — frontend redesign + feature parity (PR #52, merged 2026-08-19)

**Scope grew mid-session on owner request.** The plan going in (Vault `artefact/sprint-roadmap.md`) was "visual layer only, no API contract or business-logic change." Partway through, the owner asked for feature parity with the old Streamlit sidebar/tabs too — agent explanations, which model is running, and a working live-progress view with per-agent code/charts. That needed three small, additive, backward-compatible backend changes; each is called out below as a deliberate scope exception, not scope creep left undocumented.

**Visual redesign** (the original plan): shadcn/ui (`base-ui` primitives, `base-nova` style) initialized over the existing Tailwind v4 setup; dark mode by default (shadcn's own guidance for dashboard/AI product surfaces); Motion for the polling status card and a staggered entrance on the run-detail page; Histórico's card-list replaced with a shadcn `Table`; Plotly charts given dark-friendly transparent/gridline defaults. Fixed a self-referential `--font-sans: var(--font-sans)` left by `shadcn init` that would have silently broken the Geist Sans font.

**Feature parity additions** (backend, small/additive/read-only or opt-in — no existing field removed or changed):
- `GET /config` (new, `api/main.py`) — read-only current model name (`AI_ETL_LLM_MODEL`). Mirrors the old Streamlit sidebar's caption; *choosing* a model per run is real business logic (allowlist, cost-tracking, ~6 `get_llm()` call sites) and was deliberately left out of this sprint — a candidate for its own future item if wanted.
- `services/execution_queue.py` — `pipeline_service.py`'s existing `progress_callback` hook (previously wired to a no-op, "no progress_callback crosses the task boundary" was explicitly documented as out of scope in Sprint 3) now reports through Celery's own `update_state(state="PROGRESS", meta=...)` — no new infra, same Redis result backend already in use. `get_task_status()` returns the latest `{stage, message}` while a task is running; the frontend's existing 2s poll surfaces it live.
- `audit/db.py::_serialize_analysis_result` now persists Gold/Science generated code (the `"code"` key `agents/analyst.py`/`science.py` already produce). **This was a real pre-existing bug, not new scope**: the old Streamlit's "Código Gold/Science" tab read this same key, but nothing had ever written it to storage past Sprint 3's move to async execution — silently broken for roughly half the project's history, never noticed because no test exercised a completed-and-reloaded run's code field. Fixed here, verified live (a Science Agent's generated `science_1.py` renders correctly in Histórico's run detail post-fix).
- Also fixed in passing: the old Streamlit's per-agent timing table read a `state["_agent_timings"]` key that was **never actually set anywhere in `PipelineState`** — dead code since it was written; the real field is `stage_durations`. The new "Pipeline" tab uses the real field and shows real per-node timings.

**New frontend surface**: `agents-info.tsx` (Sheet, "Como funciona" — pyramid + agent list + current model, replaces the always-visible Streamlit sidebar with an on-demand panel), `agent-progress.tsx` (4-phase live stepper: Silver → Planner → Gold/Science → Advisor), `pipeline-tab.tsx` + `code-tab.tsx` + `code-block.tsx` (new "Pipeline"/"Código" tabs on the run-detail page).

**Live-verified in production (2026-08-19)**, twice — once against the PR's Vercel preview (old backend, confirmed every new UI piece degrades gracefully when the new backend fields aren't there yet), once against `ai-etl.vercel.app` post-merge (new backend live): real Clerk login (Google OAuth, no password ever handled by the agent), two real uploads (`sales.csv`, `orders.csv`) with different business questions, real Celery+LLM execution, live per-agent progress messages streaming during the run ("Planner — decompondo...", "Science Agent — Treinando modelo..."), model badge showing `gpt-4o-mini`, Histórico table, and the run-detail Pipeline/Código tabs — including a real Science Agent code block rendering post-fix.

**CI caught one real regression before merge**: the progress-callback change added a new kwarg to `run_full_analysis`'s call site; two existing unit tests (`tests/unit/test_execution_queue.py`) had fakes with the old fixed signature and broke. Fixed by widening both fakes to accept the new kwarg (matches the real function's signature) — not a design problem, just tests needing to catch up.

**Also touched mid-session, unrelated to the redesign itself**: a genuine Railway platform incident (`status.railway.com/incident/YYU63JUO`, deployments stuck `QUEUED` platform-wide for ~40 min) blocked live verification twice; diagnosed via `railway-agent`, not a real config issue, resolved on its own. `API_ALLOWED_ORIGINS` was temporarily widened to include two Vercel preview URLs for pre-merge verification, then reverted to the single production origin once verification moved to production.

## Confirmed state (branch `main`, PRs #37-#43 merged 2026-08-17)

- **Sprint 6, PRs 1-5 done and live-verified (ADR-011)** — real frontend replacing the pasted-Clerk-token Streamlit login. `src/ai_etl/api/` (new FastAPI layer, PR #37): `GET/POST /runs`, `GET /runs/{task_id}/status`, auth via the same unchanged `services/auth_service.verify_session_token()`. `frontend/` (new Next.js 15 + `@clerk/nextjs` app, PR #38, downgraded from a Next.js 16 scaffold in PR #39 — see below): `middleware.ts` gates every route via Clerk, `/` is the real "Executar" page (PR #41) — upload/manual-spec + business question, `POST /runs`, client-side poll of `/status` every 2s with `getToken()` called fresh on every request (no stale-token dead end). `/historico` + `/historico/[runId]` (PR #42) — run list + detail, Gold/Science/Advisor results, Plotly.js charts reading `api/serialization.py`'s `fig.to_plotly_json()` output directly (same schema `storage.py`'s `_fig.json` already persists, ADR-009). Deploys: API live on Railway (`ai-etl-api-production.up.railway.app`, PR #46 fixed the missing `fastapi` runtime dep); frontend deploys to Vercel (`ai-etl.vercel.app`), auto-deploying from `main` (reconnected 2026-08-18 after 3 project recreations chasing a false-positive `curl`-vs-Clerk-dev-instance signal — see "Start here next session" above). Streamlit (`app.py`) still live on Railway, unchanged — not retired until Sprint 6's PR 6 cutover (see "Start here next session" above). Real end-to-end run verified live 2026-08-18: upload → Celery execution → completed → Histórico → Plotly chart, all real (no mocks).
- **Real, non-trivial Vercel deployment troubleshooting** (PR #39 + a same-day Vercel-dashboard-only fix, no code change) — two stacked, unrelated problems: (1) Next.js 16's `middleware.ts`→`proxy.ts` rename broke Vercel's edge routing (fixed: downgraded to `next@15.5.23`); (2) after that fix, the project's domain still 404'd for reasons never conclusively diagnosed from the dashboard/API (owner's authenticated session got 404 where an anonymous session correctly hit Clerk's login — backwards from expected) — resolved only by **deleting and recreating the Vercel project**, setting Root Directory to `frontend` as the first action this time. Full writeup: Vault `bugs-solved/vercel-project-domain-404-fixed-by-recreate.md`.
- Extracted `services/spec_builder.py::auto_generate_spec()` out of `app.py` (pure function, no Streamlit dependency) so both the Streamlit UI and the new `POST /runs` endpoint share one implementation instead of duplicating the Transformer prompt-construction logic.
- Root `README.md` rewritten with a "Project structure" section explaining the monorepo layout (Python backend at root, `frontend/` Next.js app, independent Railway/Vercel deploys) — **physically splitting the backend into its own `backend/` directory is a deferred follow-up** (owner's call), once the Sprint 6 frontend cutover is fully live; not done now to avoid reconfiguring Railway's deploy Root Directory mid-sprint.
- **Owner direction (2026-08-17, not yet scheduled to a sprint): source connectors should go well beyond Postgres** — more database engines, more REST API patterns, and other connector types the agent judges worth adding. Recorded for future roadmap planning; `sources/` today is `csv_source.py`/`postgres_source.py`/`rest_source.py`/`document_source.py` (ADR-010) only.
- **Working agreement, 2026-08-17: batch local changes into fewer CI-triggering pushes** — CI minutes cost money on this account. Verify as much as possible locally (ruff, `ast.parse` sanity checks, `npm run build`/lint, manual review — same substitutes already used for the mypy/pytest local-sandbox-hang bug) before the first push of a PR; accumulate further related fixes locally rather than pushing once per individual CI finding.

## Confirmed state (Sprint 4/5, branch `main`, PRs #34/#35 merged 2026-08-16)

- **Sprint 5 complete (PR #35, ADR-010)** — `sources/document_source.py` adds PDF/DOCX as a 4th source type (`pypdf`/`python-docx` text extraction + LLM structuring into rows, same retry-loop shape as `orchestrator_node`), wired into `extractor_node`'s dispatch and the Orchestrator's source-type schema — no new graph agent (matches the roadmap's prior no-new-agent decision). `tests/e2e/` (previously empty) now has all 4 case-study scenarios (CSV / CSV+Postgres / CSV+Postgres+REST / +PDF+DOCX) running through `enqueue_analysis` against the real stack — real Postgres, real Celery task round-trip (`task_always_eager`, still through `.delay()`/JSON serialization), real sandboxed transform execution, real Clerk-JWT-shaped auth (fake JWKS, no network call to Clerk). Only LLM calls are mocked. CI got a new, deliberately isolated `e2e` job (its own Postgres/Redis service containers) — see Known risks below for why it's isolated from the main `check` job.
- **Sprint 4 complete (PR #34, ADR-009), verified live** — `audit/storage.py` adds a `StorageBackend` abstraction (`LocalStorageBackend`, unchanged default behavior; `S3StorageBackend` via `boto3`, keys prefixed `{AI_ETL_ENV}/{tenant_id}/...`), selected via `STORAGE_BACKEND` (`local` default, `s3` opt-in). `audit/db.py`'s `save_run`/`save_analysis`/`load_full_result` route through it instead of raw `pathlib`/`pandas` file I/O. Live-verified: bucket `ai-etl-artifacts-brlla` (`sa-east-1`), a real production run wrote all 6 expected artifacts under `prod/{tenant_id}/...`. `mypy` caught one real `no-any-return` finding (`S3StorageBackend.read_bytes`), fixed with `cast`.

- 5-agent LangGraph "Silver" pipeline (Orchestrator → Extractor → Transformer → Quality → Loader) + 4-agent "Agentic BI" layer (Planner → Analyst/Gold, Science → Advisor) — both fully implemented and exercised by the case study (15 runs, 100% success) and by the Streamlit app (`app.py`).
- **Real authentication (Clerk) and account-based tenancy (Supabase Postgres) are live**, both in code and on a real Railway deployment (Sprint 1, PR #18, merged 2026-08-13; deploy debugged and confirmed working 2026-08-14). `runs`/`analysis_runs.tenant_id` are `NOT NULL` foreign keys to a new `users` table, keyed by Clerk `user_id` — the PR #16 session-UUID stopgap is fully retired.
- **The `exec()` sandbox is now unified** (Sprint 2, PR #23, ADR-007) — `core/sandbox.py` is the single call site for Transformer/Analyst/Science, running in a `multiprocessing.Process` (spawn context) with a real enforced timeout (30s/15s/20s respectively) and `os.environ.clear()` in the child before user code runs. `SECURITY.md`/ADR-003 are now stale on this point (still describe 3 separate sites) — worth a follow-up doc pass. The introspection-escape limitation (`().__class__.__mro__[1].__subclasses__()`) remains open, unchanged, still accepted for TCC scope.
- Per-stage latency instrumentation live: `stage_durations` on `PipelineState`, persisted to a `stage_latencies` table (migration `0004`, applied to production 2026-08-15) via `save_stage_latencies()` — feeds the evaluation-metrics framework (`artefact/evaluation-metrics.md` in the Vault).
- **Sprint 3 complete (PR #27, ADR-008)** — pipeline/analysis execution is now asynchronous via Celery + Redis (`core/celery_app.py`, `services/execution_queue.py`); `app.py` enqueues and polls instead of blocking. Per-tenant rate limiting uses a fixed-window counter directly on Redis (Celery's own `rate_limit` is global per task type, not per tenant — a deliberate divergence from ADR-008's initial sketch, documented inline). Cost per execution (`core/pricing.py`, migration `0005` — `model_name`/`cost_usd` on `analysis_runs`, applied to production 2026-08-15) is now visible in the History tab. Full results (DataFrames, Plotly figures) are persisted as CSV/JSON artifacts alongside the existing lossy JSON audit log, and reloaded via `load_full_result()` to re-render the complete results UI (`_render_results`) after an async run completes or from History — `load_full_result` enforces a server-side `tenant_id` ownership check (added in security review) rather than relying solely on the UI only ever offering a tenant's own `run_id`s.
- Dependencies: `boto3` (Sprint 4); `pypdf`, `python-docx` (Sprint 5); `celery`, `redis` (Sprint 3); `gitpython` bumped to 3.1.59 (cleared 15 CVEs), `pandas`/`pandas-stubs` bumped to `<4.0.0` (Dependabot #13/#14), `pyjwt[crypto]>=2.13.0` added (Sprint 1) — all merged.

## Changed files (2026-08-19 — Sprint 12: scale robustness, PR open not merged)

- `docs/adr/ADR-012-scale-strategy.md` (new) — real profiling against a 204k-row x 300-col
  synthetic benchmark; both flagged points confirmed as real bottlenecks and fixed.
- `docs/work/2026-08-19-sprint12-scale-profiling.md` (new) — full raw profiling numbers.
- `case_study/data/generate_benchmark.py` (new) — configurable heterogeneous benchmark
  dataset generator (default 200k rows x 300 cols), reusing `generate_sales.py`/
  `generate_orders.py`'s seed/null/outlier/duplicate-injection patterns. Not committed
  (gitignored, like the other case-study CSVs) — script is the versioned artifact.
- `case_study/data/profile_scale.py` (new) — profiling harness: real extractor schema-size
  measurement, real `execute_in_sandbox()` timing for representative Transformer/Analyst/
  Science-style code (no LLM calls — see ADR-012 for why), real `quality_node` timing.
- `src/ai_etl/agents/extractor.py` — `_extract_schema()`'s raw sample capped to
  `MAX_SAMPLE_COLUMNS=20` columns; adds `null_ratio`/`sample_truncated` keys. Additive,
  backward-compatible — no-op for every source narrower than 20 columns (every existing
  case-study source).
- `src/ai_etl/core/sandbox.py` — new `scale_timeout_for_rows()` helper (doubles the
  timeout above `LARGE_DATASET_ROW_THRESHOLD=50,000` rows); `execute_in_sandbox()`'s own
  signature/contract unchanged.
- `src/ai_etl/agents/transformer.py`, `analyst.py`, `science.py` — each calls
  `scale_timeout_for_rows()` once per `run_*()`/`transformer_node()` call, using the real
  row count of the DataFrame(s) about to be sandboxed.
- `src/ai_etl/core/state.py` — `source_schemas` docstring updated to match the new schema
  shape.
- `tests/unit/test_extractor.py`, `test_sandbox.py`, `test_analyst.py`, `test_science.py`,
  `test_transformer.py` — new tests for the schema cap and timeout scaling (happy path +
  no-op-below-threshold + real large-scale trigger). Full suite: 292 passed, 8 skipped
  (pre-existing DB-skip pattern), 90.35% coverage. `make check` (ruff/format/mypy/tests/
  bandit/pip-audit) green — mypy/pytest ran fine directly this session (no sandbox hang
  encountered), unlike some prior sessions' documented workaround.
- **Not changed**: `execute_in_sandbox()`'s public signature/contract (ADR-007) — the new
  helper is opt-in at call sites, not a change to the shared sandbox itself. No LangGraph
  node signature touched beyond `extractor_node`'s existing `(state) -> state` contract
  (unchanged, only its internal `_extract_schema()` helper changed).
- **PR open against `main`, deliberately NOT merged** — see "Start here next session" above.

## Changed files (2026-08-18 — Sprint 6 PR 6: cutover, Streamlit retired)

- `Dockerfile` (PR #49) — `ENTRYPOINT` swaps from `streamlit run app.py` to `uvicorn ai_etl.api.main:app`; only installs the `api` extra now.
- `app.py`, `tests/unit/test_app.py` — removed.
- `pyproject.toml` — `app` optional-dependencies group (streamlit) removed; `uv.lock` regenerated (streamlit and transitive deps dropped, 148 packages resolved vs. ~214 before).
- `Makefile` — `app` target (Streamlit) removed.
- `docker-compose.yml` — `app` service renamed `api`, runs the same Dockerfile/uvicorn instead of Streamlit.
- `README.md` — two stale Streamlit references updated.
- Railway: `ai-etl` service's Dockerfile `ENTRYPOINT` now serves the API directly; interim `ai-etl-api` service decommissioned; `API_ALLOWED_ORIGINS` moved to `ai-etl`. Vercel: `NEXT_PUBLIC_API_URL` re-pointed at `ai-etl`'s own domain.

## Changed files (2026-08-18 — Sprint 6 live-verification: Railway API, Vercel fix, e2e)

- `Dockerfile` (PR #46) — `uv sync --no-dev --no-editable --extra app --extra api`, was missing `--extra api` (`fastapi`/`uvicorn`), so the new API service crashed with `ModuleNotFoundError` on first deploy.
- `.gitignore` — `.vercel`, `.env*` added at repo root (Vercel CLI's own auto-edit when linking from the repo root, not just `frontend/`).
- No other application code changed today — this session was infra (Railway API service, Vercel project reconnect/domain fix) and live verification, not feature work.

## Changed files (2026-08-17 — Sprint 6 in progress: FastAPI + Next.js/Clerk frontend)

- `docs/adr/ADR-011-nextjs-frontend-fastapi-clerk-middleware.md` (new).
- `docs/work/2026-08-17-sprint6-frontend-nextjs-clerk-fastapi.md` (new) — full implementation plan, 6-PR sequencing, Vercel troubleshooting postscript.
- `src/ai_etl/api/` (new) — `main.py` (FastAPI app + CORS), `deps.py` (`get_current_tenant_id`), `serialization.py` (JSON-safe `load_full_result()`/DataFrame conversion, `nan_to_none_records`), `routers/runs.py` (`GET/POST /runs`, `GET /runs/{task_id}/status`).
- `src/ai_etl/services/spec_builder.py` (new) — `auto_generate_spec()`, extracted from `app.py`.
- `pyproject.toml` — new `api` extra (`fastapi`, `uvicorn[standard]`, `python-multipart`).
- `frontend/` (new) — Next.js 15.5.23 + `@clerk/nextjs` v7 app. `src/middleware.ts` (Clerk route gating), `src/app/page.tsx` + `src/components/executar-form.tsx` (the real "Executar" page), `src/components/auth-header.tsx` (`useUser()`-based header — `<SignedIn>`/`<SignedOut>` don't exist in Clerk v7 "Core 3").
- `.github/workflows/frontend-ci.yml` (new) — lint/build, scoped to `frontend/**` paths only.
- `.gitguardian.yaml` (new, Sprint 5 carryover) — ignores a known test-credential false positive.
- Root `README.md` — rewritten, "Project structure" section.
- `tests/unit/test_api_deps.py`, `test_api_runs.py`, `test_spec_builder.py` (new).
- `frontend/src/app/historico/page.tsx`, `historico/[runId]/page.tsx` (new) — run list + detail.
- `frontend/src/components/plotly-chart.tsx`, `data-table.tsx`, `analysis-section.tsx`, `frontend/src/lib/api.ts`, `types.ts` (new).
- `pyproject.toml` — `plotly`/`scikit-learn`/`statsmodels` moved from the `app` (Streamlit-only) extra to base `dependencies` — they're real pipeline runtime deps (`agents/analyst.py`/`science.py`'s sandbox `extra_modules`), misclassified since before this sprint; caught ahead of PR 6's planned extra removal, not a live bug.

## Changed files (2026-08-16 — Sprint 5: PDF/DOCX source + `tests/e2e/`)

- `docs/adr/ADR-010-document-source-pdf-docx.md` (new) — document connector as a `sources/` module (no new agent), LLM structuring lives inline in the connector.
- `src/ai_etl/sources/document_source.py` (new) — `load_document()`: `pypdf`/`python-docx` text extraction, LLM-structured rows via `get_llm()` with a 3-attempt retry loop.
- `src/ai_etl/agents/extractor.py`, `agents/orchestrator.py` — `document` wired into the source-type dispatch and the Orchestrator's prompt schema.
- `tests/e2e/conftest.py` (new) — shared fixtures: DB/Redis reachability skip, Celery eager-mode, fake-JWKS Clerk token minting, `mock_pipeline_llm` (patches every LLM call site `run_full_analysis` touches — Orchestrator/Transformer/Planner/Analyst/Advisor — see Known risks for why all five are needed even for a business-question-less run).
- `tests/e2e/test_scenario{1,2,3,4}_*.py` (new) — the 4 case-study scenarios, run through `enqueue_analysis`.
- `.github/workflows/ci.yml` — new `e2e` job (own Postgres/Redis `services:`), deliberately not folded into the `check` matrix.
- `.gitguardian.yaml` (new) — ignores a GitGuardian false-positive on the e2e job's throwaway test-Postgres password (identical value already committed in `docker-compose.yml`, predating GitGuardian on this repo).
- `CONTRIBUTING.md` — `feature/` → `feat/` branch-prefix fix.

## Changed files (2026-08-16 — Sprint 4: S3 storage)

- `docs/adr/ADR-009-tenant-scoped-storage-and-config.md` (new).
- `src/ai_etl/audit/storage.py` (new) — `StorageBackend` protocol, `LocalStorageBackend`, `S3StorageBackend`, `get_storage_backend()`.
- `src/ai_etl/audit/db.py` — `save_run`/`save_analysis`/`load_full_result`/`_serialize_analysis_result`/`_reload_analysis_entry` now route through the selected backend; `save_run`/`save_analysis` return a storage key (`str`) instead of a `Path` (no caller used the old return value as a `Path`).
- `.env.example` — 6 new S3 vars documented, commented (opt-in).

## Changed files (2026-08-15 — Sprint 3: async execution, rate limiting, cost per run)

- `docs/adr/ADR-008-async-execution-celery-redis.md` (new) — Celery+Redis over RQ/Arq, rationale and consequences.
- `src/ai_etl/core/celery_app.py` (new) — Celery app factory/config.
- `src/ai_etl/services/execution_queue.py` (new) — `enqueue_analysis()`, `get_task_status()`, `run_full_analysis_task` (Celery task wrapping `pipeline_service.run_full_analysis`), fixed-window per-tenant rate limiter on Redis.
- `alembic/versions/0005_analysis_cost_tracking.py` (new) — `model_name`/`cost_usd` on `analysis_runs`, applied to production.
- `src/ai_etl/core/pricing.py` (new) — `compute_cost_usd()`.
- `src/ai_etl/audit/db.py` — `save_run`/`save_analysis` now also persist reconstructable artifacts (Silver DataFrame as CSV; Gold/Science DataFrames as CSV, figures via `fig.to_json()`); new `load_full_result()` (with tenant ownership check) and `_run_belongs_to_tenant()`.
- `app.py` — enqueues via `execution_queue` and polls instead of blocking; History tab calls `load_full_result()` to re-render `_render_results()` for both sync and completed-async runs.
- `docker-compose.yml`, `Makefile`, `.env.example` — Redis + Celery worker for local dev.

## Changed files (2026-08-13 — Sprint 1 code)

- `docs/adr/ADR-006-clerk-auth-supabase-postgres-tenancy.md` (new) — supersedes ADR-005.
- `src/ai_etl/services/auth_service.py` (new) — `verify_session_token()`: local JWT verification via JWKS, RS256-only, `exp`/`sub`/`iss` all required, fails closed on every error path.
- `alembic/versions/0003_users_table_and_required_tenant_id.py` (new), `src/ai_etl/audit/models.py`, `src/ai_etl/audit/db.py` (`ensure_user()` added — a real bug found by security review: nothing created the `users` row for a brand-new Clerk account, so every new user's first `save_run()` would fail its FK) — all merged in PR #18.
- `app.py` — real sign-in gate (`_render_sign_in_gate()`) replacing the Sprint A session-UUID gate. Interim UI: paste a Clerk session token (Clerk has no native Streamlit sign-in component yet).
- `Dockerfile`, `docker-compose.yml`, `railway.json` — Railway deploy prep (PR #18).

## Changed files (2026-08-14 — Railway deploy debugging)

- `Dockerfile` (PR #19) — was missing `COPY README.md`; `uv sync --no-editable` needs it on disk (hatchling validates `pyproject.toml`'s `readme` field at build time). Build failed 100% of the time until fixed.
- `railway.json` (PR #20) — removed a redundant `deploy.startCommand` that duplicated the Dockerfile's `ENTRYPOINT`; Railway runs `startCommand` without a shell, so `$PORT` was never expanded and reached Streamlit as the literal string `"$PORT"`.
- `alembic/env.py` (PR #21) — added `connect_args={"connect_timeout": 15}`; the engine had no timeout at all, so a stalled connection (root-caused to a VPN-induced MTU/TLS-handshake stall on the machine running it) hung forever with zero output instead of failing fast. Does not fully solve the class of hang (`connect_timeout` only bounds the initial TCP phase per libpq, not a stalled TLS negotiation) — flagged as a known partial mitigation.

## Validation

- **Sprint 6 PR 6 (cutover) live-verified (2026-08-18)**: PR #49 CI green (ruff clean on `src/`/`tests/`, `uv lock` resolves cleanly with streamlit fully removed; local `uv run`/pytest hit the project's known sandbox hang, CI was the real gate). Post-merge, all 3 Railway services (`ai-etl`, `ai-etl-api`, `tranquil-appreciation`) failed their first deploy simultaneously with zero deploy logs — a transient platform blip, not a real bug (see "Start here next session"); a plain retry fixed `ai-etl-api`/`tranquil-appreciation` immediately, `ai-etl` took a few retries. Once green: `ai-etl-production.up.railway.app/docs` → 200, `/health` → 200, `/runs` unauthenticated → 401. Full end-to-end re-verified live against the consolidated architecture (real Clerk login, `case_study/data/orders.csv` uploaded, real Celery execution, `completed`, Plotly Gold chart rendered in `/historico`) — same rigor as the pre-cutover verification, this time against `ai-etl`'s own domain instead of the interim `ai-etl-api` service.
- **Sprint 6 PRs 1-5 fully live-verified (2026-08-18)**: real Railway service `ai-etl-api` deployed and reachable (`/docs` 200, `/runs` correctly 401 unauthenticated, CORS header matches `ai-etl.vercel.app`); real end-to-end run via the live Vercel frontend — `case_study/data/sales.csv` (5000 rows) uploaded through a real Clerk login (browser automation, not curl — see the Vercel troubleshooting note above for why that distinction mattered), real Celery worker execution, `completed` status, appeared in `/historico`, run detail rendered the Silver table and a real Plotly Gold chart. PR #46 (Dockerfile `api` extra fix) — CI green, live-verified on Railway before merge.
- **PRs #37-#41 (Sprint 6): CI green on all 5**, `frontend-ci.yml` (new job, `frontend/**`-scoped) added alongside the existing Python matrix + e2e. Real findings caught by CI, not locally: a `mypy --strict` DataFrame-stub overload mismatch (`api/serialization.py`, fixed with `cast` + post-`to_dict()` NaN cleanup instead of `DataFrame.where(..., None)`); the Next.js 16→proxy.ts Vercel routing break (build succeeded every time locally and in CI — only reproduced on the actual Vercel deployment, see Confirmed state above); a stale `eslint-config-next` export path after the Next 15 downgrade. `npm audit`: 0 vulnerabilities (pinned `postcss`/`sharp` via `package.json` overrides rather than following `npm audit fix --force`'s suggestion to jump back to Next 16).
- **PR #34/#35 (Sprint 4/5): CI green, both via `mypy`/`pytest` running only in CI** — the sandbox hang (see Known risks) meant neither was runnable locally; verification was `ruff` (clean) + manual review + letting CI be the real gate, same pattern as prior sprints. CI caught real findings both times: Sprint 4 — one `mypy --strict` `no-any-return` in `S3StorageBackend.read_bytes` (fixed with `cast`); Sprint 5 — two real e2e bugs (Advisor's and Analyst's LLM calls were unmocked, because Planner's empty-response fallback produces one Gold sub-task instead of zero — see Known risks) plus a GitGuardian false-positive on a test-only CI credential (resolved via the GitGuardian dashboard, "test credential" classification).
- **Sprint 4 verified live in production**: a real run on Railway wrote all 6 expected artifacts (`{run_id}.json`, `_transform.py`, `_silver.csv`, `_gold_0.csv`, `_gold_0_fig.json`, `_analysis.json`) to `s3://ai-etl-artifacts-brlla/prod/{tenant_id}/...` — write path and key-prefix scoping both confirmed by inspecting the bucket directly.
- PR #18: CI green after 5 debugging rounds (Python 3.11/3.12), 94.29% coverage. Two reusable test bugs found and fixed along the way — see vault bug notes.
- PR #19, #20, #21: CI green, each verified against the real failure it fixes (`docker build`/`docker run` locally for #19; the actual Railway deploy log for #20; direct SQL application against the real Supabase database after `alembic upgrade head` itself proved unable to complete for #21 — see Known risks below).
- **Live deploy confirmed working end-to-end** on Railway: build passes, container boots, public domain reachable, Clerk sign-in gate renders, a real Clerk JWT validates correctly (including correctly *rejecting* an invalid-`kid` token — fail-closed behavior confirmed in production, not just in tests), `ensure_user()` writes to the real Supabase database.
- **Sprint 3's async worker verified live end-to-end on Railway (2026-08-15)** — a real Celery worker service was deployed (Redis addon + a second Railway service running `celery -A ai_etl.core.celery_app:celery_app worker`), and a real upload → enqueue → worker execution → completed run was confirmed via the History tab (`run_id 835efbc7...`, `status=completed`, `4900` rows loaded, `cost_usd=0.000643`, `model_name=gpt-4o-mini`). This first real end-to-end run surfaced and fixed **3 bugs invisible to CI/local dev** (none of the automated tests exercise a real worker process against a real second container):
  1. **`REDIS_URL` was only set on the worker service, not the web service** — the web process also needs Redis (rate-limit counter in `enqueue_analysis`), and defaulted to `redis://localhost:6379/0`, which doesn't exist on Railway. Fixed by adding the same `${{Redis.REDIS_URL}}` reference to the web service too (Railway dashboard config, no code change).
  2. **Uploaded files never crossed the web→worker boundary** — `enqueue_analysis` only passed a file *path* (as text embedded in `spec`), not the file itself; the web and worker are separate containers with separate filesystems. Fixed (PR #30) by base64-encoding the file's bytes through the Celery task payload; the worker re-materializes the file on its own disk before running. Explicitly scoped as a Sprint-3-only interim fix — Sprint 4's S3 storage work replaces it.
  3. **`daemonic processes are not allowed to have children`** — Celery's default `prefork` pool runs each worker as a daemonic process; `core/sandbox.py` (ADR-007) needs to spawn a real `multiprocessing.Process` per sandboxed execution for timeout enforcement, which Python disallows from a daemonic parent. Fixed (PR #31 + Railway Custom Start Command) by running the worker with `--pool=threads` instead of `prefork` — preserves the sandbox's real process-level isolation/timeout while removing the daemon conflict.

## Security fix (2026-08-21) — Supabase RLS was off, `anon`/`authenticated` had full CRUD on every table

**Real, confirmed vulnerability, fixed directly against production — not hypothetical.** Prompted by the owner asking about a known class of Supabase misconfiguration after reading a community report of it. Verified directly against the production database (`information_schema.role_table_grants`, `pg_class.relrowsecurity`):

- Row Level Security was **disabled** on all 6 public tables (`users`, `runs`, `analysis_runs`, `saved_pipelines`, `stage_latencies`, `alembic_version`) — Supabase's own project default, never touched since Sprint 1.
- The `anon` and `authenticated` roles — the roles Supabase's auto-generated PostgREST REST/GraphQL API uses for any request carrying the project's (not-secret-by-design) anon key — had full `SELECT`/`INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` grants on every table, including `users` and all tenants' run data.
- Net effect: anyone in possession of the project's anon key (never used or embedded by this codebase — confirmed zero `supabase-js`/anon-key references anywhere in `src/`/`frontend/` — but not a secret Supabase itself protects) could read and write any tenant's data directly via Supabase's REST API, completely bypassing Clerk auth and the FastAPI backend.

**Fix applied**: `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` on all 6 tables, no policies added. Verified safe *before* and *after* applying — the app's own connection role (`postgres`, owns every table) has `rolbypassrls = true`, so RLS is a no-op for the application itself; a real read (`SELECT COUNT(*) FROM users`) was re-confirmed working immediately after enabling. `anon`/`authenticated` now get zero rows/zero writes by default (RLS enabled + no policy = deny-all for any non-bypassing role) — no policies are needed since this app is not, and has never been, a Supabase-client consumer of these tables.

**Still open, owner's call, not yet acted on**: whether to also disable Supabase's Data API (PostgREST) entirely for this project in the dashboard (Project Settings → API) — this app has never needed it, and doing so would remove this entire class of exposure rather than just this specific instance of it. Grants to `anon`/`authenticated` themselves were left in place (only RLS was turned on) — revoking those grants directly would be an equally valid alternative/belt-and-suspenders fix, not done here to keep the change minimal and single-purpose.

## Known risks / open items

- **`ai-etl.vercel.app` does not automatically follow new production deployments — it needs a manual re-alias after some deploys.** Root cause: the Vercel project was originally created (2026-08-18, during the recreation churn described above) under the name `ai-etl-realfirst`; Vercel assigns a project's "default" `<name>.vercel.app` domain at creation time and does **not** update it on a later `project rename`. `ai-etl.vercel.app` is attached only as a manually-assigned alias (`vercel alias set <deployment> ai-etl.vercel.app`) — `vercel domains add ai-etl.vercel.app ai-etl` reports `"status":"success"` but `vercel domains ls` still shows 0 domains afterward, meaning `.vercel.app` subdomains can't actually be registered as a tracked project Domain via this CLI (only real owned DNS domains can). Confirmed twice: a fresh production deploy (git-triggered, PR #47's merge) left `ai-etl.vercel.app` pointing at the *previous* deployment until manually re-aliased. **If the live site looks stale after a deploy, run `vercel alias set <latest-deployment-url> ai-etl.vercel.app` from `frontend/`** (or via the repo root with `ai-etl` linked) — `vercel ls --json` (not the plain-table `vercel ls`, which renders empty rows in this environment) shows the latest deployment's URL. A cleaner permanent fix would be either renaming the project *before* any deploys happen next time (not applicable retroactively) or checking whether Vercel's dashboard (Settings → Domains) offers a "make primary" action the CLI doesn't expose — not yet tried.
- **`tests/integration/` fails for real when actually run against a live Postgres — never caught before because CI never gave it one.** Discovered while wiring Sprint 5's `e2e` CI job: giving the `check` job's matrix a live Postgres made `tests/integration/test_audit_persistence.py`'s `_database_reachable()` skip-guard start returning `True` for the first time, and the tests underneath immediately failed — a `tenant_id` `NOT NULL` violation (tests written before ADR-006's migration `0003`, never updated) and an Alembic migration-test table-already-exists conflict (`test_alembic_migration.py` and `test_audit_persistence.py`'s `metadata.create_all()` collide on a persistent service-container Postgres, not the ephemeral tmpfs one local `docker-compose` gives each run). **Not fixed** — deliberately kept out of Sprint 5's scope (unrelated to PDF/DOCX or e2e). Worked around by isolating `tests/e2e/`'s Postgres/Redis service containers into their own CI job, leaving `check`'s behavior (integration self-skips, same as always) untouched. Whoever picks up `tests/integration/` next should expect it to fail on first real run and budget time to fix both issues before trying to fold it into the same job as `e2e`.
- **`alembic upgrade head`'s exact root-cause hang is still not fully diagnosed, and recurred a third time (2026-08-15) applying migration `0005`.** Same workaround each time: apply the equivalent schema via direct SQL, manually sync `alembic_version`. A new, significant diagnostic data point from this round: immediately after `alembic upgrade head` hung, a plain `psycopg2.connect()` to the *same* database with the *same* credentials, in the same environment, connected and ran queries in well under a second — isolating the hang to Alembic's own code path specifically, not network/TLS/psycopg2. Also **not** the same HTTP/2 issue behind `git push`/`gh pr create` hangs in this environment (psycopg2 uses the Postgres wire protocol, not HTTP). See Vault: `bugs-solved/mypy-pytest-hang-agent-sandbox.md`.
- **Local `mypy`/`ruff`/`pytest`/`git status`/`git commit` (via pre-commit hooks) all hung repeatedly during Sprint 3 development**, same sandbox bug — recurred again in Sprint 4/5 (2026-08-16: `mypy src/` and `pytest tests/`, near-zero CPU, 10+ minutes wall time) — CI was the real gate throughout; `git commit --no-verify` used to bypass hanging pre-commit hooks, with careful manual review substituting for the local `ruff`/`mypy` pre-commit couldn't run. Separately, `git reset`/`git checkout` operations on this repo were observed to be genuinely slow (not hung) rather than stuck — likely the iCloud Drive eviction pattern (`~/Documents` has iCloud sync enabled) rather than the sandbox-hang bug; letting them run to completion (several minutes, not indefinite) resolved it. Killed git commands can also leave a stale `.git/index.lock` that must be removed before the next git command will run.
- **`SECURITY.md`/`ADR-003` are now stale** — still describe 3 separate `exec()` sites; ADR-007 supersedes this but the older docs weren't rewritten (only cross-referenced). Low priority, but a reader landing on `SECURITY.md` first would get a wrong picture.
- **The Clerk pasted-session-token flow doesn't survive async execution's own polling window** — and, as of 2026-08-16, has escalated from an interim UX nit to a real blocker. Every Streamlit rerun (including the polling loop `app.py` uses while a task is running) re-validates the same static pasted token; if the pipeline takes longer than the token's short lifetime, the user gets bounced to the sign-in gate mid-run (task keeps running server-side regardless — confirmed). Pre-existing limitation since Sprint 1, made more visible by Sprint 3's async model — but it also failed live in production this same day (expired token, had to be regenerated by hand), which is exactly the kind of friction a non-technical Sprint 8 (Validação humana) participant can't be expected to work around. **Decision made 2026-08-16: Sprint 6 (new) — real frontend (Next.js + `@clerk/nextjs` middleware + a new FastAPI layer)**, inserted before the old Sprint 6 (now 7)/Sprint 7 (now 8), Streamlit retired once Next.js covers the same surface. Plan: `~/.claude/plans/adicionar-o-frontend-ir-silly-rabbit.md`. Roadmap renumbered accordingly (Vault: `artefact/sprint-roadmap.md`).
- **Two unreconciled ICP framings** across the project's own docs (`artefact/saas-potential.md`: data engineers; `writing/drafts/draft-visao-produto.md` + owner's stated framing: SMB entrepreneurs) — not yet resolved, flagged for the owner to decide, not a code task.
- **`.claude/specs/sr-standard.md` §8 SaaS Roadmap table** — the project's own pre-existing plan for exactly this transition; the current multi-sprint plan follows its sequencing logic but reorders items where the SaaS-readiness audit found reason to.
- **Local Git object-store corruption in `~/Documents/ai-etl` has now recurred three times** (2026-08-20/21), same "pack file far too short" pattern each time — strongly correlated with running multiple `git worktree`-based subagents in parallel against the same repo's shared `.git/objects`. Recovered every time by deleting and re-cloning fresh from GitHub (never any data loss — everything was already pushed). Full pattern and mitigation: Vault `bugs-solved/git-object-store-corruption-parallel-worktrees.md`.
- **Digest delivery (Sprint 14) is a single global channel per deployment, not per-tenant** — `SLACK_WEBHOOK_URL`/`GOOGLE_CHAT_WEBHOOK_URL`/`TEAMS_WEBHOOK_URL`/`RESEND_API_KEY` are environment variables on the `tranquil-appreciation` worker service, read via plain `os.getenv()`; `saved_pipelines` has no per-pipeline/per-tenant notification-destination column. Fine while the owner is the only real tenant validating the mechanism; needs real schema/API/frontend work before a second customer's alerts should go anywhere but the owner's own Slack/email. See Claude's memory `future_product_ideas.md` item 6.
- **Teams and Google Chat digest channels (PR #69) are blocked by platform account requirements**, not code: Microsoft Teams' "Workflows" incoming webhook requires a Microsoft 365 (work/school) account; Google Chat's "Manage webhooks" requires a Google Workspace (business) account. Confirmed directly — the owner hit an explicit "restricted" screen trying Google Chat with a personal account. No code change needed when either account becomes available — just set the corresponding env var.

## Next steps

**Status as of 2026-08-21: 17 of 29 sprints in the unified roadmap are done** — A, 1-14, 17, 22, 23 (partial — see its section), 29. All are merged to `main`; every one that touches production schema/infra (13, 14, 17, 29) is fully applied and verified live, not just merged in code. Sprint 10 (multi-cloud AWS) is merged as IaC-only — Terraform validated, never `apply`'d, no AWS resources exist, by design (a portability proof, not a migration).

**Digest delivery (Sprint 14 + PR #69) is live for 2 of 4 channels**: Resend (email) and Slack are configured on the `tranquil-appreciation` Railway service and confirmed working with a real test send (`railway run` + a direct call to `send_email_digest`/`send_slack_digest`, both returned `True`, owner confirmed real delivery). Teams and Google Chat are blocked on the owner not having a Microsoft 365 / Google Workspace account respectively — code is complete, only the env vars are missing.

**Next technically-unblocked sprints**: 15 (production reliability, depends only on 13) and 18 (executive UI, depends on 14) — both have no pending dependency now that 13/14 are live. See `product-roadmap-post-tcc.md`'s dependency graph before parallelizing further.

**Real product gap surfaced, not yet a sprint**: digest delivery channels are global (one Slack/email/Google Chat per deployment), not per-tenant — see this file's top summary and Claude's memory (`future_product_ideas.md` item 6) for the real fix shape (new column(s) on `saved_pipelines`, a frontend field, `notifications.py` reading a parameter instead of `os.getenv()`).

## Deploy

- **Target: Railway, live.** Deployed via Docker (`Dockerfile`, `railway.json`), public domain generated through Railway's Networking settings.
- `Dockerfile` installs the `api` extra only (`uv sync --no-dev --no-editable --extra api` — fastapi, uvicorn, python-multipart; `plotly`/`scikit-learn`/`statsmodels` are base dependencies, not extras); `ENTRYPOINT` runs `sh -c "uvicorn ai_etl.api.main:app --host 0.0.0.0 --port $PORT"`. `$PORT` is injected by Railway at runtime; `sh -c` is required for it to expand (Railway runs `ENTRYPOINT`/`startCommand` without a shell otherwise — see PR #20/#46 history). **Streamlit/`app.py` retired in Sprint 6's PR 6 cutover (2026-08-18) — no longer part of this project.** The `ai-etl` service's config also has a redundant explicit `deploy.startCommand` matching the `ENTRYPOINT` exactly (harmless leftover from this session's deploy troubleshooting — see "Start here next session").
- `railway.json` points Railway's builder at the Dockerfile only — no `startCommand` override (see Changed files above for why).
- `docker-compose.yml` has an `api` service (was `app`/Streamlit pre-cutover) for local dev parity.
- Env vars set in Railway's dashboard (not committed): `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `APP_DATABASE_URL` (Supabase **Session pooler**, not Direct connection — Direct connection is IPv6-only and unreachable from Railway's IPv4-only egress), `OPENAI_API_KEY`. **Sprint 4 added, on both the web and worker services** (both read/write `./runs/` artifacts): `STORAGE_BACKEND=s3`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION=sa-east-1`, `AI_ETL_S3_BUCKET=ai-etl-artifacts-brlla`, `AI_ETL_ENV=prod`.
- **Second Railway service: the Celery worker** (Sprint 3) — same repo/image as the web service, deployed as its own service with a **Custom Start Command** (`celery -A ai_etl.core.celery_app:celery_app worker --loglevel=info --pool=threads --concurrency=2`) overriding the Dockerfile's `ENTRYPOINT`. Unexposed (no public domain, correctly). Env vars: `APP_DATABASE_URL`, `OPENAI_API_KEY`, `POSTGRES_URL`, `REDIS_URL` (`${{Redis.REDIS_URL}}` reference), the 6 S3 vars above (Sprint 4) — deliberately **without** the `CLERK_*` vars, since auth only happens in the web process. Redis itself is a Railway-managed addon in the same project, referenced (never hardcoded) from both the web and worker services.
- **Target (Sprint 6): Vercel, live** — `frontend/` (Next.js), project `ai-etl` (`prj_55IU6Ntx7CviFT9VN4lNM9Cbs3Jp`), Root Directory `frontend`, connected to `brunoribeirol/ai-etl`, auto-deploys on push to `main` (confirmed working 2026-08-18). Public URL: `ai-etl.vercel.app` — **manually re-aliased after every production deploy, doesn't auto-follow** (see Known risks). Deployment Protection (Vercel's own SSO wall, on by default for new projects) is disabled — it would otherwise block every visitor before they even reach Clerk. Env vars (Production + Preview): `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_API_URL=https://ai-etl-production.up.railway.app` (points at the main Railway service post-cutover; the interim `ai-etl-api` service is decommissioned — see "Start here next session").
- `API_ALLOWED_ORIGINS=https://ai-etl.vercel.app` is set on the `ai-etl` Railway service (CORS, `api/main.py`'s `CORSMiddleware`, comma-separated allowlist, no wildcard) — moved here from the now-decommissioned interim `ai-etl-api` service as part of the PR 6 cutover.

## Related

- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-18-sprint6-railway-api-vercel-fix-e2e-verified.md` — this session: Railway API deployment, Vercel domain troubleshooting + correction, live e2e verification, frontend design feedback.
- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-17-sprint6-fastapi-nextjs-clerk-frontend.md` — Sprint 6 PRs 1-4, Vercel troubleshooting (superseded — see the correction above and in the bug note), source-diversity direction.
- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-16-sprint4-s3-storage-sprint5-document-source-e2e.md` — S3 storage (Sprint 4), PDF/DOCX source + e2e (Sprint 5), frontend decision (new Sprint 6).
- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-13-sprint1-clerk-auth-tenancy.md` — Sprint 1 code session.
- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-14-railway-deploy-clerk-supabase.md` — deploy debugging session (4 infra bugs, all documented as reusable Vault bug notes).
- Vault: `~/Documents/Obsidian Vault/tcc/artefact/saas-potential.md` — product/business framing (explicitly out of TCC scope).
- Vault: `~/Documents/Obsidian Vault/tcc/bugs-solved/mypy-pytest-hang-agent-sandbox.md` — the recurring local sandbox hang, updated 2026-08-16.
- Vault: `~/Documents/Obsidian Vault/tcc/bugs-solved/vercel-project-domain-404-fixed-by-recreate.md` — Sprint 6's Vercel deployment postmortem.
- `docs/adr/` — ADR-001 through ADR-011.
