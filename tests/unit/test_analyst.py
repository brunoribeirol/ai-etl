"""Unit tests for the Analyst Agent (Gold layer)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.agents.analysis.analyst import _build_column_stats, _strip_fences, run_analyst

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product": ["A", "B", "A", "C", "B"],
            "region": ["Sul", "Norte", "Sul", "Norte", "Sul"],
            "revenue": [100.0, 200.0, 150.0, 300.0, 250.0],
            "quantity": [1, 2, 1, 3, 2],
        }
    )


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.agents.analysis.analyst.get_llm")


def _make_llm_mock(responses: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=r) for r in responses]
    return llm


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------


def test_strip_fences_plain_code() -> None:
    code = "x = 1\ny = 2"
    assert _strip_fences(code) == code


def test_strip_fences_python_fence() -> None:
    code = "```python\nx = 1\ny = 2\n```"
    assert _strip_fences(code) == "x = 1\ny = 2"


def test_strip_fences_generic_fence() -> None:
    code = "```\nx = 1\n```"
    assert _strip_fences(code) == "x = 1"


def test_strip_fences_no_closing_fence() -> None:
    code = "```python\nx = 1"
    result = _strip_fences(code)
    assert "x = 1" in result


# ---------------------------------------------------------------------------
# _build_column_stats
# ---------------------------------------------------------------------------


def test_build_column_stats_numeric(sample_df: pd.DataFrame) -> None:
    stats = _build_column_stats(sample_df)
    assert "revenue" in stats
    assert "numeric" in stats
    assert "min=" in stats


def test_build_column_stats_categorical(sample_df: pd.DataFrame) -> None:
    stats = _build_column_stats(sample_df)
    assert "product" in stats
    assert "categorical" in stats


def test_build_column_stats_empty_df() -> None:
    empty = pd.DataFrame({"x": pd.Series([], dtype=float)})
    stats = _build_column_stats(empty)
    assert "x" in stats


# ---------------------------------------------------------------------------
# run_analyst — happy path
# ---------------------------------------------------------------------------

HAPPY_CODE = """\
gold_df = df.groupby('product')['revenue'].sum().reset_index()
gold_df.columns = ['product', 'total_revenue']
fig = px.bar(gold_df, x='product', y='total_revenue', title='Receita por produto')
narrative = 'O produto C gerou mais receita no período analisado.'
"""


def test_run_analyst_happy_path(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _make_llm_mock([HAPPY_CODE])
    result = run_analyst(sample_df, "Qual produto gerou mais receita?")

    assert result["error"] is None
    assert isinstance(result["gold_df"], pd.DataFrame)
    assert not result["gold_df"].empty
    assert result["fig"] is not None
    assert isinstance(result["narrative"], str)
    assert result["attempts"] == 1


def test_run_analyst_scales_timeout_for_large_df(
    mocker, mock_get_llm, sample_df: pd.DataFrame
) -> None:
    """ADR-012: a Silver DataFrame above LARGE_DATASET_ROW_THRESHOLD gets a
    doubled sandbox timeout budget — real profiling confirmed the fixed 15s
    budget is too tight for representative Analyst-style code at large scale."""
    from ai_etl.core import sandbox as sandbox_module

    mock_get_llm.return_value = _make_llm_mock([HAPPY_CODE])
    big_df = pd.concat(
        [sample_df] * ((sandbox_module.LARGE_DATASET_ROW_THRESHOLD // len(sample_df)) + 1),
        ignore_index=True,
    )
    spy = mocker.patch(
        "ai_etl.agents.analysis.analyst.execute_in_sandbox", wraps=sandbox_module.execute_in_sandbox
    )

    run_analyst(big_df, "Qual produto gerou mais receita?")

    assert (
        spy.call_args.kwargs["timeout_seconds"]
        == 15 * sandbox_module.LARGE_DATASET_TIMEOUT_MULTIPLIER
    )


NESTED_FUNCTION_CODE = """\
def _compute():
    return df.groupby('product')['revenue'].sum().reset_index()

