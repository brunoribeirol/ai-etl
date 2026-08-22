"""Unit tests for core/cost_estimation.py (Sprint 35, FinOps heuristic)."""

import pytest

from ai_etl.core.cost_estimation import (
    BASE_PROMPT_TOKENS,
    TOKENS_PER_COLUMN,
    TOKENS_PER_ROW,
    estimate_token_usage,
)


def test_estimate_token_usage_scales_with_column_count() -> None:
    small = estimate_token_usage(row_count=100, col_count=5)
    large = estimate_token_usage(row_count=100, col_count=50)
    assert large["total_tokens"] > small["total_tokens"]


def test_estimate_token_usage_row_count_effect_is_small_relative_to_columns() -> None:
    """Grounded in `extractor.py::_extract_schema`: the sample sent to the LLM
    is capped at 3 rows regardless of dataset size, so row count should barely
    move the estimate compared to column count (see module docstring)."""
    few_rows = estimate_token_usage(row_count=10, col_count=10)
    many_rows = estimate_token_usage(row_count=1_000_000, col_count=10)
    # A million extra rows adds far less than doubling the column count would.
    ten_cols_vs_twenty = estimate_token_usage(row_count=10, col_count=20)
    assert (many_rows["total_tokens"] - few_rows["total_tokens"]) < (
        ten_cols_vs_twenty["total_tokens"] - few_rows["total_tokens"]
    )


def test_estimate_token_usage_zero_shape_is_just_the_base_overhead() -> None:
    tokens = estimate_token_usage(row_count=0, col_count=0)
    assert tokens["total_tokens"] == round(BASE_PROMPT_TOKENS)
    assert tokens["input_tokens"] + tokens["output_tokens"] == tokens["total_tokens"]


def test_estimate_token_usage_matches_the_documented_formula() -> None:
    row_count, col_count = 1000, 12
    tokens = estimate_token_usage(row_count=row_count, col_count=col_count)
    expected_total = round(
        BASE_PROMPT_TOKENS + TOKENS_PER_COLUMN * col_count + TOKENS_PER_ROW * row_count
    )
    assert tokens["input_tokens"] + tokens["output_tokens"] == expected_total


@pytest.mark.parametrize("row_count,col_count", [(-1, 5), (5, -1)])
def test_estimate_token_usage_rejects_negative_shape(row_count: int, col_count: int) -> None:
    with pytest.raises(ValueError):
        estimate_token_usage(row_count=row_count, col_count=col_count)
