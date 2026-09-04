"""Tests for `services/spec_builder.py` (extracted from `app.py`, Sprint 6/ADR-011 —
moved here alongside `pipeline_service.py`'s own tests since it's no longer
Streamlit-specific)."""

from pathlib import Path

import pandas as pd

from ai_etl.services.spec_builder import auto_generate_spec


def test_auto_generate_spec_includes_file_path_and_columns() -> None:
    df = pd.DataFrame({"name": ["A"], "price": [1.0]})
    spec = auto_generate_spec(Path("runs/uploads/abc123.csv"), df, Path("out.csv"))

    assert "runs/uploads/abc123.csv" in spec
    assert "name, price" in spec
    assert "out.csv" in spec


def test_auto_generate_spec_includes_business_question_hint() -> None:
    df = pd.DataFrame({"a": [1]})
    spec = auto_generate_spec(
        Path("f.csv"), df, Path("out.csv"), business_question="Quais produtos vendem mais?"
    )

    assert "Quais produtos vendem mais?" in spec


def test_auto_generate_spec_forbids_fabricating_critical_fields() -> None:
    """Regression guard: this instruction is what stopped the Transformer from
    inventing a fake row (fillna("Unknown") etc.) for missing name/category/price."""
    df = pd.DataFrame({"name": ["A"]})
    spec = auto_generate_spec(Path("f.csv"), df, Path("out.csv"))

    assert "Do NOT fabricate values" in spec
    assert "is_incomplete" in spec


def test_auto_generate_spec_reports_row_and_column_counts() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    spec = auto_generate_spec(Path("f.csv"), df, Path("out.csv"))

    assert "3 rows and 2 columns" in spec


def test_auto_generate_spec_includes_additional_instructions() -> None:
    """2026-09-04 gap-closing fix: the manual-spec textarea used to be silently
    discarded whenever a file was also attached (`api/routers/runs.py::create_run`
    never passed it through) — regression guard that it now reaches the spec text
    and is marked as taking priority over the generic cleaning steps."""
    df = pd.DataFrame({"dt": ["2026-01-02"], "active": [True]})
    spec = auto_generate_spec(
        Path("f.csv"),
        df,
        Path("out.csv"),
        additional_instructions="rename dt to date, keep only active rows",
    )

    assert "rename dt to date, keep only active rows" in spec
    assert "user's instructions" in spec


def test_auto_generate_spec_omits_additional_instructions_hint_when_empty() -> None:
    df = pd.DataFrame({"a": [1]})
    spec = auto_generate_spec(Path("f.csv"), df, Path("out.csv"))

    assert "specific instructions" not in spec
