# ADR-007 — Unified sandbox policy, enforced timeouts, and per-stage latency instrumentation

**Status:** Accepted
**Date:** 2026-08-14
**Deciders:** Bruno Ribeiro

---

## Context

ADR-003 documented three independent `exec()` sites (`core/sandbox.py`, `agents/analyst.py`, `agents/science.py`) with diverging globals whitelists and a `timeout_seconds` parameter that is declared but never enforced anywhere. ADR-006 accepted this as the top open risk once real Clerk auth and a public Railway deploy shipped, and deliberately deferred the fix to Sprint 2 — this ADR is that Sprint 2 artifact. It closes ADR-003's "Follow-up" section and additionally scopes a second, independent Sprint 2 deliverable: per-pipeline-stage latency instrumentation for evaluation metrics. Two design questions need answers before implementation starts.

### Question 1 — sandbox unification approach

1. **One shared base + per-agent extra-allowlist layering** — `core/sandbox.py` becomes the single `exec()` call site; `analyst.py`/`science.py` stop calling `exec()` directly and instead call into `core/sandbox.py`, passing only the *additional* globals/builtins their prompts require (Plotly for Analyst; Plotly + sklearn + statsmodels for Science) on top of one shared, reviewed base. This was ADR-003's own stated preferred direction.
2. **Keep three separate implementations, but formally document why each needs a different boundary** — write down, per site, why Analyst/Science need `setattr`/`vars`/`iter`/`next` and Transformer doesn't. Rejected: an audit of the current whitelists during this ADR's research found no code path in either the Analyst or Science prompt templates that actually requires `setattr` or `vars` on arbitrary objects — the LLM is asked to produce `gold_df`/`fig`/`narrative` (and `predictions_df`/`model_info` for Science) as plain assignments, not to mutate object attributes reflectively. The extra builtins look like copy-paste growth from an earlier draft of the prompt, not a deliberate requirement. Formally documenting "why" here would mean writing a justification for a permissiveness gap that has no real justification — the honest per-site audit argues for tightening `analyst.py`/`science.py`, not codifying their current whitelist as intentional.
3. **Something else — a fully separate sandbox per site, generated from a declarative policy spec (e.g. YAML listing allowed symbols per agent, loaded at runtime)** — more flexible for future agents, but adds a config-parsing layer and an extra file to keep in sync with the Python source for a project with three agents total. Rejected as premature: the flexibility this buys isn't needed yet, and it moves the security-relevant whitelist out of reviewable Python into a format `git diff` and mypy don't check as directly.

**Chosen: option 1.** It is the direction ADR-003 already flagged as preferred, it removes the maintenance/audit risk ADR-003 called out explicitly (a fix to the base sandbox landing only in one of three copies), and the research above shows the "extra" permissions each agent genuinely needs are smaller than what they currently have — unification is also an opportunity to *tighten* Analyst/Science's builtins (drop `setattr`, `vars`), not just merge them.

### Question 2 — timeout enforcement mechanism

