"""Unit tests for the Advisor Agent (prescriptive layer)."""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.agents.analysis.advisor import run_advisor


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product": ["A", "B", "A", "C"],
            "revenue": [100.0, 200.0, 150.0, 300.0],
            "region": ["Sul", "Norte", "Sul", "Norte"],
        }
    )


@pytest.fixture
def gold_result() -> list[dict]:
    return [
        {
            "task_question": "Qual produto gerou mais receita?",
            "narrative": "O produto C gerou 43% da receita total.",
            "gold_df": pd.DataFrame({"product": ["C", "B", "A"], "total_revenue": [300, 200, 250]}),
            "error": None,
        }
    ]


@pytest.fixture
def science_result() -> list[dict]:
    return [
        {
            "task_question": "Qual a previsão de receita?",
            "narrative": "O modelo prevê crescimento de 15% nos próximos 3 meses.",
            "model_info": {
                "model_type": "LinearRegression",
                "task": "regression",
                "metrics": {"r2": 0.91},
                "features": ["units"],
                "target": "revenue",
            },
            "predictions_df": pd.DataFrame({"month": ["Jan", "Fev"], "predicted": [320.0, 340.0]}),
            "error": None,
        }
    ]


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.agents.analysis.advisor.get_llm")


def _mock_llm(responses: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=r) for r in responses]
    return llm


VALID_RESPONSE = json.dumps(
    {
        "recommendations": [
            {
                "action": "Aumentar estoque do produto C em 20%",
                "rationale": "Produto C responde por 43% da receita total.",
                "priority": "high",
                "expected_impact": "Aumento de R$50k em faturamento mensal",
            },
            {
                "action": "Expandir presença na região Norte",
                "rationale": "Norte concentra os produtos de maior ticket médio.",
                "priority": "medium",
                "expected_impact": "Crescimento de 15% na receita regional",
            },
        ],
        "summary": "Os dados indicam que o produto C e a região Norte são os principais vetores de crescimento.",
    }
)


# ---------------------------------------------------------------------------
# run_advisor — happy path
# ---------------------------------------------------------------------------


def test_run_advisor_happy_path(mock_get_llm, sample_df, gold_result, science_result) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_RESPONSE])
    result = run_advisor(sample_df, "Como aumentar o faturamento?", gold_result, science_result)

    assert result["error"] is None
    assert len(result["recommendations"]) == 2
    assert isinstance(result["summary"], str)
    assert len(result["summary"]) > 0


def test_run_advisor_en_us_locale_instructs_english(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    """Sprint 25 (ADR-036)."""
    llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = llm
    run_advisor(
        sample_df,
        "Como aumentar o faturamento?",
        gold_result,
        science_result,
        locale="en-US",
    )

    sent_prompt = llm.invoke.call_args[0][0]
    assert "English (US)" in sent_prompt


def test_run_advisor_prompt_forbids_restating_the_request(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    mock_llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = mock_llm
    run_advisor(sample_df, "Como aumentar o faturamento?", gold_result, science_result)

    sent_prompt = mock_llm.invoke.call_args[0][0]
    assert "Do NOT restate the business question" in sent_prompt


def test_run_advisor_result_has_all_keys(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_RESPONSE])
    result = run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    assert set(result.keys()) == {"recommendations", "summary", "error", "tokens"}


def test_run_advisor_recommendations_are_sorted_by_priority(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    unsorted = json.dumps(
        {
            "recommendations": [
                {"action": "B", "rationale": "x", "priority": "low", "expected_impact": "x"},
                {"action": "A", "rationale": "x", "priority": "high", "expected_impact": "x"},
                {"action": "C", "rationale": "x", "priority": "medium", "expected_impact": "x"},
            ],
            "summary": "Resumo.",
        }
    )
    mock_get_llm.return_value = _mock_llm([unsorted])
    result = run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    priorities = [r["priority"] for r in result["recommendations"]]
    assert priorities == ["high", "medium", "low"]


def test_run_advisor_normalizes_invalid_priority(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    with_bad_priority = json.dumps(
        {
            "recommendations": [
                {"action": "X", "rationale": "x", "priority": "critical", "expected_impact": "x"}
            ],
            "summary": "Resumo.",
        }
    )
    mock_get_llm.return_value = _mock_llm([with_bad_priority])
    result = run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    assert result["recommendations"][0]["priority"] == "medium"


# ---------------------------------------------------------------------------
# run_advisor — with missing upstream results
# ---------------------------------------------------------------------------


def test_run_advisor_handles_empty_gold(mock_get_llm, sample_df, science_result) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_RESPONSE])
    result = run_advisor(sample_df, "Como crescer?", [], science_result)

    assert result["error"] is None


def test_run_advisor_handles_empty_science(mock_get_llm, sample_df, gold_result) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_RESPONSE])
    result = run_advisor(sample_df, "Como crescer?", gold_result, [])

    assert result["error"] is None


