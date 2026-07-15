"""Audit persistence — saves pipeline run state to JSON and SQLite."""

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ai_etl.core.analysis_types import AdvisorResult, GoldResult, ScienceResult, TokenUsage
from ai_etl.core.state import PipelineState


def save_run(state: PipelineState, log_dir: str = "./runs") -> Path:
    """Persist the final pipeline state to JSON and record in SQLite.

    Creates:
        {log_dir}/{run_id}.json          — full state snapshot
        {log_dir}/{run_id}_transform.py  — generated transformation code (if any)
        {log_dir}/runs.db                — SQLite with run history

    Returns:
        Path to the JSON file.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    run_id = state["run_id"]
    transform_code_path: Optional[Path] = None
    if state.get("transformation_code"):
        transform_code_path = log_path / f"{run_id}_transform.py"
        transform_code_path.write_text(state["transformation_code"])

    json_path = log_path / f"{run_id}.json"
    _write_json(state, json_path, transform_code_path=transform_code_path)
    _write_sqlite(state, log_path / "runs.db")

    return json_path


def _write_json(
    state: PipelineState,
    path: Path,
    transform_code_path: Optional[Path] = None,
) -> None:
    serializable = _make_serializable(dict(state))
    if transform_code_path is not None:
        serializable["transform_code_path"] = str(transform_code_path)
    path.write_text(json.dumps(serializable, indent=2, default=str))


def _write_sqlite(state: PipelineState, db_path: Path) -> None:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                spec TEXT,
                status TEXT,
                error TEXT,
                rows_loaded INTEGER,
                timestamp TEXT
            )
        """)
        load_result = state.get("load_result")
        rows_loaded = load_result.get("rows_loaded") if load_result else None
        conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?)",
            (
                state["run_id"],
                state["spec"],
                state["status"],
                state.get("error"),
                rows_loaded,
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def save_analysis(
    run_id: str,
    gold_results: list[GoldResult],
    science_results: list[ScienceResult],
    advisor_result: AdvisorResult,
    planner_tokens: TokenUsage,
    log_dir: str = "./runs",
) -> Path:
    """Persist Gold/Science/Advisor sub-task results alongside the Silver run.

    Creates {log_dir}/{run_id}_analysis.json with narratives, model_info,
    recommendations, and a data preview for every sub-task the Planner produced.
    Figures aren't serialized (not JSON-safe, and cheap to regenerate from `code`);
    full DataFrames aren't embedded either — only a preview and shape, since the CSV
    download per sub-task already covers the full data during the session. Token
    usage is aggregated into an `analysis_runs` SQLite table so cost can be tracked
    across runs without re-parsing every JSON file.

    Without this, closing the browser tab lost every Gold/Science/Advisor result —
    only the Silver ETL state was ever persisted, which undercut the "auditable
    pipeline" pitch at exactly the layer a user is most likely to want to revisit.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    payload = {
        "run_id": run_id,
        "gold": [_serialize_analysis_result(g, "gold_df") for g in gold_results],
        "science": [_serialize_analysis_result(s, "predictions_df") for s in science_results],
        "advisor": {
            "recommendations": advisor_result.get("recommendations", []),
            "summary": advisor_result.get("summary"),
            "error": advisor_result.get("error"),
            "tokens": advisor_result.get("tokens"),
        },
        "saved_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    json_path = log_path / f"{run_id}_analysis.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False))

    total_tokens = _sum_all_tokens(gold_results, science_results, advisor_result, planner_tokens)
    _write_analysis_sqlite(
        run_id, len(gold_results), len(science_results), total_tokens, log_path / "runs.db"
    )

    return json_path


def _serialize_analysis_result(result: "GoldResult | ScienceResult", df_key: str) -> dict[str, Any]:
    df = result.get(df_key)
    serialized: dict[str, Any] = {
        "task_question": result.get("task_question"),
        "narrative": result.get("narrative"),
        "attempts": result.get("attempts"),
        "error": result.get("error"),
        "repaired": result.get("repaired", False),
        "tokens": result.get("tokens"),
    }
    if "model_info" in result:
        serialized["model_info"] = result.get("model_info")
    if isinstance(df, pd.DataFrame) and not df.empty:
        serialized["data_preview"] = df.head(20).to_dict(orient="records")
        serialized["data_shape"] = list(df.shape)
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


def _write_analysis_sqlite(
    run_id: str, n_gold: int, n_science: int, tokens: TokenUsage, db_path: Path
) -> None:
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY,
                gold_subtasks INTEGER,
                science_subtasks INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                timestamp TEXT
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                n_gold,
                n_science,
                tokens.get("input_tokens", 0),
                tokens.get("output_tokens", 0),
                tokens.get("total_tokens", 0),
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
        conn.commit()


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