1. **`signal.alarm()` / `SIGALRM`** — simplest API (`signal.alarm(timeout_seconds)`, catch the resulting exception). Rejected: `SIGALRM` only works in the main thread of the main interpreter. Streamlit runs each active session's script in a dedicated worker thread (`streamlit.runtime.scriptrunner`), not the process's main thread — calling `signal.alarm()` from inside a pipeline/analysis run invoked from a Streamlit session would raise `ValueError: signal only works in main thread of the main interpreter` at runtime, not silently no-op. This alone disqualifies it for this codebase. It is also Unix-only, which would leave local Windows development unprotected even if the threading problem didn't exist.
2. **`concurrent.futures.ProcessPoolExecutor` + `future.result(timeout=...)`** — clean call-site ergonomics (`executor.submit(...)`, `.result(timeout=N)` raises `TimeoutError`). Rejected as the sole mechanism: `.result(timeout=...)` only bounds how long the *caller* waits for the future — it does not stop the worker process. A hung or adversarial script keeps running inside the pool worker indefinitely, consuming CPU/memory, and (worse) that worker is not returned to the pool for reuse in a bounded way, so repeated timeouts degrade the whole pool over a session's lifetime. Actually killing the specific worker requires reaching into `ProcessPoolExecutor`'s internals (not part of its public API) or shutting down and recreating the whole pool — neither is a clean fit for a per-call sandbox boundary.
3. **`multiprocessing.Process` + explicit `.join(timeout=...)` / `.terminate()` / `.kill()`** (chosen) — run the sandboxed code in its own child process, started with an explicit `multiprocessing.get_context("spawn")` (not the platform default, which is `fork` on Linux and `spawn` on macOS since 3.8 — pinning `spawn` explicitly keeps behavior identical between local macOS dev and Railway's Linux containers, and avoids `fork()`-specific hazards like inheriting open DB connections, Streamlit's internal threads, or file descriptors into the child). The parent calls `process.join(timeout_seconds)`; if the process is still alive afterward, the parent calls `process.terminate()` (SIGTERM), gives it a short grace period, and calls `process.kill()` (SIGKILL) if it still hasn't exited. This is the only one of the three options that can actually stop a hung or runaway worker rather than merely stop waiting for it.
4. **Docker / subprocess-per-call sandboxing** — out of scope for this ADR; ADR-003 already rejected this for latency/infra-complexity reasons at the TCC's current stage, and nothing here changes that tradeoff. Left as the documented "real" production answer if/when the project needs stronger isolation than process-boundary + restricted globals.

**Chosen: option 3.** It is the only mechanism that satisfies both hard constraints this project actually has: it must work from a Streamlit worker thread (rules out `signal.alarm`), and a timeout must be able to actually stop execution, not just stop waiting for it (rules out bare `ProcessPoolExecutor.result(timeout=...)`).

## Decision

Unify all three `exec()` sites behind `core/sandbox.py`, enforce `timeout_seconds` via a dedicated `multiprocessing.Process` per call, and add lightweight per-stage latency capture that flows into a new small audit table.

### Unified sandbox interface (sketch — not the final implementation; `backend-agent-sprint2` owns the real module)

```python
# core/sandbox.py — sketch, matches how ADR-006 sketched auth_service.py

class SandboxResult(TypedDict):
    values: dict[str, Any]        # named results extracted from the executed code
    error: Optional[str]          # None on success
    timed_out: bool
    duration_seconds: float

def execute_in_sandbox(
    code: str,
    dfs: dict[str, pd.DataFrame],
    *,
    mode: Literal["function", "script"] = "function",
    entry_point: str = "transform",          # used when mode="function" (Transformer's contract)
    result_vars: Optional[list[str]] = None, # used when mode="script", e.g. ["gold_df", "fig", "narrative"]
    extra_globals: Optional[dict[str, Any]] = None,   # e.g. {"px": px, "go": go} for Analyst
    extra_builtins: Optional[dict[str, Any]] = None,  # additive on top of SAFE_BUILTINS
    timeout_seconds: int = 30,
) -> SandboxResult:
    """Execute LLM-generated code in an isolated, time-bounded child process.

    `mode="function"` preserves the Transformer's existing transform(dfs) -> pd.DataFrame
    contract. `mode="script"` covers Analyst/Science: code that assigns top-level
    variables (gold_df/fig/narrative, or predictions_df/fig/narrative/model_info)
    instead of defining a callable — result_vars names which ones to collect.

    Runs in a multiprocessing.Process (spawn context). On timeout, terminates
    (SIGTERM) then kills (SIGKILL) the child and returns
    SandboxResult(timed_out=True, error="...", values={}).
    """
```

`SAFE_BUILTINS`/`SAFE_GLOBALS` in `core/sandbox.py` remain the single shared base (unchanged from today: `pandas`/`numpy`, no `setattr`/`vars`/`open`/`__import__`/`eval`/`exec`/`compile`). `analyst.py` passes `extra_globals={"px": px, "go": go}` and no extra builtins beyond the shared base (per the Question 1 audit above — `setattr`/`vars`/`iter`/`next`/`repr`/`format` are dropped as unused). `science.py` passes the same Plotly extras plus the pre-imported sklearn/statsmodels symbols, and no extra builtins either (`slice` is dropped for the same reason).