def test_run_advisor_aggregates_multiple_gold_subtasks(
    mock_get_llm, sample_df, science_result
) -> None:
    mock_llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = mock_llm
    two_gold_tasks = [
        {
            "task_question": "KPIs gerais?",
            "narrative": "Receita total de R$750.",
            "gold_df": pd.DataFrame({"total": [750]}),
            "error": None,
        },
        {
            "task_question": "KPIs por região?",
            "narrative": "Norte lidera com R$500.",
            "gold_df": pd.DataFrame({"region": ["Norte"], "total": [500]}),
            "error": None,
        },
    ]
    run_advisor(sample_df, "Pergunta com múltiplas partes", two_gold_tasks, science_result)

    sent_prompt = mock_llm.invoke.call_args[0][0]
    assert "KPIs gerais?" in sent_prompt
    assert "KPIs por região?" in sent_prompt


def test_run_advisor_skips_failed_subtasks_in_context(
    mock_get_llm, sample_df, science_result
) -> None:
    mock_llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = mock_llm
    mixed = [
        {"task_question": "Falhou", "error": "boom"},
        {
            "task_question": "Funcionou",
            "narrative": "Tudo certo.",
            "gold_df": pd.DataFrame({"a": [1]}),
            "error": None,
        },
    ]
    run_advisor(sample_df, "Pergunta", mixed, science_result)

    sent_prompt = mock_llm.invoke.call_args[0][0]
    assert "Falhou" not in sent_prompt
    assert "Funcionou" in sent_prompt


# ---------------------------------------------------------------------------
# run_advisor — JSON retry
# ---------------------------------------------------------------------------


def test_run_advisor_retries_on_invalid_json(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    mock_get_llm.return_value = _mock_llm(["not json at all", VALID_RESPONSE])
    result = run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    assert result["error"] is None
    assert len(result["recommendations"]) > 0


def test_run_advisor_returns_error_after_all_failures(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    mock_get_llm.return_value = _mock_llm(["bad", "also bad"])
    result = run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    assert result["error"] is not None
    assert result["recommendations"] == []


# ---------------------------------------------------------------------------
# LLM provider/model override (Sprint 30/gap-closing, ADR-031 §5)
# ---------------------------------------------------------------------------


def test_run_advisor_no_override_calls_get_llm_with_no_override(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_RESPONSE])
    run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    mock_get_llm.assert_called_once_with(provider=None, model=None)


def test_run_advisor_forwards_llm_override(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_RESPONSE])
    run_advisor(
        sample_df, "Como crescer?", gold_result, science_result, "anthropic", "claude-sonnet-5"
    )

    mock_get_llm.assert_called_once_with(provider="anthropic", model="claude-sonnet-5")


# ---------------------------------------------------------------------------
# run_advisor — fence-stripping via the shared helper (Sprint 33, dedupe)
# ---------------------------------------------------------------------------


