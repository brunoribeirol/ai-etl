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
