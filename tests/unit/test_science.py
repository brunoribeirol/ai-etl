"""Unit tests for the Science Agent (predictive layer)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.agents.science import _build_column_stats, _strip_fences, run_science


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "month": pd.to_datetime(
                ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"],
                format="%Y-%m",
            ),
            "revenue": [100.0, 120.0, 115.0, 140.0, 160.0],
            "units": [10, 12, 11, 14, 16],
            "region": ["Sul", "Norte", "Sul", "Norte", "Sul"],
        }
    )


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.agents.science.get_llm")


def _mock_llm(responses: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=r) for r in responses]
    return llm


HAPPY_CODE = """\
from sklearn.linear_model import LinearRegression
predictions_df = df[['revenue', 'units']].copy()
predictions_df['predicted'] = LinearRegression().fit(
    df[['units']].fillna(0), df['revenue'].fillna(0)
).predict(df[['units']].fillna(0))
fig = px.scatter(predictions_df, x='units', y='revenue', title='Previsão de Receita')
narrative = 'O modelo de regressão linear apresenta boa aderência aos dados históricos.'
model_info = {'model_type': 'LinearRegression', 'task': 'regression',
              'metrics': {'r2': 0.92}, 'features': ['units'], 'target': 'revenue'}
"""

# HAPPY_CODE uses `from sklearn` — not allowed in sandbox.
# Use the injected sklearn classes directly:
HAPPY_CODE = """\
X = df[['units']].fillna(0)
y = df['revenue'].fillna(0)
model = LinearRegression().fit(X, y)
predictions_df = df[['units', 'revenue']].copy()
predictions_df['predicted'] = model.predict(X)
fig = px.scatter(predictions_df, x='units', y='revenue', title='Previsão de Receita')
narrative = 'O modelo de regressão linear apresenta boa aderência aos dados históricos.'
model_info = {'model_type': 'LinearRegression', 'task': 'regression',
              'metrics': {'r2': float(r2_score(y, predictions_df["predicted"]))},
              'features': ['units'], 'target': 'revenue'}
"""


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------


def test_strip_fences_plain() -> None:
    assert _strip_fences("x = 1") == "x = 1"


def test_strip_fences_python_fence() -> None:
    assert _strip_fences("```python\nx = 1\n```") == "x = 1"


# ---------------------------------------------------------------------------
# _build_column_stats
# ---------------------------------------------------------------------------


def test_build_column_stats_datetime(sample_df: pd.DataFrame) -> None:
    stats = _build_column_stats(sample_df)
    assert "month" in stats
    assert "datetime" in stats


def test_build_column_stats_numeric(sample_df: pd.DataFrame) -> None:
    stats = _build_column_stats(sample_df)
    assert "revenue" in stats
    assert "numeric" in stats


# ---------------------------------------------------------------------------
# run_science — happy path
# ---------------------------------------------------------------------------


def test_run_science_happy_path(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _mock_llm([HAPPY_CODE])
    result = run_science(sample_df, "Qual será a receita nos próximos meses?")

    assert result["error"] is None
    assert isinstance(result["predictions_df"], pd.DataFrame)
    assert not result["predictions_df"].empty
    assert result["fig"] is not None
    assert isinstance(result["narrative"], str)
    assert isinstance(result["model_info"], dict)
    assert result["attempts"] == 1


def test_run_science_result_has_all_keys(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _mock_llm([HAPPY_CODE])
    result = run_science(sample_df, "Qual será a receita?")

    assert set(result.keys()) == {
        "predictions_df",
        "fig",
        "narrative",
        "model_info",
        "code",
        "attempts",
        "error",
    }


def test_run_science_strips_fences(mock_get_llm, sample_df: pd.DataFrame) -> None:
    fenced = f"```python\n{HAPPY_CODE}\n```"
    mock_get_llm.return_value = _mock_llm([fenced])
    result = run_science(sample_df, "Previsão?")

    assert result["error"] is None


# ---------------------------------------------------------------------------
# run_science — retry / failure
# ---------------------------------------------------------------------------


BAD_CODE = "this is not python !!!"


def test_run_science_retries_on_exec_error(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _mock_llm([BAD_CODE, HAPPY_CODE])
    result = run_science(sample_df, "Previsão?")

    assert result["error"] is None
    assert result["attempts"] == 2


def test_run_science_fails_after_3_attempts(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _mock_llm([BAD_CODE, BAD_CODE, BAD_CODE])
    result = run_science(sample_df, "Previsão?")

    assert result["error"] is not None
    assert result["predictions_df"].empty
    assert result["fig"] is None
    assert result["attempts"] == 3


def test_run_science_retries_when_predictions_df_wrong_type(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    wrong_type_code = (
        "predictions_df = 42\n"
        "fig = px.bar(df.head(3), x='region', y='revenue', title='X')\n"
        "narrative = 'test'\n"
        "model_info = {'model_type': 'X', 'task': 'X', 'metrics': {}, 'features': [], 'target': 'X'}"
    )
    mock_get_llm.return_value = _mock_llm([wrong_type_code, HAPPY_CODE])
    result = run_science(sample_df, "Previsão?")

    assert result["error"] is None
    assert result["attempts"] == 2
