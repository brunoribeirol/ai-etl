"""Unit tests for the Transformer Agent."""

from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.agents.pipeline.transformer import _clean_code, transformer_node
from ai_etl.core.state import initial_state

VALID_CODE = """
def transform(dfs):
    df = dfs["orders"].copy()
    df["total"] = df["price"] * df["qty"]
    return df
"""

INVALID_CODE = """
def transform(dfs):
    raise ValueError("something went wrong")
"""

IMPORT_CODE = """
import re
def transform(dfs):
    df = dfs["orders"].copy()
    df["total"] = df["price"] * df["qty"]
    return df
"""


def _make_state() -> dict:
    state = initial_state(spec="compute total", run_id="test-run")
    df = pd.DataFrame({"price": [10.0, 20.0], "qty": [2, 3]})
    return {
        **state,
        "pipeline_plan": {"transformations": ["compute total = price * qty"]},
        "extracted_data": {"orders": df},
        "source_schemas": {
            "orders": {
                "columns": ["price", "qty"],
                "dtypes": {},
                "shape": [2, 2],
                "sample": [],
                "null_counts": {},
            }
        },
    }


def _mock_llm(responses: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=r) for r in responses]
    return llm


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.agents.pipeline.transformer.get_llm")


def test_valid_code_returns_transformed_dataframe(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    result = transformer_node(_make_state())

    assert result["error"] is None
    assert result["transformed_data"] is not None
    assert "total" in result["transformed_data"].columns
    assert result["transformation_attempts"] == 1


def test_audit_log_entry_added_on_success(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    result = transformer_node(_make_state())

    assert any(e["action"] == "code_executed" for e in result["audit_log"])


def test_scales_timeout_for_large_source(mocker, mock_get_llm) -> None:
    """ADR-012: the largest extracted source (not just the Silver output)
    determines the sandbox timeout budget: Transformer receives every raw
    source at once, unlike Analyst/Science's single merged Silver input."""
    from ai_etl.core import sandbox as sandbox_module

    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    state = _make_state()
    big_df = pd.concat(
        [state["extracted_data"]["orders"]]
        * (
            (sandbox_module.LARGE_DATASET_ROW_THRESHOLD // len(state["extracted_data"]["orders"]))
            + 1
        ),
        ignore_index=True,
    )
    state["extracted_data"]["orders"] = big_df
    spy = mocker.patch(
        "ai_etl.agents.pipeline.transformer.execute_in_sandbox",
        wraps=sandbox_module.execute_in_sandbox,
    )

    transformer_node(state)

    assert (
        spy.call_args.kwargs["timeout_seconds"]
        == 30 * sandbox_module.LARGE_DATASET_TIMEOUT_MULTIPLIER
    )


def test_timeout_unchanged_for_small_source(mocker, mock_get_llm) -> None:
    from ai_etl.core import sandbox as sandbox_module

    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    spy = mocker.patch(
        "ai_etl.agents.pipeline.transformer.execute_in_sandbox",
        wraps=sandbox_module.execute_in_sandbox,
    )

    transformer_node(_make_state())

    assert spy.call_args.kwargs["timeout_seconds"] == 30


def test_sandbox_error_triggers_retry(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([INVALID_CODE, INVALID_CODE, VALID_CODE])
    result = transformer_node(_make_state())

    assert result["error"] is None
    assert result["transformation_attempts"] == 3


def test_import_statement_triggers_retry_with_hint(mock_get_llm) -> None:
    """The sandbox has no `__import__`; the retry prompt should call this out
    explicitly so the LLM removes the import instead of repeating it."""
    llm = _mock_llm([IMPORT_CODE, VALID_CODE])
    mock_get_llm.return_value = llm
    result = transformer_node(_make_state())

    assert result["error"] is None
    assert result["transformation_attempts"] == 2
    second_prompt = llm.invoke.call_args_list[1].args[0]
    assert "remove it entirely" in second_prompt


def test_retry_prompt_carries_the_failed_code_and_its_error(mock_get_llm) -> None:
    """The model must see what it wrote, not only the error. Real runs, 2026-10-12:
    with the error alone the same mistake came back in all three attempts."""
    llm = _mock_llm([INVALID_CODE, VALID_CODE])
    mock_get_llm.return_value = llm
    result = transformer_node(_make_state())

    assert result["error"] is None
    assert result["transformation_attempts"] == 2
    first_prompt = llm.invoke.call_args_list[0].args[0]
    second_prompt = llm.invoke.call_args_list[1].args[0]
    assert INVALID_CODE.strip() not in first_prompt
    assert INVALID_CODE.strip() in second_prompt
    assert "Code of that attempt" in second_prompt
    assert "Error it raised" in second_prompt
    assert second_prompt.index("Code of that attempt") < second_prompt.index("Error it raised")


def test_all_attempts_exhausted_sets_failed(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([INVALID_CODE, INVALID_CODE, INVALID_CODE])
    result = transformer_node(_make_state())

    assert result["error"] is not None
    assert result["status"] == "failed"
    assert result["transformation_attempts"] == 3


def test_all_attempts_exhausted_still_persists_last_code(mock_get_llm) -> None:
    """Wave 1 item 4b, 2026-09-05 mega-audit remediation: before this fix, a
    total failure never set `transformation_code` at all: the History page's
    Code tab showed "No code available for this run." with no way to see what
    was actually tried (confirmed live for the nested-JSON case)."""
    mock_get_llm.return_value = _mock_llm([INVALID_CODE, INVALID_CODE, INVALID_CODE])
    result = transformer_node(_make_state())

    assert result["transformation_code"] == INVALID_CODE.strip()


def test_upstream_error_short_circuits() -> None:
    state = _make_state()
    state["error"] = "upstream failure"
    result = transformer_node(state)
    assert result["error"] == "upstream failure"


# --- _clean_code() unit tests ---


def test_clean_code_strips_python_fence() -> None:
    raw = "```python\ndef transform(dfs):\n    return dfs\n```"
    assert _clean_code(raw) == "def transform(dfs):\n    return dfs"


def test_clean_code_strips_plain_fence() -> None:
    raw = "```\ndef f(): pass\n```"
    assert _clean_code(raw) == "def f(): pass"


def test_clean_code_passthrough_if_no_fence() -> None:
    raw = "def transform(dfs):\n    return dfs"
    assert _clean_code(raw) == raw


# --- LLM provider/model override (Sprint 30/gap-closing, ADR-031 §5) ---


def test_no_override_calls_get_llm_with_no_override(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    transformer_node(_make_state())

    mock_get_llm.assert_called_once_with(provider=None, model=None)


def test_override_is_forwarded_to_get_llm(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    state = {
        **_make_state(),
        "llm_provider_override": "anthropic",
        "llm_model_override": "claude-sonnet-5",
    }
    transformer_node(state)

    mock_get_llm.assert_called_once_with(provider="anthropic", model="claude-sonnet-5")


def test_override_used_is_audit_logged(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    state = {
        **_make_state(),
        "llm_provider_override": "anthropic",
        "llm_model_override": "claude-sonnet-5",
    }
    result = transformer_node(state)

    override_entries = [e for e in result["audit_log"] if e["action"] == "llm_override_used"]
    assert len(override_entries) == 1
    assert override_entries[0]["details"] == {"provider": "anthropic", "model": "claude-sonnet-5"}


def test_no_override_does_not_add_audit_entry(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_CODE])
    result = transformer_node(_make_state())

    assert all(e["action"] != "llm_override_used" for e in result["audit_log"])


def test_code_is_read_from_a_response_whose_content_is_a_list_of_blocks(mock_get_llm) -> None:
    """A model that reasons before answering returns its content as blocks. Found
    2026-10-12 with `claude-haiku-5-5`: the Transformer was handed the repr of that
    list and failed three times with "No function named 'transform' found"."""
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "CAQSqBMKEAgS..."},
        {"type": "text", "text": VALID_CODE},
    ]
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=blocks)]
    mock_get_llm.return_value = llm

    result = transformer_node(_make_state())

    assert result.get("error") is None
    assert result["transformation_attempts"] == 1
    assert "total" in result["transformed_data"].columns


def test_default_locale_prompt_prefers_dayfirst(mock_get_llm) -> None:
    """Sprint 25 (ADR-036): `_make_state()`'s default locale (`initial_state`'s
    `"pt-BR"` default) should steer the prompt toward preferring `dayfirst=True`.
    2026-09-04 fix (2nd round): the prompt must gate that preference on a strict
    ISO-format check, not on comparing the two parses' results: the 1st-round fix
    (agreement-check) was found live to still corrupt ISO dates whenever day and
    month are both <= 12, since dayfirst=True genuinely disagrees with the default
    reading in exactly that case."""
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    transformer_node(_make_state())

    sent_prompt = llm.invoke.call_args[0][0]
    assert "use the dayfirst=True reading" in sent_prompt
    assert "NOT already unambiguous ISO" in sent_prompt
    # 2026-10-12: the check must be ISO 8601, not date-only. A date-only format sent
    # every ISO value with a time part to the day-first branch.
    assert 'iso = pd.to_datetime(value, format="ISO8601", errors="coerce")' in sent_prompt
    assert 'iso = pd.to_datetime(value, format="%Y-%m-%d"' not in sent_prompt


def _date_parse_pattern_from_prompt(dates: pd.Series, prefer_dayfirst: bool) -> pd.Series:
    """A literal copy of the RIGHT pattern from `TRANSFORMER_PROMPT` (Wave 3
    item 16, 2026-09-05 mega-audit remediation): executed for real against
    pandas, not just asserted as prompt text.

    Per-value (`.apply()`), not per-column: the previous column-wide version
    of this pattern (2026-09-04, 2nd round fix) correctly handled a column
    that was *purely* ISO or *purely* non-ISO, but a live-verified real gap
    (docs/archive/work/2026-09-05-mega-audit-remediation-plan.md) found it still
    broke on a column genuinely MIXING both: `pd.to_datetime(...)` with no
    explicit `format=` infers ONE format from the column and silently turns
    minority-format rows into NaT, even though each string is individually
    parseable. Parsing per-value sidesteps that column-wide inference
    entirely. `prefer_dayfirst` stands in for what the two
    `date_parse_hint()` branches steer the LLM toward.
    """

    def parse_one(value: object) -> Any:
        if pd.isna(value):
            return pd.NaT
        iso = pd.to_datetime(value, format="ISO8601", errors="coerce")
        if pd.notna(iso):
            return iso
        return pd.to_datetime(value, errors="coerce", dayfirst=prefer_dayfirst)

    return dates.apply(parse_one)


def test_date_parse_pattern_preserves_iso_dates_with_day_and_month_both_low() -> None:
    """The exact live-reproduced 2026-09-04 corruption case (found a 2nd time by
    the LLM/Prompt Engineer persona audit after the 1st-round fix): ISO dates
    where day and month are both <= 12 must NOT get swapped, for a pt-BR
    (dayfirst-preferring) tenant."""
    dates = pd.Series(["2026-02-01", "2026-03-01", "2026-01-05"])

    result = _date_parse_pattern_from_prompt(dates, prefer_dayfirst=True)

    assert list(result) == list(pd.to_datetime(dates))  # unchanged, no swap


@pytest.mark.parametrize(
    "value",
    [
        "2025-01-06 00:04:25",  # the exact shape of the NYC TLC extract
        "2025-01-06T00:04:25",
        "2025-01-06 00:04:25.123",
        "2025-01-06T00:04:25Z",
    ],
)
def test_date_parse_pattern_preserves_iso_datetimes_that_carry_a_time_part(value: str) -> None:
    """Third variant of the day/month swap, found 2026-10-12 by the first real run
    of the public-data case study. The strict check was date-only
    (`format="%Y-%m-%d"`), so an ISO value with a time part failed it, fell to the
    locale branch and was re-read day-first: 6 January became 1 June, on 19,974 of
    20,000 rows, in a run that reported success."""
    result = _date_parse_pattern_from_prompt(pd.Series([value]), prefer_dayfirst=True)

    assert (result.iloc[0].month, result.iloc[0].day) == (1, 6)


def test_date_parse_pattern_keeps_a_whole_week_of_iso_datetimes_inside_its_window() -> None:
    """Same finding at column level: every day of the case-study window
    (2025-01-06 to 2025-01-12) has day and month both <= 12, so each one is
    swappable. None may leave January."""
    dates = pd.Series([f"2025-01-{day:02d} 13:45:00" for day in range(6, 13)])

    result = _date_parse_pattern_from_prompt(dates, prefer_dayfirst=True)

    assert list(result.dt.month.unique()) == [1]
    assert list(result.dt.day) == list(range(6, 13))


def test_date_parse_pattern_still_prefers_dayfirst_for_genuine_ddmmyyyy_text() -> None:
    """Regression guard the other direction: fixing the ISO case must not break
    the original bug this whole fix chain exists for: real DD/MM/YYYY text for
    a pt-BR tenant must still parse day-first."""
    dates = pd.Series(["02/03/2026", "15/04/2026"])  # 2 Mar and 15 Apr if dayfirst

    result = _date_parse_pattern_from_prompt(dates, prefer_dayfirst=True)

    assert list(result) == [pd.Timestamp("2026-03-02"), pd.Timestamp("2026-04-15")]


def test_date_parse_pattern_handles_a_column_genuinely_mixing_iso_and_ddmmyyyy() -> None:
    """Wave 3 item 16, 2026-09-05 mega-audit remediation
    (docs/archive/work/2026-09-05-mega-audit-remediation-plan.md): the exact
    live-reproduced case (Data Engineer persona, `vendas_br_edge.csv` /
    confirmed again via the deployed app's own live-verification run for
    items 2/3/4b): a column mixing ISO strings with DD/MM/YYYY strings.
    The column-wide version of this pattern dropped every ISO row here
    (pandas infers one format from the column and applies it uniformly);
    parsing per-value must recover all of them."""
    dates = pd.Series(["02/03/2026", "2026-03-15", "05/03/2026", "2026-03-20"])

    result = _date_parse_pattern_from_prompt(dates, prefer_dayfirst=True)

    assert list(result) == [
        pd.Timestamp("2026-03-02"),
        pd.Timestamp("2026-03-15"),
        pd.Timestamp("2026-03-05"),
        pd.Timestamp("2026-03-20"),
    ]


def test_en_us_locale_prompt_prefers_month_first(mock_get_llm) -> None:
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    state = {**_make_state(), "locale": "en-US"}
    transformer_node(state)

    sent_prompt = llm.invoke.call_args[0][0]
    assert "month-first" in sent_prompt


# ---------------------------------------------------------------------------
# Wave 1 item 3, 2026-09-05 mega-audit remediation
# (docs/archive/work/2026-09-05-mega-audit-remediation-plan.md): Data Engineer found,
# live, that generated code twice wrote `str.replace('R\$ ', '')` to strip a
# BR-currency prefix: `\$` is not a valid Python escape sequence, so the
# literal backslash it leaves never matches "R$ " in the real data, silently
# turning the entire column to NaN after `pd.to_numeric`. Same technique as
# the date-parsing tests above: a literal copy of the prompt's pattern,
# executed for real against pandas, not just asserted as prompt text.
# ---------------------------------------------------------------------------


def test_currency_prompt_warns_against_invalid_backslash_escape(mock_get_llm) -> None:
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    transformer_node(_make_state())

    sent_prompt = llm.invoke.call_args[0][0]
    assert "NOT a valid Python escape sequence" in sent_prompt
    assert "regex=False" in sent_prompt


def test_numeric_coercion_prompt_forbids_silent_fillna_zero(mock_get_llm) -> None:
    """Audit finding M11 (2026-09-09): a live run coerced a bad-type quantity value
    ("cem") silently to 0, corrupting units math with no trace. Pin the prompt rule
    that a genuinely-non-numeric value must become NaN, not 0."""
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    transformer_node(_make_state())

    sent_prompt = llm.invoke.call_args[0][0]
    assert "must become `NaN`, NOT `0`" in sent_prompt
    assert 'errors="coerce"' in sent_prompt


def _wrong_currency_clean(prices: pd.Series) -> pd.Series:
    """The exact real bug, found live 2026-09-04 in generated code (Data
    Engineer persona, `vendas_br_edge.csv`): kept here only to prove it
    really is broken, not to recommend it."""
    cleaned = prices.str.replace("R\\$ ", "").str.replace(".", "").str.replace(",", ".")
    return pd.to_numeric(cleaned, errors="coerce")


def _right_currency_clean(prices: pd.Series) -> pd.Series:
    """A literal copy of the RIGHT pattern from `TRANSFORMER_PROMPT`."""
    cleaned = (
        prices.str.replace("R$ ", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def test_wrong_currency_pattern_really_does_null_the_whole_column() -> None:
    """Documents the bug mechanism for real: the invalid `\\$` escape means the
    WRONG pattern is not merely non-idiomatic, it silently produces 100% NaN:
    the exact live-reproduced symptom ('preco' column became fully null)."""
    prices = pd.Series(["R$ 1.234,56", "R$ 89,90"])

    result = _wrong_currency_clean(prices)

    assert result.isna().all()


def test_right_currency_pattern_parses_br_formatted_values() -> None:
    """The exact live-reproduced 2026-09-04 case: BR currency with a symbol
    prefix and thousands/decimal separators must parse to real floats."""
    prices = pd.Series(["R$ 1.234,56", "R$ 89,90"])

    result = _right_currency_clean(prices)

    assert list(result) == [1234.56, 89.90]


def test_llm_usage_adds_every_attempt_to_what_the_state_already_holds(mock_get_llm) -> None:
    """Every model call of the Transformer is counted, failed attempts included, on top
    of the Orchestrator's usage already in the state."""
    llm = MagicMock()
    usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    llm.invoke.side_effect = [
        MagicMock(content=INVALID_CODE, usage_metadata=usage),
        MagicMock(content=VALID_CODE, usage_metadata=usage),
    ]
    mock_get_llm.return_value = llm
    state = {
        **_make_state(),
        "llm_usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    }

    result = transformer_node(state)

    assert result["transformation_attempts"] == 2
    assert result["llm_usage"] == {"input_tokens": 207, "output_tokens": 43, "total_tokens": 250}


def test_llm_usage_is_kept_when_every_attempt_fails(mock_get_llm) -> None:
    llm = MagicMock()
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    llm.invoke.side_effect = [MagicMock(content=INVALID_CODE, usage_metadata=usage)] * 3
    mock_get_llm.return_value = llm

    result = transformer_node(_make_state())

    assert result["status"] == "failed"
    assert result["llm_usage"] == {"input_tokens": 30, "output_tokens": 15, "total_tokens": 45}


# ---------------------------------------------------------------------------
# Decimal point removed by the generated code (real run, 2026-10-12)
# ---------------------------------------------------------------------------

POINT_REMOVED_CODE = """
def transform(dfs):
    df = dfs["rates"].copy()
    df["valor"] = (
        df["valor"]
        .str.replace("R$ ", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df
"""

POINT_KEPT_CODE = """
def transform(dfs):
    df = dfs["rates"].copy()
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df
"""


def _rates_state() -> dict:
    rates = pd.DataFrame({"data": ["01/09/2026", "02/09/2026"], "valor": ["5.1570", "5.1273"]})
    return {**_make_state(), "extracted_data": {"rates": rates}, "source_schemas": {"rates": {}}}


def test_decimal_point_removed_is_reported_for_the_real_case() -> None:
    from ai_etl.agents.pipeline.transformer import _decimal_point_removed

    source = {"rates": pd.DataFrame({"valor": ["5.1570", "5.1273"]})}
    message = _decimal_point_removed(source, pd.DataFrame({"valor": [51570.0, 51273.0]}))
    assert message is not None
    assert "'valor'" in message and "5.1570" in message and "51570" in message


def test_decimal_point_removed_accepts_values_read_as_they_are() -> None:
    from ai_etl.agents.pipeline.transformer import _decimal_point_removed

    source = {"rates": pd.DataFrame({"valor": ["5.1570", "5.1273"]})}
    assert _decimal_point_removed(source, pd.DataFrame({"valor": [5.157, 5.1273]})) is None


def test_decimal_point_removed_is_reported_with_blanks_and_whole_numbers() -> None:
    from ai_etl.agents.pipeline.transformer import _decimal_point_removed

    # Real run in production, 2026-10-13 (dbc6f480): Telco churn file, column TotalCharges,
    # text with 11 blank values and 324 whole numbers; written as 2985 for "29.85".
    source = {"telco": pd.DataFrame({"TotalCharges": ["29.85", "1889.5", " ", "20", "108.15"]})}
    written = pd.DataFrame({"TotalCharges": [2985.0, 18895.0, float("nan"), 20.0, 10815.0]})
    message = _decimal_point_removed(source, written)
    assert message is not None
    assert "'TotalCharges'" in message and "'29.85'" in message and "2985 for 29.85" in message

    read_as_they_are = pd.DataFrame({"TotalCharges": [29.85, 1889.5, float("nan"), 20.0, 108.15]})
    assert _decimal_point_removed(source, read_as_they_are) is None


def test_decimal_point_removed_says_nothing_without_a_decimal_value() -> None:
    from ai_etl.agents.pipeline.transformer import _decimal_point_removed

    source = {"s": pd.DataFrame({"qty": ["20", " ", "45"]})}
    assert _decimal_point_removed(source, pd.DataFrame({"qty": [20.0, float("nan"), 45.0]})) is None
    # Whole numbers beside values with a three-digit fraction: the dot may still be a
    # thousands separator, so nothing is judged.
    source = {"s": pd.DataFrame({"total": ["1.234", "20", "2.500"]})}
    assert _decimal_point_removed(source, pd.DataFrame({"total": [1234.0, 20.0, 2500.0]})) is None


def test_decimal_point_removed_says_nothing_when_the_dot_may_be_a_thousands_separator() -> None:
    from ai_etl.agents.pipeline.transformer import _decimal_point_removed

    # "1.234" and "2.500" may be 1234 and 2500 written the Brazilian way: not judged.
    source = {"sales": pd.DataFrame({"total": ["1.234", "2.500"]})}
    assert _decimal_point_removed(source, pd.DataFrame({"total": [1234.0, 2500.0]})) is None
    # Brazilian format with a comma is not plain decimal text either.
    source = {"sales": pd.DataFrame({"total": ["1.234,56", "99,90"]})}
    assert _decimal_point_removed(source, pd.DataFrame({"total": [1234.56, 99.9]})) is None


def test_decimal_point_removed_ignores_numeric_sources_other_columns_and_other_changes() -> None:
    from ai_etl.agents.pipeline.transformer import _decimal_point_removed

    numeric = {"s": pd.DataFrame({"valor": [5.157, 5.1273]})}
    assert _decimal_point_removed(numeric, pd.DataFrame({"valor": [51570.0, 51273.0]})) is None
    text = {"s": pd.DataFrame({"valor": ["5.1570", "5.1273"]})}
    assert _decimal_point_removed(text, pd.DataFrame({"rate": [51570.0, 51273.0]})) is None
    assert _decimal_point_removed(text, pd.DataFrame({"valor": [515.7, 512.73]})) is None
    assert _decimal_point_removed(text, pd.DataFrame({"valor": ["5.1570", "5.1273"]})) is None


def test_code_that_removes_the_decimal_point_is_a_failed_attempt(mock_get_llm) -> None:
    llm = _mock_llm([POINT_REMOVED_CODE, POINT_KEPT_CODE])
    mock_get_llm.return_value = llm

    result = transformer_node(_rates_state())

    assert result["error"] is None
    assert result["transformation_attempts"] == 2
    assert result["transformed_data"]["valor"].tolist() == [5.157, 5.1273]
    retry_prompt = llm.invoke.call_args_list[1].args[0]
    assert "removed the dot" in retry_prompt


def test_code_that_always_removes_the_decimal_point_fails_the_run(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([POINT_REMOVED_CODE] * 3)

    result = transformer_node(_rates_state())

    assert result["status"] == "failed"
    assert "removed the dot" in result["error"]


def test_prompt_limits_the_currency_pattern_to_values_with_a_decimal_comma(mock_get_llm) -> None:
    llm = _mock_llm([POINT_KEPT_CODE])
    mock_get_llm.return_value = llm
    transformer_node(_rates_state())
    prompt = llm.invoke.call_args_list[0].args[0]
    assert "the dot IS the decimal" in prompt
    assert "never\nremove the dot" in prompt or "never remove the dot" in prompt.replace("\n", " ")
