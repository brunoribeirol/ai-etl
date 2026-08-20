# ADR-018: Drift detection and digest delivery for saved pipelines

**Status:** Proposed (checkpoint — not merged, see PR)
**Date:** 2026-08-20
**Context:** Sprint 14 (product roadmap post-TCC, `product-roadmap-post-tcc.md`)

## Context

Sprint 13 (ADR-016) made recurring execution possible: a `saved_pipeline` fires
unattended on a cron schedule and produces a normal `runs` row every time,
exactly like an avulso run. Nothing today connects one fire to the next —
each scheduled execution is audited in isolation, and a customer has to open
Histórico and eyeball two runs side by side to notice anything changed.

That is the gap this sprint closes: give the recurring-pipeline customer a
recurring *reason* to come back — an alert when something in a pipeline's
output moved more than expected, delivered as a ready-to-read briefing
(email and/or Slack) built from output the Gold/Science/Advisor agents
already produce. No new agent — this is a comparison + delivery layer on
top of the existing pipeline.

Sprint 17 (named in the roadmap as landing after this one) is expected to
formalize a general "comparable run history" concept. This ADR deliberately
does **not** wait for or attempt to predict that design — see Decision 1.

## Decision 1 — compare "most recent" vs "second most recent" `runs` row for the same `saved_pipeline_id`, nothing more

The comparison unit is two `runs` rows with `status = "completed"` that share
a `saved_pipeline_id`, ordered by `timestamp`. No new "comparable run" concept,
no run grouping/versioning, no historical trend line — just "the current
scheduled fire vs. the one immediately before it, for this same pipeline."

This requires `runs` to actually know which `saved_pipeline` produced it —
today it doesn't (Decision 2). Once Sprint 17 defines a richer comparable-run
abstraction, this comparison should be re-expressed on top of it rather than
duplicated; until then, a direct query against `runs.saved_pipeline_id` is the
smallest correct thing that unblocks this sprint without guessing at a design
another sprint owns. Chosen over waiting for Sprint 17: the two sprints run in
parallel by explicit owner instruction, and "most recent vs. second most
recent" is simple enough that re-deriving it later on top of a formal
comparable-run model is a small, mechanical follow-up, not a rewrite.

A pipeline's very first scheduled fire has no prior run to compare against —
`get_previous_completed_run` returns `None`, and drift detection is a no-op
for that fire (not an error, not a false "everything changed" alert).

## Decision 2 — add `runs.saved_pipeline_id` (nullable FK), not a new join table

`runs` needs one new nullable column, `saved_pipeline_id` (FK
`saved_pipelines.id`), set only when a run was produced by a scheduled fire
(`services/scheduler.py::check_scheduled_pipelines_task` threads the pipeline
id through `enqueue_analysis` → the Celery task → `run_full_analysis` →
`run_silver_pipeline` → `save_run`). An avulso run (`POST /runs`) never sets
it — `NULL` for every run before this column existed and every future
manually triggered run, matching `saved_pipelines.last_task_id`'s existing
"loose link, no cascade" precedent from ADR-016 rather than inventing a
stricter association table for what is, in practice, a 1:N pointer with no
extra attributes of its own.

Rejected: a separate `pipeline_run_links` table. Nothing about this
relationship needs its own row (no extra columns, no many-to-many) — a plain
FK column is `saved_pipelines.last_task_id`'s pattern taken one step further
(now a real FK a query can filter/sort on, not just a display-only string).

### Addendum — reconciled against Sprint 17's parallel, more complete version (post-review)

Sprint 17 (ADR-017, `feat/sprint17-comparable-run-history`) independently
added the *same* `runs.saved_pipeline_id` column in the same migration slot
(`0007`) while this sprint ran in parallel — a genuinely more complete
version for the project as a whole: it also adds the FK to `analysis_runs`
(not just `runs`), and uses `ON DELETE SET NULL` on both (deleting a saved
pipeline must not delete the run history it produced — this sprint's first
cut omitted `ondelete` entirely, defaulting to `RESTRICT`, which would have
blocked deleting any saved pipeline with run history at all, a real
regression this reconciliation caught). Sprint 17 merges first.

