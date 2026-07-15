"""Unit tests for audit persistence (db.py) — JSON-file behavior only.

Row-level Postgres behavior (upsert semantics, token aggregation, history
ordering) is covered by tests/integration/test_audit_persistence.py against a
live application database, since it isn't meaningful to fake with a mock
without duplicating the SQL. Here, `_write_run_row`/`_write_analysis_row` are
monkeypatched to no-ops so these tests exercise only the JSON-writing path
and don't require a database.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from ai_etl.audit import db
from ai_etl.audit.db import save_analysis, save_run
from ai_etl.core.state import initial_state

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_write_run_row", lambda state: None)
    monkeypatch.setattr(db, "_write_analysis_row", lambda *args: None)


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


def test_save_run_writes_row_with_correct_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(db, "_write_run_row", lambda state: captured.update(state))

    state = _make_completed_state()
    save_run(state, log_dir=str(tmp_path))

    assert captured["run_id"] == "run-abc-123"
    assert captured["status"] == "completed"
    assert captured["load_result"]["rows_loaded"] == 42


def test_save_run_failed_state_has_no_load_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(db, "_write_run_row", lambda state: captured.update(state))

    state = _make_failed_state()
    save_run(state, log_dir=str(tmp_path))

    assert captured.get("load_result") is None


def test_save_run_json_serializes_dataframe(tmp_path: Path) -> None:
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


def test_save_analysis_aggregates_tokens_before_writing_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_write(run_id: str, n_gold: int, n_science: int, tokens: dict) -> None:
        captured.update(run_id=run_id, n_gold=n_gold, n_science=n_science, tokens=tokens)

    monkeypatch.setattr(db, "_write_analysis_row", fake_write)

    planner_tokens = {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40}
    save_analysis(
        "run-analysis-3",
        [_make_gold_result()],
        [_make_science_result()],
        _make_advisor_result(),
        planner_tokens,
        log_dir=str(tmp_path),
    )

    assert captured["n_gold"] == 1
    assert captured["n_science"] == 1
    # 100 (gold) + 200 (science) + 50 (advisor) + 30 (planner) = 380
    assert captured["tokens"]["input_tokens"] == 380
    assert captured["tokens"]["output_tokens"] == 50 + 80 + 20 + 10
    assert captured["tokens"]["total_tokens"] == 150 + 280 + 70 + 40


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