**Why the process boundary changes the call-site shape, not just where `exec()` lives:** today `analyst.py`/`science.py` call `exec(code, exec_env)` with a single dict used as both globals and locals (documented in-code: LLM-defined nested `def`s close over globals, not a separate locals dict, so `df` must be visible there), then read `exec_env.get("gold_df")` etc. directly afterward because `exec()` mutates that dict in place. Once execution happens in a child process, the child's `exec_env` does not exist in the parent's memory — there is nothing to read after the call returns. The parent must instead receive `SandboxResult.values` back across the process boundary (via a `multiprocessing.Queue` or `Pipe` the child writes into before exiting). This means `analyst.py`/`science.py` need more than a one-line swap of `exec(...)` for `execute_in_sandbox(...)` — the code immediately after the call, which currently reads local dict keys, needs to read `result.values["gold_df"]` etc. instead. Flagged explicitly for `backend-agent-sprint2`.

### Timeout enforcement — signature and behavior notes

- `core/sandbox.py`'s `execute_in_sandbox()` already declares `timeout_seconds: int = 30` today (currently a no-op). No new parameter is introduced — the parameter starts being honored, which is a **behavior change, not a signature change**, for the one existing call site (`transformer.py`).
- `analyst.py`/`science.py` currently call `exec()` directly, not a shared function — routing them through `execute_in_sandbox()` is a new call site being introduced, not an existing signature being broken.
- **Data crossing the process boundary must be picklable.** `dfs`/`df.copy()` (pandas DataFrames), Plotly `Figure` objects, and sklearn estimator instances all pickle cleanly today. `extra_globals` values that are modules (`px`, `go`) or classes (`LinearRegression`, `ARIMA`, ...) also pickle by reference under `spawn` (Python re-imports them by qualified name in the child) — no code changes needed there, but this is worth an explicit smoke test once implemented, since it's the one part of this design not already exercised by the current in-process code. Nothing in today's globals/builtins is inherently unpicklable (no open file handles, DB connections, or LLM client objects are ever placed in `SAFE_GLOBALS`/`extra_globals`) — that invariant needs to hold going forward, since anything unpicklable added to the sandbox globals later would break silently at the process boundary rather than at review time.
- **`print()` inside sandboxed code now writes to the child process's stdout**, not the parent's. Nothing currently consumes that output (it isn't captured or logged today either), so this is a no-op functional change, but it's worth noting for anyone tempted to add `print()`-based debugging later.
- **Serialization cost.** DataFrames now cross the process boundary by value (pickle) rather than being passed by reference in-process. For the Silver-layer DataFrame sizes this project currently exercises this is not expected to be a bottleneck, but it is a real cost that didn't exist before, and worth watching if larger datasets are exercised later.

### Interaction with the Analyst/Science retry loops — explicit flag for `backend-agent-sprint2`

`analyst.py`/`science.py` each retry `for attempt in range(1, 4)`, calling `exec()` fresh on every attempt (up to 3 LLM generations, each executed once). Under the new design:

- Each attempt now pays **process-spawn overhead** (a `spawn`-context child re-imports `pandas`/`numpy`/`plotly`/`sklearn`/`statsmodels`, which is materially slower than `fork` or in-process execution) on top of the existing LLM-call latency. This is a real, measurable latency regression per attempt — expected to be on the order of a few hundred milliseconds to low seconds depending on which libraries the site imports, not milliseconds. `backend-agent-sprint2` should measure this once implemented and decide whether it's acceptable as-is or whether the child should be kept warm across retries (e.g. one long-lived worker process per `run_analyst`/`run_science` call, reused across its up-to-3 attempts) — the latter is a legitimate follow-up optimization but is **not required for correctness** and should not block this ADR's core fix (unification + real timeout enforcement) from shipping.
- A **timeout is a distinct failure mode from an exception**, but the retry loop today only has one failure channel (`last_error`, fed into `_RETRY_PREFIX` and shown to the LLM on the next attempt). A timeout should feed the same channel with a distinguishable message (e.g. `"Execution exceeded {timeout_seconds}s — simplify the computation"`) so the LLM's next attempt has a chance to produce cheaper code, rather than being treated as an opaque exception.
- **Worst-case latency compounds across the retry loop**: 3 attempts × `timeout_seconds` is a real ceiling on how long one Analyst/Science sub-task can take if the LLM repeatedly produces code that hangs (e.g. an unbounded loop, a pathological `groupby`/`merge`). At the current default of `timeout_seconds=30`, that's up to 90 seconds of sandbox time alone per sub-task, before LLM invocation latency. `backend-agent-sprint2` should consider whether `timeout_seconds` should be lower for Analyst/Science than for the Transformer (single-shot, no retry-driven compounding) — this ADR does not fix a specific number, only flags that the current default was chosen before compounding was a consideration.

## Latency instrumentation (independent of the security work above, same PR)

**Where timestamps are captured:**

- **LangGraph nodes** (`orchestrator`/`extractor`/`transformer`/`quality`/`loader`, per ADR-001): wrapped at graph-construction time in `core/graph.py`, not inside each agent function — a small `_timed(name, node_fn)` wrapper records `time.monotonic()` before and after calling `node_fn(state)`, and adds the elapsed seconds to a new `stage_durations: dict[str, float]` entry in the state update. This keeps every existing agent function untouched (`add_node("extractor", _timed("extractor", extractor_node))` instead of `add_node("extractor", extractor_node)`), consistent with this project's preference for minimal, non-invasive changes.
- **Analyst/Science**: these are not LangGraph nodes — `pipeline_service.py` calls `run_analyst()`/`run_science()` directly, once per sub-task, with `run_science_with_repair()` sometimes calling `run_science_analysis()` a second time. Latency is captured the same way at the `pipeline_service.py` call sites (wrap each `run_analyst`/`run_science_analysis` call with `time.monotonic()` before/after), not inside `analyst.py`/`science.py` themselves, for the same non-invasive reasoning.

**Schema — new table, not new columns on `runs`/`analysis_runs`:**

```sql
CREATE TABLE stage_latencies (
    id             SERIAL PRIMARY KEY,
    run_id         VARCHAR NOT NULL,        -- runs.run_id or analysis_runs.run_id, disambiguated by run_type
    run_type       VARCHAR(10) NOT NULL,    -- 'silver' | 'analysis'
    tenant_id      VARCHAR NOT NULL REFERENCES users(id),
    stage          VARCHAR(30) NOT NULL,    -- 'orchestrator'|'extractor'|'transformer'|'quality'|'loader'|'analyst'|'science'
    seq            INTEGER NOT NULL DEFAULT 1,  -- 2nd+ Analyst/Science call for the same run (repair/multi-subtask)
    duration_seconds FLOAT NOT NULL,
    timed_out      BOOLEAN NOT NULL DEFAULT FALSE,
    recorded_at    TIMESTAMPTZ NOT NULL
);
```

A new table was chosen over new columns on `runs`/`analysis_runs` because the stage set and repeat-count differ per run type and aren't fixed: the Silver pipeline always runs the same 5 LangGraph nodes exactly once, but one `analysis_runs` row can correspond to multiple Analyst/Science sub-task calls (one business question can fan out into several Gold/Science sub-tasks, per `pipeline_service.py`'s existing `gold_subtasks`/`science_subtasks` counters) plus repair-call reruns. A fixed set of columns (`extractor_seconds`, `transformer_seconds`, ...) cannot represent "N calls to Science within this analysis run" without an array column or repeated NULL-padded columns; a narrow long-format table with a `seq` counter does, and mirrors the `tenant_id`-filtered-query pattern `audit/db.py` already uses for `runs`/`analysis_runs`. It is also purely additive — no `NOT NULL` retrofit on an existing table, unlike ADR-006's `users` FK migration, so there is no backfill decision to make.

