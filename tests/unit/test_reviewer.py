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

    entries, tokens = review_gold_result(
        "Qual produto vendeu mais?", "O produto A vendeu mais.", pd.DataFrame({"product": ["A"]})
    )

    assert len(entries) == 1
    assert entries[0]["check"] == "llm_review"
    assert entries[0]["severity"] == "ok"
    assert tokens["total_tokens"] == 15


def test_review_gold_result_inconsistent(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning(
        '{"consistent": false, "issue": "narrative answers a different question"}'
    )

    entries, _tokens = review_gold_result(
        "Qual produto vendeu mais?", "O clima estava bom.", pd.DataFrame({"product": ["A"]})
    )

    assert len(entries) == 1
    assert entries[0]["severity"] == "warning"
    assert "different question" in entries[0]["detail"]


def test_review_gold_result_strips_markdown_fences(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('```json\n{"consistent": true, "issue": null}\n```')

    entries, _tokens = review_gold_result("q", "n", pd.DataFrame({"x": [1]}))

    assert len(entries) == 1
    assert entries[0]["severity"] == "ok"


def test_review_gold_result_handles_empty_df(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    entries, _tokens = review_gold_result("q", "n", pd.DataFrame())

    assert len(entries) == 1


def test_review_gold_result_malformed_json_returns_empty_list(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning("not json at all")

    entries, tokens = review_gold_result("q", "n", pd.DataFrame({"x": [1]}))

    assert entries == []
    assert tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_review_gold_result_llm_call_raises_returns_empty_list(mock_get_llm) -> None:
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("provider unreachable")
    mock_get_llm.return_value = llm

    entries, tokens = review_gold_result("q", "n", pd.DataFrame({"x": [1]}))

    assert entries == []
    assert tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_review_gold_result_forwards_llm_override(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    review_gold_result("q", "n", pd.DataFrame({"x": [1]}), "anthropic", "claude-haiku-4-5")

    mock_get_llm.assert_called_once_with(provider="anthropic", model="claude-haiku-4-5")


# ---------------------------------------------------------------------------
# review_gold_result / review_science_result — directional hedging (2026-08-24
# LLM/Prompt-Engineer audit, ADR-037 follow-up)
# ---------------------------------------------------------------------------


def test_review_gold_result_flags_hedged_directional_answer(mock_get_llm) -> None:
    """A trend question answered with a non-committal narrative gets a second,
    distinct entry — separate from the factual-consistency check, which the
    hedge doesn't necessarily fail."""
    mock_get_llm.return_value = _llm_returning(
        '{"consistent": true, "issue": null, "directional_question": true, '
        '"hedges_direction": true, "hedge_detail": "narrative never states a direction"}'
    )

    entries, _tokens = review_gold_result(
        "A receita está aumentando ou diminuindo?",
        "A receita aumentou em alguns meses e diminuiu em outros.",
        pd.DataFrame({"month": [1, 2], "revenue": [100, 90]}),
    )

    checks = {e["check"]: e for e in entries}
    assert "llm_review" in checks
    assert checks["llm_review"]["severity"] == "ok"
    assert "llm_review_hedge" in checks
    assert checks["llm_review_hedge"]["severity"] == "warning"
    assert "direction" in checks["llm_review_hedge"]["detail"]


def test_review_gold_result_does_not_flag_committed_directional_answer(mock_get_llm) -> None:
    """A directional question answered with a clear, committed direction gets no
    hedge entry — only the usual factual-consistency check."""
    mock_get_llm.return_value = _llm_returning(
        '{"consistent": true, "issue": null, "directional_question": true, '
        '"hedges_direction": false, "hedge_detail": null}'
    )

    entries, _tokens = review_gold_result(
        "A receita está aumentando ou diminuindo?",
        "A receita caiu 12% no período, uma tendência de queda clara.",
        pd.DataFrame({"month": [1, 2], "revenue": [100, 88]}),
    )

    assert len(entries) == 1
    assert entries[0]["check"] == "llm_review"
    assert not any(e["check"] == "llm_review_hedge" for e in entries)


def test_review_gold_result_non_directional_question_no_hedge_entry(mock_get_llm) -> None:
    """A non-directional question never gets a hedge entry, even if the LLM
    (incorrectly) reports hedges_direction — directional_question gates it."""
    mock_get_llm.return_value = _llm_returning(
        '{"consistent": true, "issue": null, "directional_question": false, '
        '"hedges_direction": true, "hedge_detail": "irrelevant"}'
    )

    entries, _tokens = review_gold_result(
        "Qual o produto mais vendido?",
        "O produto A foi o mais vendido.",
        pd.DataFrame({"product": ["A"]}),
    )

    assert len(entries) == 1
    assert entries[0]["check"] == "llm_review"


# ---------------------------------------------------------------------------
# review_science_result
# ---------------------------------------------------------------------------


def test_review_science_result_consistent(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    entries, tokens = review_science_result(
        "Qual a previsão de vendas?",
        "A previsão é de crescimento.",
        pd.DataFrame({"forecast": [100]}),
        {"model_type": "LinearRegression", "task": "forecast"},
    )

    assert len(entries) == 1
    assert entries[0]["severity"] == "ok"
    assert tokens["total_tokens"] == 15


def test_review_science_result_handles_empty_predictions_df(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning('{"consistent": true, "issue": null}')

    entries, _tokens = review_science_result("q", "n", pd.DataFrame(), {"model_type": "KMeans"})

    assert len(entries) == 1


def test_review_science_result_flags_hedged_directional_answer(mock_get_llm) -> None:
    mock_get_llm.return_value = _llm_returning(
        '{"consistent": true, "issue": null, "directional_question": true, '
        '"hedges_direction": true, "hedge_detail": "forecast narrative avoids a direction"}'
    )

    entries, _tokens = review_science_result(
        "As vendas vão subir ou cair no próximo trimestre?",
        "As vendas podem subir ou cair, dependendo do mês.",
        pd.DataFrame({"forecast": [100, 95]}),
        {"model_type": "LinearRegression", "task": "forecast"},
    )

    assert any(e["check"] == "llm_review_hedge" and e["severity"] == "warning" for e in entries)
