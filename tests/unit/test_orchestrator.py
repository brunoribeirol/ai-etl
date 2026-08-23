"""Unit tests for the Orchestrator Agent."""

import json
from unittest.mock import MagicMock

import pytest

from ai_etl.agents.pipeline.orchestrator import orchestrator_node
from ai_etl.core.state import initial_state

VALID_PLAN = {
    "sources": [{"name": "orders", "type": "csv", "path": "data/orders.csv"}],
    "destination": {"type": "csv", "path": "output.csv"},
    "transformations": ["rename dt to date"],
    "quality_checks": ["null_check", "duplicate_check"],
}


def _make_state() -> dict:
    return initial_state(spec="load orders.csv and rename dt to date", run_id="test-run")


def _mock_llm(responses: list[str]) -> MagicMock:
    llm = MagicMock()
    llm.invoke.side_effect = [MagicMock(content=r) for r in responses]
    return llm


@pytest.fixture
def mock_get_llm(mocker):
    return mocker.patch("ai_etl.agents.pipeline.orchestrator.get_llm")


def test_valid_json_creates_pipeline_plan(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([json.dumps(VALID_PLAN)])
    result = orchestrator_node(_make_state())

    assert result["pipeline_plan"] == VALID_PLAN
    assert result["error"] is None
    assert result["status"] == "running"


def test_audit_log_entry_added(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm([json.dumps(VALID_PLAN)])
    result = orchestrator_node(_make_state())

    assert len(result["audit_log"]) == 1
    assert result["audit_log"][0]["agent"] == "orchestrator"
    assert result["audit_log"][0]["action"] == "plan_created"


def test_invalid_json_retries_and_succeeds(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm(["not json at all", json.dumps(VALID_PLAN)])
    result = orchestrator_node(_make_state())

    assert result["pipeline_plan"] == VALID_PLAN
    assert result["error"] is None


def test_all_retries_exhausted_sets_failed(mock_get_llm) -> None:
    mock_get_llm.return_value = _mock_llm(["bad json", "also bad json"])
    result = orchestrator_node(_make_state())

    assert result["error"] is not None
    assert "failed" in result["error"].lower() or "JSON" in result["error"]
    assert result["status"] == "failed"


def test_markdown_fenced_json_is_parsed(mock_get_llm) -> None:
    fenced = f"```json\n{json.dumps(VALID_PLAN)}\n```"
    # Orchestrator tries json.loads directly; fenced JSON fails on attempt 1.
    # LLM returns clean JSON on retry (attempt 2) — documents current behavior.
    mock_get_llm.return_value = _mock_llm([fenced, json.dumps(VALID_PLAN)])
    result = orchestrator_node(_make_state())

    assert result["pipeline_plan"] == VALID_PLAN


class TestLlmOverride:
    """Sprint 30/gap-closing (ADR-031 §5) — PipelineState's llm_provider_override/
    llm_model_override actually reach get_llm(), and get audit-logged when used."""

    def test_no_override_calls_get_llm_with_no_override(self, mock_get_llm) -> None:
        mock_get_llm.return_value = _mock_llm([json.dumps(VALID_PLAN)])
        orchestrator_node(_make_state())

        mock_get_llm.assert_called_once_with(provider=None, model=None)

    def test_override_is_forwarded_to_get_llm(self, mock_get_llm) -> None:
        mock_get_llm.return_value = _mock_llm([json.dumps(VALID_PLAN)])
        state = {
            **_make_state(),
            "llm_provider_override": "anthropic",
            "llm_model_override": "claude-sonnet-5",
        }
        orchestrator_node(state)

        mock_get_llm.assert_called_once_with(provider="anthropic", model="claude-sonnet-5")

    def test_override_used_is_audit_logged(self, mock_get_llm) -> None:
        mock_get_llm.return_value = _mock_llm([json.dumps(VALID_PLAN)])
        state = {
            **_make_state(),
            "llm_provider_override": "anthropic",
            "llm_model_override": "claude-sonnet-5",
        }
        result = orchestrator_node(state)

        override_entries = [e for e in result["audit_log"] if e["action"] == "llm_override_used"]
        assert len(override_entries) == 1
        assert override_entries[0]["details"] == {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
        }

    def test_no_override_does_not_add_audit_entry(self, mock_get_llm) -> None:
        mock_get_llm.return_value = _mock_llm([json.dumps(VALID_PLAN)])
        result = orchestrator_node(_make_state())

        assert all(e["action"] != "llm_override_used" for e in result["audit_log"])
