"""Audit persistence — saves pipeline run state to JSON and the application Postgres."""

import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Union

import pandas as pd
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import analysis_runs, runs, saved_pipelines, stage_latencies, users
from ai_etl.audit.storage import StorageBackend, get_storage_backend
from ai_etl.core.analysis_types import AdvisorResult, GoldResult, ScienceResult, TokenUsage
from ai_etl.core.llm import get_model_name
from ai_etl.core.pricing import compute_cost_usd
from ai_etl.core.scheduling import compute_next_run_at
from ai_etl.core.state import PipelineState


def ensure_user(user_id: str) -> None:
    """Idempotently upsert a `users` row for a verified Clerk `user_id`.

    Migration 0003 (ADR-006) made `runs.tenant_id`/`analysis_runs.tenant_id`
    NOT NULL foreign keys to `users.id`. Nothing in the Clerk sign-in flow
    otherwise creates that row, so without this call the very first
    `save_run()`/`save_analysis()` for a brand-new Clerk account fails with a
    Postgres FK violation (`IntegrityError`) — every real user's first run
    would crash. Callers must invoke this with the verified `tenant_id`
    (`auth_service.verify_session_token()`'s `user_id`) before it is used
    anywhere else that writes to `runs`/`analysis_runs`.

    `ON CONFLICT DO NOTHING` makes repeat calls for an already-known user
    (e.g. every Streamlit rerun) cheap no-ops that don't touch `created_at`.
    """
    stmt = (
        pg_insert(users)
        .values(id=user_id, created_at=datetime.now(tz=timezone.utc))
        .on_conflict_do_nothing(index_elements=[users.c.id])
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def save_run(state: PipelineState, log_dir: str = "./runs", tenant_id: str | None = None) -> str:
    """Persist the final pipeline state to JSON and record it in the app database.

    Creates (ADR-009: via the `StorageBackend` selected by `STORAGE_BACKEND` —
    `local` writes under `log_dir`, `s3` writes under the tenant/environment-scoped
    bucket prefix; key names below are unchanged either way):
        {run_id}.json          — full state snapshot
        {run_id}_transform.py  — generated transformation code (if any)
        a row in the `runs` table of the application Postgres (APP_DATABASE_URL)

    Args:
        state: Final pipeline state to persist.
        log_dir: Directory to write the JSON/transform files into (`local` backend only).
        tenant_id: Sprint A session-scoping stopgap — the browser session's UUID
            (see `app.py::_get_session_id`), not a real tenant/account. Defaults to
            `None` for backward compatibility with callers that don't pass one, but
            `app.py` should always pass a real value going forward. Also used by the
            `s3` backend as the tenant segment of the storage key prefix.

    Returns:
        The storage key of the JSON file (relative to `log_dir` for `local`, to the
        bucket prefix for `s3`).
    """
    storage = get_storage_backend(log_dir, tenant_id)

    run_id = state["run_id"]
    transform_code_key: Optional[str] = None
    if state.get("transformation_code"):
        transform_code_key = f"{run_id}_transform.py"
        storage.write_bytes(transform_code_key, state["transformation_code"].encode())

    # Sprint 3 (ADR-008): also persist the Silver DataFrame as CSV, alongside
    # the (lossy) JSON snapshot below — `_make_serializable` only keeps a
    # shape placeholder for DataFrames in `{run_id}.json`. `load_full_result`
    # reads this CSV back to reconstruct `state["transformed_data"]` for
    # `app.py::_render_results`, which async execution's polling loop can no
    # longer pass the live in-memory DataFrame to directly.
    silver_df = state.get("transformed_data")
    if isinstance(silver_df, pd.DataFrame) and not silver_df.empty:
        storage.write_bytes(f"{run_id}_silver.csv", silver_df.to_csv(index=False).encode())

    json_key = f"{run_id}.json"
    _write_json(state, storage, json_key, transform_code_key=transform_code_key)
    _write_run_row(state, tenant_id=tenant_id)

    return json_key


def _write_json(
    state: PipelineState,
    storage: StorageBackend,
    key: str,
    transform_code_key: Optional[str] = None,
) -> None:
    serializable = _make_serializable(dict(state))
    if transform_code_key is not None:
        serializable["transform_code_path"] = transform_code_key
    storage.write_bytes(key, json.dumps(serializable, indent=2, default=str).encode())


def _write_run_row(state: PipelineState, tenant_id: str | None = None) -> None:
    load_result = state.get("load_result")
    rows_loaded = load_result.get("rows_loaded") if load_result else None
    stmt = pg_insert(runs).values(
        run_id=state["run_id"],
        spec=state["spec"],
        status=state["status"],
        error=state.get("error"),
        rows_loaded=rows_loaded,
        timestamp=datetime.now(tz=timezone.utc),
        tenant_id=tenant_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[runs.c.run_id],
        set_={
            "spec": stmt.excluded.spec,
            "status": stmt.excluded.status,
            "error": stmt.excluded.error,
            "rows_loaded": stmt.excluded.rows_loaded,
            "timestamp": stmt.excluded.timestamp,
            "tenant_id": stmt.excluded.tenant_id,
        },
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def load_history(limit: int = 20, tenant_id: str | None = None) -> pd.DataFrame:
    """Return the most recent runs for the history table in `app.py`.

    Mirrors the Phase 1 `_load_history` SQLite query. Returns an empty DataFrame
    (rather than raising) if the application database is unreachable, so history
    stays a soft-fail feature — matching the original behavior when `runs.db` was
    missing or unreadable.

    Args:
        limit: Maximum number of rows to return, most recent first.
        tenant_id: Sprint A session-scoping stopgap. When not `None`, restricts
            results to that browser session's own runs via a bound-parameter
            `WHERE` clause — this is the actual fix for the cross-session history
            leak. When `None` (the default, kept for backward compatibility with
            other callers), no filter is applied. `app.py` should always pass a
            real value going forward.

    `cost_usd`/`model_name` (Sprint 3, ADR-008) come from a `LEFT OUTER JOIN`
    against `analysis_runs` on `run_id` — a Silver-only run (no business
    question asked, so `save_analysis` never ran) has no matching row and
    reads back as `NaN`/`None`, which is the correct "no analysis, no cost"
    signal, not a bug to backfill.
    """
    stmt = (
        select(
            runs.c.run_id,
            runs.c.status,
            runs.c.rows_loaded,
            runs.c.timestamp,
            func.substr(runs.c.spec, 1, 80).label("spec"),
            analysis_runs.c.cost_usd,
            analysis_runs.c.model_name,
        )
        .select_from(runs.outerjoin(analysis_runs, runs.c.run_id == analysis_runs.c.run_id))
        .order_by(runs.c.timestamp.desc())
        .limit(limit)
    )
    if tenant_id is not None:
        stmt = stmt.where(runs.c.tenant_id == tenant_id)
    try:
        with get_engine().connect() as conn:
            return pd.read_sql(stmt, conn)
    except Exception:
        return pd.DataFrame()


def save_analysis(
    run_id: str,
    gold_results: list[GoldResult],
    science_results: list[ScienceResult],
    advisor_result: AdvisorResult,
    planner_tokens: TokenUsage,
    log_dir: str = "./runs",
    tenant_id: str | None = None,
    business_question: str = "",
) -> str:
    """Persist Gold/Science/Advisor sub-task results alongside the Silver run.

    Creates {run_id}_analysis.json (ADR-009: via the tenant-scoped `StorageBackend`,
    same as `save_run`) with narratives, model_info,
    recommendations, and a data preview for every sub-task the Planner produced.
    Figures aren't serialized (not JSON-safe, and cheap to regenerate from `code`);
    full DataFrames aren't embedded either — only a preview and shape, since the CSV
    download per sub-task already covers the full data during the session. Token
    usage is aggregated into an `analysis_runs` table in the application Postgres so
    cost can be tracked across runs without re-parsing every JSON file.

    Without this, closing the browser tab lost every Gold/Science/Advisor result —
    only the Silver ETL state was ever persisted, which undercut the "auditable
    pipeline" pitch at exactly the layer a user is most likely to want to revisit.

    Args:
        tenant_id: Sprint A session-scoping stopgap — the browser session's UUID.
            Defaults to `None` for backward compatibility; `app.py` should always
            pass a real value going forward.
        business_question: Sprint 3 addition — persisted so `load_full_result` can
            reconstruct the header `_render_results` shows ("O que fazer sobre:
            ..."). Defaults to `""` for backward compatibility with existing callers.
    """
    storage = get_storage_backend(log_dir, tenant_id)

    payload = {
        "run_id": run_id,
        "question": business_question,
        "gold": [
            _serialize_analysis_result(g, "gold_df", storage, f"{run_id}_gold_{i}")
            for i, g in enumerate(gold_results)
        ],
        "science": [
            _serialize_analysis_result(s, "predictions_df", storage, f"{run_id}_science_{i}")
            for i, s in enumerate(science_results)
        ],
        "advisor": {
            "recommendations": advisor_result.get("recommendations", []),
            "summary": advisor_result.get("summary"),
            "error": advisor_result.get("error"),
            "tokens": advisor_result.get("tokens"),
        },
        "saved_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    json_key = f"{run_id}_analysis.json"
    storage.write_bytes(
        json_key, json.dumps(payload, indent=2, default=str, ensure_ascii=False).encode()
    )

    total_tokens = _sum_all_tokens(gold_results, science_results, advisor_result, planner_tokens)
    _write_analysis_row(run_id, len(gold_results), len(science_results), total_tokens, tenant_id)

    return json_key


def _serialize_analysis_result(
    result: "GoldResult | ScienceResult", df_key: str, storage: StorageBackend, file_prefix: str
) -> dict[str, Any]:
    """Build one gold/science manifest entry.

    `data_preview`/`data_shape` are the original lossy-but-JSON-embedded preview
    (unchanged, still useful for `st.json(...)` debugging in the History tab's
    raw view). Sprint 3 adds `data_path`/`fig_path`: the full DataFrame (CSV) and
    Plotly `Figure` (`fig.to_json()`) persisted as sibling files, not embedded —
    `load_full_result` reads them back to reconstruct the exact objects
    `app.py::_render_results` renders. A missing df/fig simply omits that key
    from the manifest; the reload step treats that as "not available" the same
    way a live run with no chart/data does today, not as an error.
    """
    df = result.get(df_key)
    fig = result.get("fig")
    serialized: dict[str, Any] = {
        "task_question": result.get("task_question"),
        "narrative": result.get("narrative"),
        "attempts": result.get("attempts"),
        "error": result.get("error"),
        "repaired": result.get("repaired", False),
        "tokens": result.get("tokens"),
        # Sprint 7: the generated pandas/sklearn code (`agents/analyst.py`/
        # `science.py`'s `"code"` key, ADR-007's sandbox `exec()`s it) was never
        # persisted here — only ever available in-memory for a synchronous run,
        # which stopped being how this ran once Sprint 3 made execution async.
        # `app.py`'s old "Código Gold/Science" tab read this same key and had
        # silently shown nothing since Sprint 3 as a result. Plain string,
        # cheap to keep inline rather than a sibling file like data/fig.
        "code": result.get("code"),
    }
    if "model_info" in result:
        serialized["model_info"] = result.get("model_info")
    if isinstance(df, pd.DataFrame) and not df.empty:
        serialized["data_preview"] = df.head(20).to_dict(orient="records")
        serialized["data_shape"] = list(df.shape)
        data_key = f"{file_prefix}.csv"
        storage.write_bytes(data_key, df.to_csv(index=False).encode())
        serialized["data_path"] = data_key
    if fig is not None:
        fig_key = f"{file_prefix}_fig.json"
        storage.write_bytes(fig_key, fig.to_json().encode())
        serialized["fig_path"] = fig_key
    return serialized


def _sum_all_tokens(
    gold_results: list[GoldResult],
    science_results: list[ScienceResult],
    advisor_result: AdvisorResult,
    planner_tokens: TokenUsage,
) -> TokenUsage:
    per_task_tokens = [g.get("tokens", {}) for g in gold_results]
    per_task_tokens += [s.get("tokens", {}) for s in science_results]
    per_task_tokens.append(advisor_result.get("tokens", {}))
    per_task_tokens.append(planner_tokens)

    total: TokenUsage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for tokens in per_task_tokens:
        total["input_tokens"] += tokens.get("input_tokens", 0)
        total["output_tokens"] += tokens.get("output_tokens", 0)
        total["total_tokens"] += tokens.get("total_tokens", 0)
    return total


def _write_analysis_row(
    run_id: str,
    n_gold: int,
    n_science: int,
    tokens: TokenUsage,
    tenant_id: str | None = None,
) -> None:
    # Sprint 3 (ADR-008): model_name/cost_usd computed here, not threaded
    # through as a caller-supplied argument — get_model_name() reads the same
    # AI_ETL_LLM_MODEL env var every agent call in this run already used (see
    # core/llm.py::get_llm()), so it's the correct model for this row without
    # widening save_analysis()'s signature for every existing caller.
    model_name = get_model_name()
    cost_usd = compute_cost_usd(model_name, tokens)

    stmt = pg_insert(analysis_runs).values(
        run_id=run_id,
        gold_subtasks=n_gold,
        science_subtasks=n_science,
        input_tokens=tokens.get("input_tokens", 0),
        output_tokens=tokens.get("output_tokens", 0),
        total_tokens=tokens.get("total_tokens", 0),
        timestamp=datetime.now(tz=timezone.utc),
        tenant_id=tenant_id,
        model_name=model_name,
        cost_usd=cost_usd,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[analysis_runs.c.run_id],
        set_={
            "gold_subtasks": stmt.excluded.gold_subtasks,
            "science_subtasks": stmt.excluded.science_subtasks,
            "input_tokens": stmt.excluded.input_tokens,
            "output_tokens": stmt.excluded.output_tokens,
            "total_tokens": stmt.excluded.total_tokens,
            "timestamp": stmt.excluded.timestamp,
            "tenant_id": stmt.excluded.tenant_id,
            "model_name": stmt.excluded.model_name,
            "cost_usd": stmt.excluded.cost_usd,
        },
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def save_stage_latencies(
    run_id: str,
    run_type: str,
    tenant_id: str | None,
    durations: Union[dict[str, float], list[dict[str, Any]]],
    log_dir: str = "./runs",
) -> None:
    """Persist per-stage wall-clock durations to the `stage_latencies` table (ADR-007).

    No JSON artifact is written here (unlike `save_run`/`save_analysis`) — `log_dir`
    is accepted only for signature symmetry with those two, since `stage_latencies`
    is pure aggregate timing data with no per-run file of its own.

    Args:
        run_id: `runs.run_id` (run_type="silver") or `analysis_runs.run_id`
            (run_type="analysis") this batch of timings belongs to.
        run_type: "silver" | "analysis".
        tenant_id: Same Sprint A/ADR-006 tenant-scoping stopgap as save_run/save_analysis.
        durations: either
            - {"stage_name": seconds} — Silver's `state["stage_durations"]`, one entry
              per LangGraph node, each run exactly once (seq is always 1), or
            - a list of {"stage", "duration_seconds", "timed_out"} dicts in call
              order — Analyst/Science, where a business question can fan out into
              several sub-tasks plus repair reruns; repeat calls for the same stage
              get an incrementing `seq` (e.g. a repair call is seq=2).
        log_dir: unused; kept for signature symmetry with save_run/save_analysis.
    """
    rows: list[dict[str, Any]] = []
    if isinstance(durations, dict):
        for stage, seconds in durations.items():
            rows.append(
                {"stage": stage, "duration_seconds": float(seconds), "timed_out": False, "seq": 1}
            )
    else:
        seq_counters: dict[str, int] = {}
        for entry in durations:
            stage = entry["stage"]
            seq_counters[stage] = seq_counters.get(stage, 0) + 1
            rows.append(
                {
                    "stage": stage,
                    "duration_seconds": float(entry["duration_seconds"]),
                    "timed_out": bool(entry.get("timed_out", False)),
                    "seq": seq_counters[stage],
                }
            )

    if not rows:
        return

    now = datetime.now(tz=timezone.utc)
    stmt = insert(stage_latencies).values(
        [
            {
                "run_id": run_id,
                "run_type": run_type,
                "tenant_id": tenant_id,
                "stage": row["stage"],
                "seq": row["seq"],
                "duration_seconds": row["duration_seconds"],
                "timed_out": row["timed_out"],
                "recorded_at": now,
            }
            for row in rows
        ]
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def _run_belongs_to_tenant(run_id: str, tenant_id: str) -> bool:
    """Server-side ownership check for `load_full_result` — queries `runs`
    directly rather than trusting a `run_id` the caller already claims is
    tenant-scoped. Soft-fails to `False` (i.e. "not authorized") on any DB
    error, matching this module's existing soft-fail style — a reload should
    never leak data just because the ownership check itself couldn't run."""
    try:
        stmt = select(runs.c.run_id).where(runs.c.run_id == run_id, runs.c.tenant_id == tenant_id)
        with get_engine().connect() as conn:
            return conn.execute(stmt).first() is not None
    except Exception:
        return False


def load_full_result(
    run_id: str, log_dir: str = "./runs", tenant_id: str | None = None
) -> Optional[dict[str, Any]]:
    """Reconstruct the `AnalysisRunResult`-shaped dict `app.py::_render_results`
    expects, from the artifacts `save_run`/`save_analysis` persist to disk.

    Sprint 3 (ADR-008) context: `run_full_analysis_task`'s Celery return value is
    a small JSON-safe summary only (DataFrames/Figures don't cross the task's
    process boundary — see `services/execution_queue.py`'s docstring). The full
    result was always durably persisted as a side effect of `save_run`/
    `save_analysis` for the History tab's raw-JSON view; this function is what
    actually reconstructs it into a shape `_render_results` can render, for both
    the History tab and a completed async run.

    Security note (added on review): this reads artifacts straight off disk by
    `run_id`, with no inherent ownership check. `app.py`'s only caller today
    passes a `run_id` sourced from `load_history(..., tenant_id=...)`, so a
    tenant can normally only ever select their own runs — but that's a UI-layer
    restriction, not a data-layer one, and this project has already shipped
    (and fixed) exactly one cross-tenant data leak before (Sprint A). When
    `tenant_id` is given, ownership is verified against `runs.tenant_id` before
    any file is read, returning `None` (same soft-fail shape as "unknown run
    id") on mismatch — a second, defense-in-depth check that doesn't rely on
    the caller never changing how `run_id` is sourced. `tenant_id` defaults to
    `None` for callers that intentionally operate without tenant scoping (none
    exist in `app.py` today; kept optional rather than required so this stays
    a additive, non-breaking signature change).

    Returns `None` if `{run_id}.json` doesn't exist (unknown run id), or if
    `tenant_id` is given and doesn't match the run's owner — mirrors
    `load_history`'s soft-fail style rather than raising.

    `bronze` is always `None` on reload: the originally-uploaded file only ever
    existed in the browser session's memory and was never persisted (unchanged
    from before Sprint 3 — `_render_results` already renders a missing bronze_df
    as "not available" rather than erroring).

    Reload is best-effort per sub-task artifact: a missing/corrupt CSV or figure
    JSON for one gold/science entry degrades just that entry (data/figure
    omitted, narrative/model_info still shown) rather than failing the whole
    reload — matches `load_history`'s "soft-fail, don't block the UI" philosophy.
    """
    if tenant_id is not None and not _run_belongs_to_tenant(run_id, tenant_id):
        return None

    storage = get_storage_backend(log_dir, tenant_id)
    json_key = f"{run_id}.json"
    if not storage.exists(json_key):
        return None

    state: dict[str, Any] = json.loads(storage.read_bytes(json_key))
    silver_key = f"{run_id}_silver.csv"
    if storage.exists(silver_key):
        try:
            state["transformed_data"] = pd.read_csv(io.BytesIO(storage.read_bytes(silver_key)))
        except Exception:  # nosec B110 — best-effort reload; a corrupt CSV degrades
            pass  # to a missing Silver tab, not a failed reload of the whole run.

    gold: list[dict[str, Any]] = []
    science: list[dict[str, Any]] = []
    advisor: dict[str, Any] = {}
    question = ""

    analysis_key = f"{run_id}_analysis.json"
    if storage.exists(analysis_key):
        analysis = json.loads(storage.read_bytes(analysis_key))
        question = analysis.get("question", "")
        gold = [_reload_analysis_entry(e, storage, "gold_df") for e in analysis.get("gold", [])]
        science = [
            _reload_analysis_entry(e, storage, "predictions_df")
            for e in analysis.get("science", [])
        ]
        advisor = analysis.get("advisor", {})

    return {
        "bronze": None,
        "state": state,
        "gold": gold,
        "science": science,
        "advisor": advisor,
        "question": question,
        "tokens": _load_analysis_tokens(run_id),
    }


def _reload_analysis_entry(
    entry: dict[str, Any], storage: StorageBackend, df_key: str
) -> dict[str, Any]:
    """Re-hydrate one gold/science manifest entry: read back the full DataFrame
    (`data_path`) and Plotly `Figure` (`fig_path`) `_serialize_analysis_result`
    persisted alongside the JSON-safe preview, if present and readable."""
    reloaded = dict(entry)

    data_key = entry.get("data_path")
    if data_key and storage.exists(data_key):
        try:
            reloaded[df_key] = pd.read_csv(io.BytesIO(storage.read_bytes(data_key)))
        except Exception:  # nosec B110 — best-effort per sub-task; a corrupt
            pass  # CSV omits that entry's data, not the whole reload.

    fig_key = entry.get("fig_path")
    if fig_key and storage.exists(fig_key):
        try:
            import plotly.io as pio

            reloaded["fig"] = pio.from_json(storage.read_bytes(fig_key).decode())
        except Exception:  # nosec B110 — best-effort per sub-task; a corrupt
            pass  # figure JSON omits that entry's chart, not the whole reload.

    return reloaded


def _load_analysis_tokens(run_id: str) -> TokenUsage:
    """Read the already-aggregated token totals back from `analysis_runs`
    (written by `_write_analysis_row`), rather than re-summing every
    sub-task's `tokens` field from the JSON manifest a second time. Soft-fails
    to all-zero on any DB error, matching `load_history`'s pattern — a reload
    should never hard-fail just because the aggregate row is unreachable."""
    try:
        stmt = select(
            analysis_runs.c.input_tokens,
            analysis_runs.c.output_tokens,
            analysis_runs.c.total_tokens,
        ).where(analysis_runs.c.run_id == run_id)
        with get_engine().connect() as conn:
            row = conn.execute(stmt).first()
        if row is None:
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return {
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
        }
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _saved_pipeline_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "source_type": row.source_type,
        "spec": row.spec,
        "business_question": row.business_question,
        "cron_schedule": row.cron_schedule,
        "is_active": row.is_active,
        "next_run_at": row.next_run_at,
        "last_task_id": row.last_task_id,
        "last_run_at": row.last_run_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_saved_pipeline(
    tenant_id: str,
    name: str,
    source_type: str,
    spec: str,
    cron_schedule: str,
    business_question: str = "",
) -> dict[str, Any]:
    """Persist a new saved pipeline (Sprint 13, ADR-016).

    Caller (the `/pipelines` router) is responsible for validating
    `cron_schedule` (`core.scheduling.validate_cron_schedule`) and
    `source_type` (`core.scheduling.SCHEDULABLE_SOURCE_TYPES`, ADR-016
    Decision 3) before calling this — this function trusts its inputs,
    matching `save_run`/`save_analysis`'s existing "no revalidation at the
    persistence layer" pattern.
    """
    now = datetime.now(tz=timezone.utc)
    pipeline_id = str(uuid.uuid4())
    next_run_at = compute_next_run_at(cron_schedule, now)
    stmt = insert(saved_pipelines).values(
        id=pipeline_id,
        tenant_id=tenant_id,
        name=name,
        source_type=source_type,
        spec=spec,
        business_question=business_question,
        cron_schedule=cron_schedule,
        is_active=True,
        next_run_at=next_run_at,
        last_task_id=None,
        last_run_at=None,
        created_at=now,
        updated_at=now,
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)
    return {
        "id": pipeline_id,
        "tenant_id": tenant_id,
        "name": name,
        "source_type": source_type,
        "spec": spec,
        "business_question": business_question,
        "cron_schedule": cron_schedule,
        "is_active": True,
        "next_run_at": next_run_at,
        "last_task_id": None,
        "last_run_at": None,
        "created_at": now,
        "updated_at": now,
    }


def list_saved_pipelines(tenant_id: str) -> list[dict[str, Any]]:
    """Return every saved pipeline belonging to `tenant_id`, most recently updated first."""
    stmt = (
        select(saved_pipelines)
        .where(saved_pipelines.c.tenant_id == tenant_id)
        .order_by(saved_pipelines.c.updated_at.desc())
    )
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_saved_pipeline_row_to_dict(row) for row in rows]


def get_saved_pipeline(pipeline_id: str, tenant_id: str) -> Optional[dict[str, Any]]:
    """Return one saved pipeline, scoped to `tenant_id` — `None` covers both
    "unknown id" and "belongs to another tenant" (same soft-fail shape as
    `load_full_result`'s ownership check)."""
    stmt = select(saved_pipelines).where(
        saved_pipelines.c.id == pipeline_id, saved_pipelines.c.tenant_id == tenant_id
    )
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    return _saved_pipeline_row_to_dict(row) if row is not None else None


def update_saved_pipeline(
    pipeline_id: str,
    tenant_id: str,
    name: Optional[str] = None,
    source_type: Optional[str] = None,
    spec: Optional[str] = None,
    cron_schedule: Optional[str] = None,
    business_question: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    """Partial update (PATCH semantics) — only fields passed as non-`None` change.

    Recomputes `next_run_at` whenever `cron_schedule` changes, or whenever a
    paused pipeline (`is_active=False -> True`) is resumed — a schedule that
    was paused for a week should fire from "now", not immediately catch up
    on every tick it missed while paused.

    Returns the updated row, or `None` if `pipeline_id` doesn't exist or
    doesn't belong to `tenant_id` (ownership check happens first, before any
    write — same pattern as `_run_belongs_to_tenant`).
    """
    existing = get_saved_pipeline(pipeline_id, tenant_id)
    if existing is None:
        return None

    now = datetime.now(tz=timezone.utc)
    values: dict[str, Any] = {"updated_at": now}
    if name is not None:
        values["name"] = name
    if source_type is not None:
        values["source_type"] = source_type
    if spec is not None:
        values["spec"] = spec
    if business_question is not None:
        values["business_question"] = business_question

    resolved_cron = cron_schedule if cron_schedule is not None else existing["cron_schedule"]
    became_active = is_active is True and existing["is_active"] is False
    if cron_schedule is not None:
        values["cron_schedule"] = cron_schedule
    if is_active is not None:
        values["is_active"] = is_active
    if cron_schedule is not None or became_active:
        values["next_run_at"] = compute_next_run_at(resolved_cron, now)

    stmt = (
        update(saved_pipelines)
        .where(saved_pipelines.c.id == pipeline_id, saved_pipelines.c.tenant_id == tenant_id)
        .values(**values)
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)
    return get_saved_pipeline(pipeline_id, tenant_id)


def list_due_pipelines(now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Return every active saved pipeline (across all tenants) whose
    `next_run_at` has passed — the query `services/scheduler.py`'s Celery
    beat task runs on each tick. No `tenant_id` filter: the beat task itself
    isn't acting on behalf of any one tenant."""
    cutoff = now or datetime.now(tz=timezone.utc)
    stmt = select(saved_pipelines).where(
        saved_pipelines.c.is_active.is_(True), saved_pipelines.c.next_run_at <= cutoff
    )
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_saved_pipeline_row_to_dict(row) for row in rows]


def mark_pipeline_fired(pipeline_id: str, task_id: str, next_run_at: datetime) -> None:
    """Record that a saved pipeline just fired: advance `next_run_at` and
    remember `last_task_id`/`last_run_at`. Called by `services/scheduler.py`
    immediately after `enqueue_analysis` succeeds for this pipeline — a
    failed `enqueue_analysis` call (e.g. rate limit) must not advance
    `next_run_at`, so the caller only invokes this on success.

    `task_id` is the Celery task id `enqueue_analysis` returns, not yet the
    eventual `runs.run_id` (generated later, inside the task itself) — same
    distinction `GET /runs/{task_id}/status` already relies on."""
    now = datetime.now(tz=timezone.utc)
    stmt = (
        update(saved_pipelines)
        .where(saved_pipelines.c.id == pipeline_id)
        .values(last_task_id=task_id, last_run_at=now, next_run_at=next_run_at, updated_at=now)
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def _make_serializable(obj: Any) -> Any:
    """Recursively convert non-serializable objects (DataFrames, etc.) to strings."""
    import pandas as pd

    if isinstance(obj, pd.DataFrame):
        return f"<DataFrame shape={obj.shape}>"
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(item) for item in obj]
    return obj