`run_type` (rather than two separate FKs to `runs.run_id` and `analysis_runs.run_id`) avoids a nullable-dual-FK shape for a table that only ever references exactly one of the two parent tables per row. An index on `(tenant_id, stage)` supports the aggregate "average latency per stage" queries this instrumentation exists for.

**Persistence call sites**: a new `save_stage_latencies(run_id, run_type, tenant_id, durations, log_dir=...)` in `audit/db.py`, following the existing `save_run()`/`save_analysis()` shape (SQLAlchemy Core, bound `tenant_id` filter, called from `pipeline_service.py` after a run/analysis completes) — the actual function signature and insert logic are `backend-agent-sprint2`'s implementation detail, not fixed here.

## Consequences

- **Positive**: closes ADR-003's "Follow-up" section — one reviewed base whitelist instead of three drifting copies; a future tightening (e.g. removing `getattr`) now applies everywhere by construction.
- **Positive**: `timeout_seconds` becomes real — a hung or adversarial sandboxed script can no longer run indefinitely against a public, multi-tenant deployment, directly closing the risk ADR-006 flagged as accepted-until-Sprint-2.
- **Positive**: the Question 1 audit narrows Analyst/Science's builtins (`setattr`/`vars`/`iter`/`next`/`repr`/`format`/`slice` dropped as unused), a net security improvement beyond mere unification.
- **Positive**: per-stage latency data lands in a queryable table, giving this project's evaluation-metrics work (thesis-relevant) real numbers instead of anecdotal "it feels slow" observations.
- **Negative**: process-per-call execution adds spawn overhead to every sandboxed call, compounded up to 3x by the Analyst/Science retry loops — a measurable latency regression that needs to be measured post-implementation, with a documented (not required) follow-up optimization (keep-warm worker) if it proves too costly.
- **Negative**: `analyst.py`/`science.py` need more than a call-site swap — the post-`exec()` code that reads results from a local dict must be rewritten to read a returned `SandboxResult` instead, since a child process's locals aren't visible to the parent. This is real refactor work, not a drop-in replacement.
- **Negative**: DataFrames, Plotly figures, and model objects now cross a process boundary by pickling, adding serialization cost and a (currently believed low, not yet empirically verified) risk that some future extra-global value isn't picklable and fails only at runtime.
- **Negative**: a new table (`stage_latencies`) is one more thing `audit/db.py` and any future analytics/dashboarding work needs to know about, though it follows an existing, already-tested pattern (`tenant_id`-filtered SQLAlchemy Core queries).

