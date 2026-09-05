"""Unit tests for the Transformer Agent."""

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
    determines the sandbox timeout budget — Transformer receives every raw
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


def test_all_attempts_exhausted_sets_failed(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([INVALID_CODE, INVALID_CODE, INVALID_CODE])
    result = transformer_node(_make_state())

    assert result["error"] is not None
    assert result["status"] == "failed"
    assert result["transformation_attempts"] == 3


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


def test_default_locale_prompt_prefers_dayfirst(mock_get_llm) -> None:
    """Sprint 25 (ADR-036): `_make_state()`'s default locale (`initial_state`'s
    `"pt-BR"` default) should steer the prompt toward preferring `dayfirst=True`.
    2026-09-04 fix (2nd round): the prompt must gate that preference on a strict
    ISO-format check, not on comparing the two parses' results — the 1st-round fix
    (agreement-check) was found live to still corrupt ISO dates whenever day and
    month are both <= 12, since dayfirst=True genuinely disagrees with the default
    reading in exactly that case."""
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    transformer_node(_make_state())

    sent_prompt = llm.invoke.call_args[0][0]
    assert "prefer the dayfirst=True reading" in sent_prompt
    assert "NOT already unambiguous ISO" in sent_prompt
    assert 'format="%Y-%m-%d"' in sent_prompt


def _date_parse_pattern_from_prompt(dates: pd.Series, prefer_dayfirst: bool) -> pd.Series:
    """A literal copy of the RIGHT pattern from `TRANSFORMER_PROMPT`
    (2026-09-04, 2nd round fix) — executed for real against pandas, not just
    asserted as prompt text. This is what caught the 1st round's fix being
    itself still broken: the earlier "agreement check" pattern read as
    correct prose, but `dayfirst=True` genuinely disagrees with the default
    reading for ISO dates whenever day and month are both <= 12, so the
    agreement check picked the wrong (locale-preferred) reading exactly when
    it needed to not. Testing the prompt's *words* was not enough — this
    tests the algorithm itself. `prefer_dayfirst` stands in for what the two
    `date_parse_hint()` branches steer the LLM toward.
    """
    non_null = dates.notna().sum()
    strict_iso = pd.to_datetime(dates, format="%Y-%m-%d", errors="coerce")
    if non_null > 0 and strict_iso.notna().sum() == non_null:
        return strict_iso
    default_parsed = pd.to_datetime(dates, errors="coerce")
    dayfirst_parsed = pd.to_datetime(dates, errors="coerce", dayfirst=True)
    if prefer_dayfirst:
        return (
            dayfirst_parsed
            if dayfirst_parsed.isna().sum() <= default_parsed.isna().sum()
            else default_parsed
        )
    return (
        default_parsed
        if default_parsed.isna().sum() <= dayfirst_parsed.isna().sum()
        else dayfirst_parsed
    )


def test_date_parse_pattern_preserves_iso_dates_with_day_and_month_both_low() -> None:
    """The exact live-reproduced 2026-09-04 corruption case (found a 2nd time by
    the LLM/Prompt Engineer persona audit after the 1st-round fix): ISO dates
    where day and month are both <= 12 must NOT get swapped, for a pt-BR
    (dayfirst-preferring) tenant."""
    dates = pd.Series(["2026-02-01", "2026-03-01", "2026-01-05"])

    result = _date_parse_pattern_from_prompt(dates, prefer_dayfirst=True)

    assert list(result) == list(pd.to_datetime(dates))  # unchanged, no swap


def test_date_parse_pattern_still_prefers_dayfirst_for_genuine_ddmmyyyy_text() -> None:
    """Regression guard the other direction: fixing the ISO case must not break
    the original bug this whole fix chain exists for — real DD/MM/YYYY text for
    a pt-BR tenant must still parse day-first."""
    dates = pd.Series(["02/03/2026", "15/04/2026"])  # 2 Mar and 15 Apr if dayfirst

    result = _date_parse_pattern_from_prompt(dates, prefer_dayfirst=True)

    assert list(result) == [pd.Timestamp("2026-03-02"), pd.Timestamp("2026-04-15")]


def test_en_us_locale_prompt_prefers_month_first(mock_get_llm) -> None:
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    state = {**_make_state(), "locale": "en-US"}
    transformer_node(state)

    sent_prompt = llm.invoke.call_args[0][0]
    assert "month-first" in sent_prompt