def test_run_advisor_strips_markdown_fences_via_shared_helper(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    fenced_response = f"```json\n{VALID_RESPONSE}\n```"
    mock_get_llm.return_value = _mock_llm([fenced_response])
    result = run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    assert result["error"] is None
    assert len(result["recommendations"]) == 2


def test_run_advisor_strips_markdown_fences_without_closing_fence(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    """Matches `strip_code_fences`'s tolerance for a missing closing fence."""
    fenced_response = f"```json\n{VALID_RESPONSE}"
    mock_get_llm.return_value = _mock_llm([fenced_response])
    result = run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    assert result["error"] is None
    assert len(result["recommendations"]) == 2


# ---------------------------------------------------------------------------
# run_advisor — ADR-037 sanity-check awareness (Wave 3)
# ---------------------------------------------------------------------------


def test_run_advisor_includes_gold_sanity_check_warning_in_prompt(
    mock_get_llm, sample_df, science_result
) -> None:
    gold_with_warning = [
        {
            "task_question": "Qual região gerou mais receita?",
            "narrative": "A região Sul gerou a maior receita.",
            "gold_df": pd.DataFrame({"region": ["Sul", "Norte"], "total_revenue": [100, 300]}),
            "error": None,
            "sanity_check": {
                "checks": [
                    {
                        "check": "llm_review",
                        "severity": "warning",
                        "detail": "Narrative says Sul had the highest revenue, but the data "
                        "shows Norte with a higher total.",
                    }
                ],
                "severity": "warning",
                "summary": "1 sanity check(s): 1 warning(s)",
            },
        }
    ]
    mock_llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = mock_llm
    run_advisor(sample_df, "Qual região gerou mais receita?", gold_with_warning, science_result)

    sent_prompt = mock_llm.invoke.call_args[0][0]
    assert "⚠️ Known data-consistency warning" in sent_prompt
    assert "Norte with a higher total" in sent_prompt


def test_run_advisor_includes_science_sanity_check_warning_in_prompt(
    mock_get_llm, sample_df, gold_result
) -> None:
    science_with_warning = [
        {
            "task_question": "Qual a previsão de receita?",
            "narrative": "O modelo prevê crescimento de 15%.",
            "model_info": {
                "model_type": "LinearRegression",
                "task": "regression",
                "metrics": {"r2": 0.91},
                "features": ["units"],
                "target": "revenue",
            },
            "predictions_df": pd.DataFrame({"month": ["Jan"], "predicted": [320.0]}),
            "error": None,
            "sanity_check": {
                "checks": [
                    {
                        "check": "prediction_range",
                        "severity": "warning",
                        "detail": "Column 'predicted': 1 value(s) fall far outside the "
                        "historical 'revenue' range.",
                    }
                ],
                "severity": "warning",
                "summary": "1 sanity check(s): 1 warning(s)",
            },
        }
    ]
    mock_llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = mock_llm
    run_advisor(sample_df, "Qual a previsão de receita?", gold_result, science_with_warning)

    sent_prompt = mock_llm.invoke.call_args[0][0]
    assert "⚠️ Known data-consistency warning" in sent_prompt
    assert "fall far outside the historical" in sent_prompt


def test_run_advisor_prompt_instructs_to_account_for_sanity_check_warnings(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    mock_get_llm.return_value = _mock_llm([VALID_RESPONSE])
    run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    sent_prompt = mock_get_llm.return_value.invoke.call_args[0][0]
    assert "Known data-consistency warning" in sent_prompt
    assert "do NOT present" in sent_prompt


def test_run_advisor_ok_sanity_check_injects_no_warning(
    mock_get_llm, sample_df, science_result
) -> None:
    gold_ok = [
        {
            "task_question": "Qual produto gerou mais receita?",
            "narrative": "O produto C gerou 43% da receita total.",
            "gold_df": pd.DataFrame({"product": ["C", "B", "A"], "total_revenue": [300, 200, 250]}),
            "error": None,
            "sanity_check": {
                "checks": [
                    {
                        "check": "sum_conservation",
                        "severity": "ok",
                        "detail": "Column 'total_revenue': gold_df sum is within the Silver total.",
                    }
                ],
                "severity": "ok",
                "summary": "1 sanity check(s): 0 warning(s)",
            },
        }
    ]
    mock_llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = mock_llm
    run_advisor(sample_df, "Qual produto gerou mais receita?", gold_ok, science_result)

    sent_prompt = mock_llm.invoke.call_args[0][0]
    assert "Known data-consistency warning for this sub-task" not in sent_prompt


def test_run_advisor_missing_sanity_check_injects_no_warning(
    mock_get_llm, sample_df, gold_result, science_result
) -> None:
    """`gold_result`/`science_result` fixtures have no `sanity_check` key at all —
    the common case for a sub-task whose deterministic checks all passed with no
    entries appended (or ADR-037 LLM review disabled)."""
    mock_llm = _mock_llm([VALID_RESPONSE])
    mock_get_llm.return_value = mock_llm
    run_advisor(sample_df, "Como crescer?", gold_result, science_result)

    sent_prompt = mock_llm.invoke.call_args[0][0]
    assert "Known data-consistency warning for this sub-task" not in sent_prompt
