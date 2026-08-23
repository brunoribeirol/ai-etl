"""Unit tests for `ai_etl.agents.analysis.reviewer` (Sprint 21 follow-up, ADR-037)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.agents.analysis.reviewer import review_gold_result, review_science_result


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.agents.analysis.reviewer.get_llm")


def _llm_returning(content: str) -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content=content,
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
    )
    return llm


# ---------------------------------------------------------------------------
# review_gold_result
# ---------------------------------------------------------------------------


def test_review_gold_result_consistent(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    entry, tokens = review_gold_result(
        "Qual produto vendeu mais?", "O produto A vendeu mais.", pd.DataFrame({"product": ["A"]})
    )

    assert entry is not None
    assert entry["check"] == "llm_review"
    assert entry["severity"] == "ok"
    assert tokens["total_tokens"] == 15


def test_review_gold_result_inconsistent(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning(
        '{"consistent": false, "issue": "narrative answers a different question"}'
    )

    entry, _tokens = review_gold_result(
        "Qual produto vendeu mais?", "O clima estava bom.", pd.DataFrame({"product": ["A"]})
    )

    assert entry is not None
    assert entry["severity"] == "warning"
    assert "different question" in entry["detail"]


def test_review_gold_result_strips_markdown_fences(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('```json\n{"consistent": true, "issue": null}\n```')

    entry, _tokens = review_gold_result("q", "n", pd.DataFrame({"x": [1]}))

    assert entry is not None
    assert entry["severity"] == "ok"


def test_review_gold_result_handles_empty_df(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    entry, _tokens = review_gold_result("q", "n", pd.DataFrame())

    assert entry is not None


def test_review_gold_result_malformed_json_returns_none(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning("not json at all")

    entry, tokens = review_gold_result("q", "n", pd.DataFrame({"x": [1]}))

    assert entry is None
    assert tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_review_gold_result_llm_call_raises_returns_none(mock_get_llm) -> None:
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("provider unreachable")
    mock_get_llm.return_value = llm

    entry, tokens = review_gold_result("q", "n", pd.DataFrame({"x": [1]}))

    assert entry is None
    assert tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_review_gold_result_forwards_llm_override(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    review_gold_result("q", "n", pd.DataFrame({"x": [1]}), "anthropic", "claude-haiku-4-5")

    mock_get_llm.assert_called_once_with(provider="anthropic", model="claude-haiku-4-5")


# ---------------------------------------------------------------------------
# review_science_result
# ---------------------------------------------------------------------------


def test_review_science_result_consistent(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    entry, tokens = review_science_result(
        "Qual a previsão de vendas?",
        "A previsão é de crescimento.",
        pd.DataFrame({"forecast": [100]}),
        {"model_type": "LinearRegression", "task": "forecast"},
    )

    assert entry is not None
    assert entry["severity"] == "ok"
    assert tokens["total_tokens"] == 15


def test_review_science_result_handles_empty_predictions_df(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    entry, _tokens = review_science_result("q", "n", pd.DataFrame(), {"model_type": "KMeans"})

    assert entry is not None
