"""Unit tests for audit persistence (db.py)."""

import json
import sqlite3
from pathlib import Path

from ai_etl.audit.db import save_run
from ai_etl.core.state import initial_state


def _make_completed_state() -> dict:
    state = initial_state(spec="load sales.csv", run_id="run-abc-123")
    return {
        **state,
        "status": "completed",
        "load_result": {
            "rows_loaded": 42,
            "destination": "output.csv",
            "timestamp": "2026-06-22T00:00:00+00:00",
        },
    }


def _make_failed_state() -> dict:
    state = initial_state(spec="bad spec", run_id="run-failed-1")
    return {**state, "status": "failed", "error": "LLM failed"}


def test_save_run_creates_json_file(tmp_path: Path) -> None:
    state = _make_completed_state()
    json_path = save_run(state, log_dir=str(tmp_path))

    assert json_path.exists()
    assert json_path.suffix == ".json"
    data = json.loads(json_path.read_text())
    assert data["run_id"] == "run-abc-123"
    assert data["status"] == "completed"


def test_save_run_creates_sqlite_db(tmp_path: Path) -> None:
    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path))

    db_path = tmp_path / "runs.db"
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT * FROM runs WHERE run_id = 'run-abc-123'").fetchone()
    conn.close()

    assert row is not None
    assert row[2] == "completed"  # status
    assert row[4] == 42  # rows_loaded


def test_save_run_failed_state_has_null_rows_loaded(tmp_path: Path) -> None:
    state = _make_failed_state()
    save_run(state, log_dir=str(tmp_path))

    db_path = tmp_path / "runs.db"
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT rows_loaded FROM runs WHERE run_id = 'run-failed-1'").fetchone()
    conn.close()

    assert row[0] is None


def test_save_run_json_serializes_dataframe(tmp_path: Path) -> None:
    import pandas as pd

    state = _make_completed_state()
    state["transformed_data"] = pd.DataFrame({"a": [1, 2]})
    json_path = save_run(state, log_dir=str(tmp_path))

    data = json.loads(json_path.read_text())
    assert "<DataFrame" in data["transformed_data"]


def test_save_run_creates_log_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "runs"
    state = _make_completed_state()
    save_run(state, log_dir=str(nested))
    assert nested.exists()


def test_save_run_saves_transform_code_as_py(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformation_code"] = "def transform(dfs):\n    return dfs['x']\n"
    save_run(state, log_dir=str(tmp_path))

    py_path = tmp_path / "run-abc-123_transform.py"
    assert py_path.exists()
    assert "def transform" in py_path.read_text()


def test_save_run_json_includes_transform_code_path(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformation_code"] = "def transform(dfs):\n    return dfs['x']\n"
    json_path = save_run(state, log_dir=str(tmp_path))

    data = json.loads(json_path.read_text())
    assert "transform_code_path" in data
    assert data["transform_code_path"].endswith("_transform.py")


def test_save_run_no_py_file_when_no_transform_code(tmp_path: Path) -> None:
    state = _make_completed_state()
    state["transformation_code"] = ""
    save_run(state, log_dir=str(tmp_path))

    py_files = list(tmp_path.glob("*_transform.py"))
    assert py_files == []
