"""Unit tests for the Planner (business-question task decomposition)."""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ai_etl.agents.analysis.planner import plan_analysis_tasks


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"category": ["A", "B"], "rating": [4.0, 3.5], "date": ["2020-01-01", "2021-01-01"]}
    )


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.agents.analysis.planner.get_llm")


def _mock_llm(response: str, usage: dict | None = None) -> MagicMock:
    llm = MagicMock()
    msg = MagicMock(content=response)
    msg.usage_metadata = usage or {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    llm.invoke.return_value = msg
    return llm


def test_plan_analysis_tasks_parses_valid_json(mock_get_llm, sample_df) -> None:
    response = json.dumps(
        [
            {"question": "Quais os KPIs gerais?", "type": "descriptive"},
            {"question": "Por que caiu em 2020?", "type": "diagnostic_or_predictive"},
        ]
    )
    mock_get_llm.return_value = _mock_llm(response)
    tasks, tokens = plan_analysis_tasks("pergunta complexa", sample_df)

    assert len(tasks) == 2
    assert tasks[0] == {"question": "Quais os KPIs gerais?", "type": "descriptive"}
    assert tasks[1]["type"] == "diagnostic_or_predictive"
    assert tokens == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_plan_analysis_tasks_strips_markdown_fences(mock_get_llm, sample_df) -> None:
    payload = json.dumps([{"question": "X?", "type": "descriptive"}])
    mock_get_llm.return_value = _mock_llm(f"```json\n{payload}\n```")
    tasks, _ = plan_analysis_tasks("pergunta", sample_df)

    assert tasks == [{"question": "X?", "type": "descriptive"}]


def test_plan_analysis_tasks_caps_at_max_tasks(mock_get_llm, sample_df) -> None:
    payload = json.dumps([{"question": f"Q{i}?", "type": "descriptive"} for i in range(10)])
    mock_get_llm.return_value = _mock_llm(payload)
    tasks, _ = plan_analysis_tasks("pergunta", sample_df)

    assert len(tasks) == 5


def test_plan_analysis_tasks_normalizes_invalid_type(mock_get_llm, sample_df) -> None:
    payload = json.dumps([{"question": "X?", "type": "something_weird"}])
    mock_get_llm.return_value = _mock_llm(payload)
    tasks, _ = plan_analysis_tasks("pergunta", sample_df)

    assert tasks[0]["type"] == "descriptive"


def test_plan_analysis_tasks_falls_back_on_invalid_json(mock_get_llm, sample_df) -> None:
    mock_get_llm.return_value = _mock_llm("not valid json at all")
    tasks, tokens = plan_analysis_tasks("pergunta original", sample_df)

    assert tasks == [{"question": "pergunta original", "type": "descriptive"}]
    assert tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_plan_analysis_tasks_falls_back_on_empty_list(mock_get_llm, sample_df) -> None:
    mock_get_llm.return_value = _mock_llm("[]")
    tasks, _ = plan_analysis_tasks("pergunta original", sample_df)

    assert tasks == [{"question": "pergunta original", "type": "descriptive"}]


def test_plan_analysis_tasks_falls_back_when_llm_raises(mock_get_llm, sample_df) -> None:
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("API error")
    mock_get_llm.return_value = llm
    tasks, tokens = plan_analysis_tasks("pergunta original", sample_df)

    assert tasks == [{"question": "pergunta original", "type": "descriptive"}]
    assert tokens == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_plan_analysis_tasks_skips_entries_without_question(mock_get_llm, sample_df) -> None:
    payload = json.dumps([{"type": "descriptive"}, {"question": "Válida?", "type": "descriptive"}])
    mock_get_llm.return_value = _mock_llm(payload)
    tasks, _ = plan_analysis_tasks("pergunta", sample_df)

    assert tasks == [{"question": "Válida?", "type": "descriptive"}]


def test_plan_analysis_tasks_no_override_calls_get_llm_with_no_override(
    mock_get_llm, sample_df
) -> None:
    """Sprint 30/gap-closing (ADR-031 §5)."""
    mock_get_llm.return_value = _mock_llm(json.dumps([{"question": "X?", "type": "descriptive"}]))
    plan_analysis_tasks("pergunta", sample_df)

    mock_get_llm.assert_called_once_with(provider=None, model=None)


def test_plan_analysis_tasks_forwards_llm_override(mock_get_llm, sample_df) -> None:
    mock_get_llm.return_value = _mock_llm(json.dumps([{"question": "X?", "type": "descriptive"}]))
    plan_analysis_tasks("pergunta", sample_df, "anthropic", "claude-sonnet-5")

    mock_get_llm.assert_called_once_with(provider="anthropic", model="claude-sonnet-5")