gold_df = _compute()
gold_df.columns = ['product', 'total_revenue']
fig = px.bar(gold_df, x='product', y='total_revenue', title='Receita por produto')
narrative = 'O produto C gerou mais receita no período analisado.'
"""


def test_run_analyst_code_defining_nested_function_sees_df(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    """Regression test: exec(code, globals, locals) with separate dicts makes `df`
    invisible inside any function the LLM defines, since nested scopes close over
    globals, not the locals dict. `df` must be reachable from inside a `def`."""
    mock_get_llm.return_value = _make_llm_mock([NESTED_FUNCTION_CODE])
    result = run_analyst(sample_df, "Qual produto gerou mais receita?")

    assert result["error"] is None
    assert result["attempts"] == 1
    assert not result["gold_df"].empty


def test_run_analyst_strips_markdown_fences(mock_get_llm, sample_df: pd.DataFrame) -> None:
    fenced = f"```python\n{HAPPY_CODE}\n```"
    mock_get_llm.return_value = _make_llm_mock([fenced])
    result = run_analyst(sample_df, "Qual produto gerou mais receita?")

    assert result["error"] is None
    assert isinstance(result["gold_df"], pd.DataFrame)


def test_run_analyst_returns_code_field(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _make_llm_mock([HAPPY_CODE])
    result = run_analyst(sample_df, "Qual produto?")

    assert "gold_df" in result["code"]


# ---------------------------------------------------------------------------
# run_analyst — retry on error
# ---------------------------------------------------------------------------

BAD_CODE = "this is not valid python code !!!"


def test_run_analyst_retries_on_exec_error(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _make_llm_mock([BAD_CODE, HAPPY_CODE])
    result = run_analyst(sample_df, "Qual produto?")

    assert result["error"] is None
    assert result["attempts"] == 2


def test_run_analyst_fails_after_3_attempts(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _make_llm_mock([BAD_CODE, BAD_CODE, BAD_CODE])
    result = run_analyst(sample_df, "Qual produto?")

    assert result["error"] is not None
    assert isinstance(result["gold_df"], pd.DataFrame)
    assert result["gold_df"].empty
    assert result["fig"] is None
    assert result["attempts"] == 3


def test_run_analyst_retries_when_gold_df_is_not_dataframe(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    bad_type_code = (
        "gold_df = 42\nfig = px.bar(pd.DataFrame({'x': [1]}), x='x')\nnarrative = 'test'"
    )
    mock_get_llm.return_value = _make_llm_mock([bad_type_code, HAPPY_CODE])
    result = run_analyst(sample_df, "Qual produto?")

    assert result["error"] is None
    assert result["attempts"] == 2


def test_run_analyst_retries_when_fig_is_none(mock_get_llm, sample_df: pd.DataFrame) -> None:
    no_fig_code = (
        "gold_df = df.groupby('product')['revenue'].sum().reset_index()\n"
        "fig = None\n"
        "narrative = 'test'"
    )
    mock_get_llm.return_value = _make_llm_mock([no_fig_code, HAPPY_CODE])
    result = run_analyst(sample_df, "Qual produto?")

    assert result["error"] is None
    assert result["attempts"] == 2


# ---------------------------------------------------------------------------
# run_analyst — audit fields
# ---------------------------------------------------------------------------


def test_run_analyst_result_has_all_keys(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _make_llm_mock([HAPPY_CODE])
    result = run_analyst(sample_df, "Qual produto?")

    assert set(result.keys()) == {
        "task_question",
        "gold_df",
        "fig",
        "narrative",
        "code",
        "attempts",
        "error",
        "tokens",
    }


# ---------------------------------------------------------------------------
# LLM provider/model override (Sprint 30/gap-closing, ADR-031 §5)
# ---------------------------------------------------------------------------


def test_run_analyst_no_override_calls_get_llm_with_no_override(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    mock_get_llm.return_value = _make_llm_mock([HAPPY_CODE])
    run_analyst(sample_df, "Qual produto?")

    mock_get_llm.assert_called_once_with(provider=None, model=None)


def test_run_analyst_forwards_llm_override(mock_get_llm, sample_df: pd.DataFrame) -> None:
    mock_get_llm.return_value = _make_llm_mock([HAPPY_CODE])
    run_analyst(sample_df, "Qual produto?", "anthropic", "claude-sonnet-5")

    mock_get_llm.assert_called_once_with(provider="anthropic", model="claude-sonnet-5")


# ---------------------------------------------------------------------------
# run_analyst — locale (Sprint 25, ADR-036)
# ---------------------------------------------------------------------------


def test_run_analyst_default_locale_instructs_portuguese(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    llm = _make_llm_mock([HAPPY_CODE])
    mock_get_llm.return_value = llm
    run_analyst(sample_df, "Qual produto?")

    sent_prompt = llm.invoke.call_args[0][0]
    assert "Portuguese (Brazil)" in sent_prompt


def test_run_analyst_en_us_locale_instructs_english(mock_get_llm, sample_df: pd.DataFrame) -> None:
    llm = _make_llm_mock([HAPPY_CODE])
    mock_get_llm.return_value = llm
    run_analyst(sample_df, "Qual produto?", locale="en-US")

    sent_prompt = llm.invoke.call_args[0][0]
    assert "English (US)" in sent_prompt


def test_run_analyst_unsupported_locale_falls_back_to_default(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    llm = _make_llm_mock([HAPPY_CODE])
    mock_get_llm.return_value = llm
    run_analyst(sample_df, "Qual produto?", locale="fr-FR")

    sent_prompt = llm.invoke.call_args[0][0]
    assert "Portuguese (Brazil)" in sent_prompt


# ---------------------------------------------------------------------------
# run_analyst — chart axis label grounding (2026-08-24 LLM/Prompt-Engineer audit)
# ---------------------------------------------------------------------------
#
# The Analyst's axis-label bug is a prompt-instruction gap, not deterministic
# post-processing — the label text itself comes back embedded in `code`
# (a Plotly call written by the LLM), so there is no separate function to unit
# test in isolation. What IS testable and under our control: the prompt sent to
# the LLM must (a) carry the real, exact column names from the schema, and (b)
# explicitly instruct grounding axis labels in those real columns instead of a
# generic "category" placeholder. We assert the prompt content, not LLM behavior
# we cannot deterministically control — same honesty caveat as the Planner fix.


def test_run_analyst_prompt_includes_exact_column_names(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    """The schema-grounding instruction is only useful if the real column names
    (here `product`, not a generic "category") actually reach the prompt."""
    llm = _make_llm_mock([HAPPY_CODE])
    mock_get_llm.return_value = llm
    run_analyst(sample_df, "Qual produto gerou mais receita?")

    sent_prompt = llm.invoke.call_args[0][0]
    assert "product" in sent_prompt
    assert "region" in sent_prompt


def test_run_analyst_prompt_instructs_grounding_axis_labels_in_real_columns(
    mock_get_llm, sample_df: pd.DataFrame
) -> None:
    """Regression test for the mislabeled-axis finding: a product-level chart
    axis was labeled "Categoria de Produto" (Product Category) when the schema
    has no category column, only a `product` column. The prompt must instruct
    the LLM to use the real column's semantic meaning instead of a generic
    "category" placeholder."""
    llm = _make_llm_mock([HAPPY_CODE])
    mock_get_llm.return_value = llm
    run_analyst(sample_df, "Qual produto gerou mais receita?")

    sent_prompt = llm.invoke.call_args[0][0]
    assert "category-type column" in sent_prompt
    assert "generic placeholder term" in sent_prompt
