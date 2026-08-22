# ADR-028: Human Approval / Dry-Run Before Production Writes

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 27 (post-TCC product roadmap)

## Context

Sprint 13 (ADR-016) made pipelines recurring and unattended — Celery beat fires a saved
pipeline on a cron schedule with no human in the loop. Sprint 20 (ADR-021) gave the Loader a
second real destination (`s3_parquet`, alongside `postgres`/`csv`) that can point at a
customer's actual warehouse/lake, not just a demo target. Put together: an autonomous,
recurring pipeline can now write to a customer's real production system on a schedule, with
nobody watching. `agents/loader.py::loader_node` writes unconditionally today — no preview, no
confirmation, no way to stop the first (or the riskiest) write from happening blind. One wrong
`transformation_code` regeneration (LLM-authored, Sprint 2/ADR-007) landing on a scheduled fire
is an unrecoverable write to a real system. That is exactly the trust the roadmap's own framing
calls out: autonomy over an irreversible action is what breaks confidence, not a slow pipeline.

**Definition of done (roadmap):** a pipeline configured to require approval never writes to its
destination without an operator's explicit confirmation; the preview shown matches what would
actually be written.

**The real architectural question this ADR has to answer, not assumed going in**: a scheduled
fire runs inside one Celery task invocation (`execution_queue.py::run_full_analysis_task` →
`pipeline_service.run_full_analysis` → `run_silver_pipeline` → `graph.stream(state)`, all
synchronous, in-process). There is no human available to click "approve" while that task is
running, and a Celery task cannot block on an external event without holding a worker thread
hostage indefinitely (this project's worker pool is `--pool=threads --concurrency=2` — see
`docs/CURRENT_STATE.md`'s Deploy section — blocking one thread on an unbounded wait would halve
real throughput for every other tenant). Waiting synchronously inside the task is not an option.

## Decision 1 — Pause by ending the graph in a new terminal status, resume via a second, direct invocation of the Loader node

The LangGraph topology (`core/graph.py`) is unchanged: `orchestrator → extractor → transformer →
quality → loader → END`. No checkpointer, no new node, no new edge. Instead, `loader_node`
itself gains a third possible outcome alongside "wrote successfully" / "write failed":
**"gated — computed a preview, wrote nothing, `status = "awaiting_approval"`"**. Because
`loader → END` is already the graph's last edge, `awaiting_approval` becomes a legitimate
terminal state of one normal `graph.stream()` run — the same as `"completed"`/`"failed"` today.
The first Celery task invocation still runs to completion in bounded time; it just may finish
having deliberately not written anywhere.

Approval, when it later arrives (an API call an operator makes from the frontend, whenever they
get to it — seconds or days later), does **not** re-run the graph. `agents/analyst.py`/
`science.py`/the Orchestrator/Transformer already ran and cost real LLM tokens (Sprint 3/8,
~$0.0006/run measured) to produce `transformed_data`; re-running them on approval would waste
that cost and — since `transformer.py`'s LLM call is not deterministic — could regenerate a
*different* transformation than the one the operator actually previewed and approved. Instead,
approval triggers `pipeline_service.resume_pending_load(run_id, tenant_id)`, which:

1. Reloads the persisted state via the **already-existing** `audit.db.load_full_result()` —
   built in Sprint 3/4 for an unrelated reason (reconstructing a completed run's DataFrames
   across the web/worker process boundary for the History tab), but it does exactly what a
   resume needs for free: `{run_id}.json` round-trips `pipeline_plan` (a plain dict, JSON-safe
   already) and `{run_id}_silver.csv` reconstructs `transformed_data` as a real DataFrame. No
   new persistence mechanism, no new storage key, no LangGraph checkpointer dependency added.
2. Calls `loader_node(state)` **directly** — one node, not the graph — with a new
   `approval_granted=True` flag on the reloaded state, so this second call takes the real-write
   branch instead of the gated one.
3. Calls `save_run()` again — already an `INSERT ... ON CONFLICT (run_id) DO UPDATE` — to
   overwrite the `runs` row from `awaiting_approval` to `completed`/`failed` in place. No new
   status-transition machinery; the existing upsert already does this correctly.

