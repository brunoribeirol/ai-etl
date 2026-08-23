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
    `"pt-BR"` default) should steer the prompt toward trying `dayfirst=True` first."""
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    transformer_node(_make_state())

    sent_prompt = llm.invoke.call_args[0][0]
    assert "dayfirst=True FIRST" in sent_prompt


def test_en_us_locale_prompt_prefers_month_first(mock_get_llm) -> None:
    llm = _mock_llm([VALID_CODE])
    mock_get_llm.return_value = llm
    state = {**_make_state(), "locale": "en-US"}
    transformer_node(state)

    sent_prompt = llm.invoke.call_args[0][0]
    assert "month-first" in sent_prompt
