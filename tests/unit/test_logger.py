"""Unit tests for the audit logger."""

from ai_etl.audit.logger import log_action, _sanitize
from ai_etl.core.state import initial_state


def test_log_action_appends_entry() -> None:
    state = initial_state(spec="test", run_id="run-1")
    new_log = log_action(state, "orchestrator", "plan_created", {"sources": 2})
    assert len(new_log) == 1
    assert new_log[0]["agent"] == "orchestrator"
    assert new_log[0]["action"] == "plan_created"
    assert new_log[0]["run_id"] == "run-1"
    assert "timestamp" in new_log[0]


def test_log_action_does_not_mutate_state() -> None:
    state = initial_state(spec="test", run_id="run-1")
    log_action(state, "orchestrator", "plan_created")
    assert state["audit_log"] == []


def test_log_action_redacts_sensitive_keys() -> None:
    state = initial_state(spec="test", run_id="run-1")
    new_log = log_action(state, "extractor", "connected", {"api_key": "sk-secret123", "url": "http://x.com"})
    details = new_log[0]["details"]
    assert details["api_key"] == "***REDACTED***"
    assert details["url"] == "http://x.com"


def test_sanitize_removes_sensitive_values() -> None:
    data = {"token": "abc", "password": "xyz", "name": "orders"}
    result = _sanitize(data)
    assert result["token"] == "***REDACTED***"
    assert result["password"] == "***REDACTED***"
    assert result["name"] == "orders"
