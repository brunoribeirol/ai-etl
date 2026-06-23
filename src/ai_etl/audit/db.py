"""Audit persistence — saves pipeline run state to JSON and SQLite."""

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