## Related

- [ADR-003](ADR-003-exec-sandbox.md) — the three-site drift and unenforced timeout this ADR resolves; superseded by this ADR's Decision (ADR-003 remains as historical record of the original three-site design).
- [ADR-006](ADR-006-clerk-auth-supabase-postgres-tenancy.md) — accepted the sandbox gap as the top open risk pending this Sprint, now that real auth + a public deploy expose it to external tenants.
- [ADR-004](ADR-004-sqlite-audit.md) — `audit/db.py`'s persistence pattern, extended here with `stage_latencies`.
- [ADR-001](ADR-001-langgraph-orchestration.md) — the `StateGraph`/node topology `stage_durations` instrumentation wraps without modifying.
- [ADR-002](ADR-002-shared-pipelinestate-typeddict.md) — `PipelineState` TypedDict contract; `stage_durations` is added as a new field there (pipeline-execution state, unlike `tenant_id` which ADR-002/ADR-006 keep outside the TypedDict as a persistence concern).
- `SECURITY.md` — full risk analysis; needs updating once this ADR's design lands in code (out of scope for this ADR itself, which is documentation-only).
- `src/ai_etl/core/sandbox.py`, `src/ai_etl/agents/analyst.py`, `src/ai_etl/agents/science.py` — the three sites this ADR unifies.
- `src/ai_etl/audit/models.py`, `src/ai_etl/audit/db.py` — existing audit-trail pattern this ADR's `stage_latencies` table follows.