Rather than have two migrations both try to `ADD COLUMN saved_pipeline_id`
against the same table (the second would fail outright — Postgres does not
support "add column if not exists" via plain `ALTER TABLE`), this sprint's
migration was rewritten to **not** create the column at all. Renumbered
`0007_drift_alerts.py` → `alembic/versions/0008_drift_threshold_pct.py`,
`down_revision` now points at Sprint 17's `"0007"` (this sprint's migration
revises *that* migration, not `0006` directly) and only adds two things that
remain genuinely this sprint's own, non-overlapping concern:

1. `saved_pipelines.drift_threshold_pct` — Sprint 17 never touches
   `saved_pipelines`, so this is unaffected by the collision.
2. A composite index `(saved_pipeline_id, timestamp)` on `runs`. Sprint 17's
   own migration only adds a single-column index on `saved_pipeline_id`
   (sufficient for its own comparable-run-history queries, which are not
   this sprint's `ORDER BY timestamp DESC LIMIT 1` "most recent fire" shape).
   Kept as a genuinely additional index alongside Sprint 17's rather than
   assuming their single-column index is enough for this sprint's specific
   access pattern, or folding it into a migration this sprint doesn't own.

`audit/models.py`'s Python `Table` declaration for `runs.saved_pipeline_id`
was kept (not deleted) despite the column no longer being created by this
sprint's migration: this branch's own standalone unit tests
(`tests/unit/test_saved_pipelines_db.py`,
`tests/unit/test_audit_db.py`) build their schema via
`audit.models.metadata.create_all()` against an in-memory SQLite engine, not
via Alembic — they need the column to exist in the Python model regardless
of which migration owns creating it in real Postgres. Its `ondelete` was
updated from unset (`RESTRICT`, this sprint's original, less-correct
default) to `"SET NULL"` to match what Sprint 17's migration actually
creates, so this branch's own tests exercise the same delete semantics
production will have post-merge. This is flagged, not silent: both branches
will declare the same column in the same file, and reconciling it into one
declaration is explicit, expected work for whoever handles the merge, not
something either branch can resolve unilaterally ahead of time.

**Verified for real** by temporarily staging a copy of Sprint 17's actual
`0007_run_pipeline_linkage.py` (not committed to this branch — that file is
Sprint 17's to add) next to this sprint's rewritten `0008` against a
throwaway local Postgres: `alembic upgrade head` applied the full chain
(0001→0006→Sprint 17's 0007→this sprint's 0008) cleanly, the resulting
`runs`/`saved_pipelines` schema matched exactly (including Sprint 17's
`ON DELETE SET NULL` FK and this sprint's own composite index), and a real
`get_previous_completed_run` + `detect_kpi_drift` round trip against rows
inserted through both migrations' resulting schema produced the expected
triggered finding.

## Decision 3 — threshold: a single per-pipeline `%` change on any tracked KPI

`saved_pipelines.drift_threshold_pct` (`float`, default `20.0`, server-side
default so every pre-existing row backfills to a sane value with no manual
migration step). A KPI's drift is "significant" when
`abs((current - previous) / abs(previous)) * 100 >= drift_threshold_pct`.
Configurable per pipeline via `POST /pipelines`/`PATCH /pipelines/{id}`, so a
noisy, fast-moving pipeline can be tuned looser than a stable one without a
code change — the sprint's own "avoid alert fatigue from day 1" requirement.

Tracked KPIs (Decision 4) that read `previous == 0` cannot express a `%`
change (division by zero) — treated as "drifted" whenever `current != 0`
(going from zero to any non-zero value is inherently notable, e.g. a source
that returned zero rows now returning rows, or vice versa), and as
"unchanged" when both are zero. This is the one place `pct_change` in a
`DriftMetric` is `None` while `triggered` can still be `True` — documented in
`core/drift.py`'s own docstring, not left to be inferred from the shape.

Rejected: a fixed global threshold (env var, no per-pipeline override) — too
blunt for pipelines with very different natural volatility, and the sprint
explicitly asks for a decision that avoids fatigue "from day 1," which a
single global number cannot do across a fleet of dissimilar pipelines.
Rejected: absolute-value thresholds ("alert if rows_loaded changes by more
than N") — unlike `%`, an absolute threshold doesn't stay meaningful as a
pipeline's data volume itself grows or shrinks over its lifetime, and would
need a different unit per KPI (rows vs. USD vs. an arbitrary Science metric)
instead of one dimensionless number.

## Decision 4 — which KPIs are compared

Four KPI families, chosen because every one of them is already computed and
persisted by existing code — no new computation, no new agent:

1. **`rows_loaded`** — `runs.rows_loaded` (Loader's own output, ADR-004).
2. **`cost_usd`** — `analysis_runs.cost_usd` (`core/pricing.py`, ADR-008).
3. **`total_tokens`** — `analysis_runs.total_tokens` (ADR-008).
4. **Science numeric metrics** — every `model_info.metrics` entry from every
   Science sub-task whose value is `int`/`float` (e.g. `rmse`, `r2`,
   `accuracy`), keyed by `"science · {task_question} · {metric_name}"` so a
   multi-sub-task business question tracks each sub-task's own model
   independently. Non-numeric metric values are skipped, not stringified —
   comparing a numeric threshold against a rendered string would be silently
   wrong. A KPI whose name only exists on one side (e.g. a sub-task's
   question changed wording between fires, so the two runs' Science metrics
   don't share a key) is simply not compared — there is no meaningful
   "previous value" for a brand-new sub-task, and treating "no prior value"
   as "infinite drift" would be a false positive on every pipeline edit, not
   a real signal.

Rejected for v1: Gold KPIs (Gold's `gold_df` has no fixed schema across
pipelines — there is no single numeric column to key on the way Science's
`model_info.metrics` already gives a small, named, numeric dict); data
*quality* metrics (nulls/duplicates/outliers) — `quality_node`'s report is
not currently persisted to a queryable column, only embedded in the lossy
`{run_id}.json` snapshot; wiring it into this comparison would mean adding
persistence for a second, unrelated concern in the same PR. Both are
reasonable follow-ups once there's a concrete customer need for them,
not deferred for lack of value.

## Decision 5 — delivery: Resend (email) and a Slack incoming webhook, both optional and independently configured

`services/notifications.py` adds `send_email_digest()` (Resend's HTTP API,
`RESEND_API_KEY`/`AI_ETL_ALERT_EMAIL_FROM`/`AI_ETL_ALERT_EMAIL_TO`) and
`send_slack_digest()` (a plain Slack incoming webhook URL,
`SLACK_WEBHOOK_URL`) — both built on `httpx`, already a base dependency, so no
new dependency for either channel. Each function no-ops (returns `False`,
does not raise) when its own env vars are unset — a saved pipeline with
drift enabled but no delivery channel configured still runs the comparison
and records the finding, it just has nowhere to send it, which is a
configuration gap to fix, not a crash.

**Not tested against a real provider in this environment** — this sandbox has
no `RESEND_API_KEY` and no real Slack workspace/webhook URL (same "flagged,
not faked" pattern as Sprint 8's model-comparison harness with no
`OPENAI_API_KEY`, and Sprint 23's Ollama testing). What *is* verified: the
HTTP request shape (endpoint, headers, JSON payload) against Resend's and
Slack's own published API contracts, and the full success/failure/not-configured
control flow via `httpx`-mocked unit tests. A real send needs zero code
changes once real credentials exist — flagged as an explicit follow-up, not
silently assumed to work.

Rejected: SES — Resend was chosen for a simpler HTTP-only integration (no
AWS SDK call shape, no SES sandbox/domain-verification setup to document for
a project with no AWS SES identity provisioned) at this project's TCC-adjacent
scale; nothing here prevents adding an SES-backed alternate implementation
behind the same `send_email_digest` call site later if a real deployment
prefers it. Rejected: a generic "pluggable notification channel" abstraction
(interface + registry for arbitrary future channels) — over-engineering for
exactly two channels with no third one requested; add the abstraction when a
third channel is actually needed, not speculatively now.

## Decision 6 — where drift detection runs, and how a failure there is isolated

Drift check + digest happens inside `run_full_analysis_task`
(`services/execution_queue.py`), the same Celery task every run (avulso or
scheduled) already executes — only when the task was invoked with a
`saved_pipeline_id` (i.e., this fire came from `services/scheduler.py`, never
from `POST /runs`). It is wrapped so any failure inside it (a DB hiccup
fetching the previous run, a malformed digest, a delivery-provider error) is
caught and never fails the run itself — the actual pipeline execution and its
`runs`/`analysis_runs` rows must never be held hostage by an alerting
side-effect, the same "one bad thing must not break the main path" principle
`services/scheduler.py`'s own per-pipeline try/except already applies to a
single due pipeline failing a tick.

`services/alerting.py` is the new orchestration module (`core/drift.py` for
the pure comparison math, `services/digest.py` for turning a drift result +
the Advisor's existing narrative/recommendations into subject/text/HTML/Slack
Block Kit content, `services/notifications.py` for the two delivery
functions) — kept out of `core/` because it does real I/O (DB reads, outbound
HTTP), matching the `core/` vs `services/`/`audit/` layering
`.claude/specs/sr-standard.md` already documents.

## Consequences

- `runs.saved_pipeline_id` is `NULL` for every run before the column existed
  (Sprint 17's migration, see Decision 2's addendum) and every future avulso
  run — drift detection is a strict no-op for those (nothing to compare, no
  `saved_pipeline_id` was ever threaded through).
- `audit/models.py` carries a duplicate-in-spirit `runs.saved_pipeline_id`
  declaration until Sprint 17 actually merges and the two branches'
  declarations are reconciled into one (see Decision 2's addendum) — expected,
  flagged merge-time work, not an oversight.
- Frontend UI for setting `drift_threshold_pct` or viewing past drift
  findings is **out of scope for this sprint** — the API accepts the field
  (`POST`/`PATCH /pipelines`) and defaults it sanely for every pipeline that
  doesn't set it explicitly, but `frontend/src/components/pipelines-manager.tsx`
  is not touched. Same kind of deliberate, flagged scope cut Sprint 7 made for
  per-run model selection.
- Drift findings are not persisted as their own row/table — a triggered
  finding is computed, formatted, and (best-effort) delivered, then
  discarded; there is no "drift history" to query later. Acceptable for a v1
  whose stated purpose is "give a recurring reason for the customer to look
  today," not analytics on alerting itself — a future sprint could add a
  `drift_events` table if that need materializes.
- Once Sprint 17 lands a general comparable-run-history model, this sprint's
  direct `runs.saved_pipeline_id` query should be revisited to sit on top of
  it rather than duplicate its own notion of "previous run" indefinitely.

## Addendum (2026-08-21) — Teams and Google Chat added as delivery channels

At the owner's request, extended Decision 5 with two more channels, both
following the exact opt-in/best-effort contract email and Slack already
have: `send_teams_digest()` and `send_google_chat_digest()` in
`services/notifications.py`, wired into `check_drift_and_notify`'s existing
best-effort delivery block. `check_drift_and_notify`'s return dict gained
`teams_sent`/`google_chat_sent` alongside the existing `email_sent`/
`slack_sent` — additive, no existing key removed or renamed.

- **Teams**: targets the current Power Automate "Workflows" incoming webhook
  (an Adaptive Card payload) — Microsoft retired the legacy Office 365
  Connector webhook format this project's earlier notes might otherwise
  assume; the Workflows path is what a channel's own "Workflows" connector
  setup produces today.
- **Google Chat**: a space's Incoming Webhook, `{"text": ...}` — Google
  Chat's `text` field renders a small Markdown-like subset natively, so the
  same plain `text` digest field email/Slack/Teams already share is sent
  as-is, no separate formatting needed.
- Both reuse `services/digest.py`'s existing `subject`/`text` fields — no
  new digest-formatting function, no new digest fields — Teams/Google Chat
  get the same content Slack's `fallback_text` and email's plain-text body
  already carry, just a different envelope per provider's API contract.
- **Not verified against a real provider**, same limitation as Decision 5's
  original two channels — no real Teams/Google Chat webhook URL available in
  this environment. Request shape matches each provider's published API
  contract; a real send needs zero code changes once real credentials exist.