Rejection (`resume_pending_load`'s sibling, `reject_pending_load`) follows the same shape
without ever calling the real write path — sets `status="failed"`, `error="Rejected by
operator..."`, persists via the same `save_run()` upsert.

**Rejected alternative — a LangGraph checkpointer (`langgraph.checkpoint`) pausing mid-graph.**
Would let the graph itself suspend before the `loader` node and be resumed by a later
`graph.stream(None, config)` call with the same thread id. Rejected for this sprint: it is a new
dependency/runtime concept (checkpoint storage — Postgres or Redis-backed) this project has
never used, LangGraph's checkpointer API resuming a graph whose earlier nodes already ran
non-idempotent LLM calls needs care to avoid re-invoking them, and — critically — it solves a
problem this project doesn't actually have: only the *last* node (`loader`) ever needs to pause.
Reusing `load_full_result`'s existing reconstruction path for a **single node** replay is a
strictly smaller change with an already-proven persistence path, at the cost of the resume
being "re-run one function," not "resume a graph" in the framework's own vocabulary. Worth
revisiting once a second node needs the same pause/resume capability.

**Rejected alternative — hold the Celery task open, polling for approval.** Ruled out above
(Context) — indefinitely blocks a worker thread out of a 2-thread pool, degrading every other
tenant's throughput while one operator is asleep. Would also fight Sprint 15/ADR-020's own
retry logic (`autoretry_for`/`self.retry()`), which assumes a task either finishes or raises in
bounded time, not one parked mid-execution for an unbounded human-timescale wait.

**Consequence, accepted**: an approved write does not currently re-trigger Sprint 14's drift
digest or update Sprint 15's `consecutive_failures`/`last_status` health cache automatically —
those live in `execution_queue.py::run_full_analysis_task`'s own post-completion control flow,
which the resume path (a plain service call from an API route, not a Celery task) does not go
through. `resume_pending_load`/`reject_pending_load` do call `audit.db.record_pipeline_health`
directly so the health cache doesn't go stale, but the drift-digest side effect is explicitly
out of scope here — flagged, not silently dropped. A pipeline stuck `awaiting_approval` for a
long time is also not itself retried or alerted on by Sprint 15's machinery (see Decision 3).

## Decision 2 — Gating is opt-in, per saved pipeline, and only applies to scheduled fires

Three new nullable/defaulted columns on `saved_pipelines` (migration `0015`):

- `require_approval` (`BOOLEAN NOT NULL DEFAULT false`) — the opt-in switch itself. `false` for
  every existing and new pipeline until an operator turns it on — zero behavior change unless
  explicitly requested, same posture every prior additive column on this table has taken
  (`drift_threshold_pct`, `quality_rules`, ...).
- `approval_threshold_rows` (`INTEGER NULL`) — "any write above a configurable impact
  threshold" from the roadmap's own scope line. `NULL` means "every write requires approval"
  (the roadmap's own "opção de exigir aprovação sempre"); a set value means "only a write whose
  row count is at or above this needs approval" — reusing `rows_loaded`'s existing definition
  (`len(transformed_data)`), the same number `load_result`/`runs.rows_loaded` already report,
  rather than inventing a second impact metric.
- `last_approved_at` (`TIMESTAMPTZ NULL`) — `NULL` means this pipeline has never had an
  operator-approved write; the roadmap's "before the first real write" clause is unconditional
  regardless of `approval_threshold_rows` as long as this is `NULL`. Set once, on the first
  successful approval, by `resume_pending_load`. This is what lets "writes subsequentes...podem
  seguir automáticos" work without a second boolean: once approved at least once, only a fire
  whose row count clears the configured threshold is gated again.

Gate decision (`agents/loader.py::_is_write_gated`, a pure function, unit-testable directly):

```python
def _is_write_gated(policy: dict[str, Any] | None, rows: int) -> bool:
    if not policy or not policy.get("require_approval"):
        return False
    if policy.get("last_approved_at") is None:
        return True
    threshold = policy.get("threshold_rows")
    if threshold is None:
        return True
    return rows >= threshold
```

**Scope decision: only saved (scheduled) pipelines can be gated — an avulso (`POST /runs`)
execution is never gated.** Mirrors ADR-020 Decision 1's own reasoning for retry scope exactly:
an avulso run already has a human watching it synchronously (the same person who just clicked
"Executar" and is polling the result) — gating it too would just be a second confirmation click
from the same person who already made the first one, for zero added safety. The risk this ADR
exists to close is specifically the *unattended* recurring write. `PipelineState` gains
`approval_policy: Optional[dict[str, Any]]` (resolved by `pipeline_service.run_silver_pipeline`
from the saved pipeline's row, the same place `custom_quality_rules` — ADR-023 — is already
resolved from, immediately before `initial_state()`) — `None` for every avulso run, by
construction. `loader_node` stays a pure function of `PipelineState`; it never queries the
database itself, matching every other node's contract.

**Scope decision: the gate covers only the Loader's destination write, not Gold/Science/Advisor
analysis.** `run_full_analysis` runs Planner → Gold/Science → Advisor against
`silver_state["transformed_data"]` regardless of the Silver state's `status`, as long as the
DataFrame is non-empty — unchanged by this ADR. Gold/Science/Advisor persist their results to
this application's *own* storage/database (`save_analysis`), never to the customer's
destination system — they are not the "write to a real system" the roadmap is protecting
against, so gating them would add a second, pointless confirmation with no corresponding risk
reduction.

## Decision 3 — `awaiting_approval` is a third, non-failure terminal status, threaded through Sprint 15's reliability machinery explicitly

`PipelineState["status"]` (a plain `str`, not a `Literal` — unchanged) gains a third real value
alongside `"running"`/`"completed"`/`"failed"`. This is not cosmetic: ADR-020's Level-B retry
(`execution_queue.py::run_full_analysis_task`) already treats "not completed" as "retry it,"
and its health-alert threshold (`consecutive_failures`) already treats "not completed" as "this
pipeline is broken." Both would misfire on a gated fire — retrying a write that is correctly
waiting for a human would just recompute the same preview `SCHEDULED_PIPELINE_MAX_RETRIES`
times for no reason, and `consecutive_failures` incrementing on every gated fire would
eventually fire Sprint 15's "this pipeline is broken" operator alert for a pipeline that is
working exactly as configured. Both call sites now special-case `final_status ==
"awaiting_approval"` explicitly: Level-B retry's condition becomes `final_status not in
("completed", "awaiting_approval")`, and `_record_scheduled_pipeline_health_best_effort` is
skipped entirely (no `consecutive_failures` increment, no reset, no alert) for that status —
the health cache is left exactly as the previous fire left it until the pending write is
resolved one way or the other.

**Known limitation, explicit**: a pipeline that sits `awaiting_approval` indefinitely (the
operator never comes back) is not itself surfaced by Sprint 15's health-alert path — there is no
"you have a pipeline stuck waiting on you" nudge. `GET /runs/pending-approval` (new, this
sprint) makes the queue visible on demand; a proactive "N pending approvals, oldest is X days
old" digest is a natural Sprint 14-shaped follow-up, not built here.

## Decision 4 — The preview is built from the same validated inputs the real write uses, never invents a second code path

Each destination module (`destinations/csv_dest.py`/`postgres_dest.py`/`s3_parquet_dest.py`)
gains a sibling `preview_*` function returning a small dict (`would_write_rows`, `destination`,
plus a destination-specific existing-state check: `postgres` reads the real
`SELECT COUNT(*)` if the target table already exists — same `_validate_table_name` guard as the
real write, read-only; `s3_parquet` does a `head_object` for the existing object's size, if any;
`csv` reports the local path's existing size, if any). None of the three ever calls
`to_csv`/`to_sql`/`put_object` — the "never accidentally writes on preview" invariant is
structural (the preview function contains no write call at all, not a `dry_run` flag threaded
through the write function that a caller could get wrong) rather than relying on a conditional
inside a function whose whole purpose elsewhere is to write. Both the preview and the real write
compute `would_write_rows`/`rows_loaded` the same way (`len(df)`), from the same `df` and the
same validated `destination` dict `pipeline_plan["destination"]` already carries — so the
preview `resume_pending_load` never regenerates matches, by construction, what the real write a
moment later actually does. This is what makes the roadmap's "the preview shown matches what
would actually be written" definition of done true by design, not by re-verification.

The output-validation reuse the roadmap names (Sprint 21/ADR-026, `check_gold_output`/
`check_science_output`) is additive context, not the preview's row-count source:
`GET /runs/pending-approval`'s detail view surfaces the run's already-computed
`quality_report`/any Gold/Science `sanity_check` entries (Sprint 21) alongside the Loader's own
`load_preview`, so an operator approving a write also sees whether the analysis layer already
flagged something about this run's data — full context in one screen, without ADR-026's checks
needing to know anything about destinations.

## Consequences

**Positive**: a scheduled pipeline can now be configured so its first (or any high-impact)
write to a real destination requires a human to look at a preview and click confirm — the
roadmap's stated trust gap is closed. Zero behavior change for the ~29 pipelines/sprints this
project has shipped before this one (`require_approval` defaults `false`). The resume mechanism
reuses `load_full_result`/`save_run`'s existing upsert wholesale — no new persistence layer, no
new dependency, no LangGraph API surface this project hasn't already exercised.

**Negative / accepted limitations**:
- Frontend is out of scope for this sprint (backend contract first — same staged-delivery
  pattern as Sprint 29's `GET/PATCH /budget`, Sprint 17's `GET /pipelines/{id}/history` before
  their own frontend views landed later). `GET /runs/pending-approval`,
  `POST /runs/{run_id}/approve`, `POST /runs/{run_id}/reject`, and `require_approval`/
  `approval_threshold_rows` on `POST`/`PATCH /pipelines` are real, tested backend contracts with
  no UI consuming them yet.
- No proactive notification that a pipeline is waiting on approval (Decision 3's known
  limitation).
- Approving a pending write does not re-trigger the drift digest or update
  `consecutive_failures`/`last_status` via the normal Celery-task path (Decision 1's accepted
  consequence) — health cache is kept in sync by a direct call instead, drift is not.
- `postgres`'s preview issues one real read query (`SELECT COUNT(*)`) against the customer's
  database to build the diff — not a pure computation. Same trust boundary the real write
  already crosses (a configured `POSTGRES_URL`/destination the pipeline owner supplied); no new
  credential or access this ADR introduces.
