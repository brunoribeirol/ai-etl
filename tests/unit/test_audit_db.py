"""Unit tests for audit persistence (db.py)."""

import json
import sqlite3
from pathlib import Path

import pandas as pd

from ai_etl.audit.db import save_analysis, save_run
from ai_etl.core.state import initial_state

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


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


# ---------------------------------------------------------------------------
# save_analysis
# ---------------------------------------------------------------------------


def _make_gold_result(question: str = "Quais os KPIs?") -> dict:
    return {
        "task_question": question,
        "gold_df": pd.DataFrame({"product": ["A", "B"], "total": [10, 20]}),
        "fig": None,
        "narrative": "Produto B lidera.",
        "code": "gold_df = df",
        "attempts": 1,
        "error": None,
        "tokens": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }


def _make_science_result(question: str = "Qual a previsão?") -> dict:
    return {
        "task_question": question,
        "predictions_df": pd.DataFrame({"month": ["Jan"], "predicted": [42.0]}),
        "fig": None,
        "narrative": "Tendência estável.",
        "model_info": {"model_type": "LinearRegression", "task": "regression"},
        "code": "predictions_df = df",
        "attempts": 1,
        "error": None,
        "tokens": {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
    }


def _make_advisor_result() -> dict:
    return {
        "recommendations": [
            {
                "action": "Investir no produto B",
                "rationale": "Lidera em receita.",
                "priority": "high",
                "expected_impact": "10% de crescimento",
            }
        ],
        "summary": "Foco no produto B.",
        "error": None,
        "tokens": {"input_tokens": 50, "output_tokens": 20, "total_tokens": 70},
    }


def test_save_analysis_creates_json_file(tmp_path: Path) -> None:
    json_path = save_analysis(
        "run-analysis-1",
        [_make_gold_result()],
        [_make_science_result()],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["run_id"] == "run-analysis-1"
    assert len(data["gold"]) == 1
    assert data["gold"][0]["task_question"] == "Quais os KPIs?"
    assert data["gold"][0]["narrative"] == "Produto B lidera."
    assert len(data["science"]) == 1
    assert data["advisor"]["summary"] == "Foco no produto B."


def test_save_analysis_includes_data_preview(tmp_path: Path) -> None:
    json_path = save_analysis(
        "run-analysis-2",
        [_make_gold_result()],
        [],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    data = json.loads(json_path.read_text())
    assert data["gold"][0]["data_shape"] == [2, 2]
    assert len(data["gold"][0]["data_preview"]) == 2


def test_save_analysis_aggregates_tokens_in_sqlite(tmp_path: Path) -> None:
    planner_tokens = {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40}
    save_analysis(
        "run-analysis-3",
        [_make_gold_result()],
        [_make_science_result()],
        _make_advisor_result(),
        planner_tokens,
        log_dir=str(tmp_path),
    )

    conn = sqlite3.connect(tmp_path / "runs.db")
    row = conn.execute(
        "SELECT gold_subtasks, science_subtasks, input_tokens, output_tokens, total_tokens "
        "FROM analysis_runs WHERE run_id = 'run-analysis-3'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 1  # gold_subtasks
    assert row[1] == 1  # science_subtasks
    # 100 (gold) + 200 (science) + 50 (advisor) + 30 (planner) = 380
    assert row[2] == 380
    assert row[3] == 50 + 80 + 20 + 10  # output tokens
    assert row[4] == 150 + 280 + 70 + 40  # total tokens


def test_save_analysis_handles_empty_results(tmp_path: Path) -> None:
    json_path = save_analysis(
        "run-analysis-4", [], [], _make_advisor_result(), _ZERO_TOKENS, log_dir=str(tmp_path)
    )

    data = json.loads(json_path.read_text())
    assert data["gold"] == []
    assert data["science"] == []


def test_save_analysis_marks_repaired_subtasks(tmp_path: Path) -> None:
    repaired_gold = {**_make_gold_result(), "repaired": True}
    json_path = save_analysis(
        "run-analysis-5",
        [repaired_gold],
        [],
        _make_advisor_result(),
        _ZERO_TOKENS,
        log_dir=str(tmp_path),
    )

    data = json.loads(json_path.read_text())
    assert data["gold"][0]["repaired"] is True
