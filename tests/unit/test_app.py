"""Tests for app.py — the Streamlit UI.

Two layers of coverage:
1. Pure helper functions, imported directly and unit-tested like any other module.
2. An `AppTest` smoke test that actually boots the app end-to-end in-process. This is
   the test that would have caught the `ModuleNotFoundError` and `TypeError` incidents
   found while manually driving the app with a browser this session — those were both
   failures at script-execution time, invisible to any test that only imports
   individual functions instead of running the file the way Streamlit does.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import app as app_module

# ---------------------------------------------------------------------------
# _auto_generate_spec
# ---------------------------------------------------------------------------


def test_auto_generate_spec_includes_file_path_and_columns() -> None:
    df = pd.DataFrame({"name": ["A"], "price": [1.0]})
    spec = app_module._auto_generate_spec(Path("runs/uploads/abc123.csv"), df, Path("out.csv"))

    assert "runs/uploads/abc123.csv" in spec
    assert "name, price" in spec
    assert "out.csv" in spec


def test_auto_generate_spec_includes_business_question_hint() -> None:
    df = pd.DataFrame({"a": [1]})
    spec = app_module._auto_generate_spec(
        Path("f.csv"), df, Path("out.csv"), business_question="Quais produtos vendem mais?"
    )

    assert "Quais produtos vendem mais?" in spec


def test_auto_generate_spec_forbids_fabricating_critical_fields() -> None:
    """Regression guard: this instruction is what stopped the Transformer from
    inventing a fake row (fillna("Unknown") etc.) for missing name/category/price."""
    df = pd.DataFrame({"name": ["A"]})
    spec = app_module._auto_generate_spec(Path("f.csv"), df, Path("out.csv"))

    assert "Do NOT fabricate values" in spec
    assert "is_incomplete" in spec


def test_auto_generate_spec_reports_row_and_column_counts() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    spec = app_module._auto_generate_spec(Path("f.csv"), df, Path("out.csv"))

    assert "3 rows and 2 columns" in spec


# ---------------------------------------------------------------------------
# _save_upload_to_temp
# ---------------------------------------------------------------------------


def test_save_upload_to_temp_writes_file_with_short_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    uploaded = MagicMock()
    uploaded.name = "sales.csv"
    uploaded.getvalue.return_value = b"a,b\n1,2\n"

    dest = app_module._save_upload_to_temp(uploaded)

    assert dest.exists()
    assert dest.read_bytes() == b"a,b\n1,2\n"
    assert dest.suffix == ".csv"
    # Short id (regression guard): a 36-char UUID risks LLM transcription typos when
    # the Orchestrator has to copy this path verbatim into the plan JSON.
    assert len(dest.stem) <= 12


def test_save_upload_to_temp_preserves_extension(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    uploaded = MagicMock()
    uploaded.name = "data.xlsx"
    uploaded.getvalue.return_value = b"binary"

    dest = app_module._save_upload_to_temp(uploaded)

    assert dest.suffix == ".xlsx"


# ---------------------------------------------------------------------------
# _sum_run_tokens
# ---------------------------------------------------------------------------


def test_sum_run_tokens_aggregates_across_all_calls() -> None:
    gold_results = [{"tokens": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}]
    science_results = [{"tokens": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}}]
    advisor_result = {"tokens": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}}
    planner_tokens = {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4}

    total = app_module._sum_run_tokens(
        gold_results, science_results, advisor_result, planner_tokens
    )

    assert total == {"input_tokens": 38, "output_tokens": 16, "total_tokens": 54}


def test_sum_run_tokens_handles_missing_tokens_key() -> None:
    total = app_module._sum_run_tokens([{}], [{}], {}, {})

    assert total == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# App boot smoke test (AppTest — runs the real Streamlit script in-process)
# ---------------------------------------------------------------------------


def test_app_boots_without_exception() -> None:
    """The app must import and render top-to-bottom with no unhandled exception.

    This is the test that would have caught `ModuleNotFoundError: No module named
    'ai_etl'` (a broken editable install) and the `TypeError: 'NoneType' object is
    not subscriptable` (an `err[:80]` call on a None error message) — both were only
    visible once the script actually ran, not from unit-testing individual functions.
    """
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"))
    at.run(timeout=30)

    assert not at.exception


def test_app_renders_welcome_screen_with_no_upload() -> None:
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"))
    at.run(timeout=30)

    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Faça upload" in body or "upload" in body.lower()


@pytest.mark.parametrize("has_key", [True, False])
def test_check_api_key_reflects_env_var(monkeypatch, has_key: bool) -> None:
    if has_key:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert app_module._check_api_key() is has_key
