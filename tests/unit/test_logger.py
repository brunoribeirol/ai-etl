"""Unit tests for the audit logger."""

from ai_etl.audit.logger import _sanitize, log_action
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
    new_log = log_action(
        state, "extractor", "connected", {"api_key": "sk-secret123", "url": "http://x.com"}
    )
    details = new_log[0]["details"]
    assert details["api_key"] == "***REDACTED***"
    assert details["url"] == "http://x.com"


def test_sanitize_removes_sensitive_values() -> None:
    data = {"token": "abc", "password": "xyz", "name": "orders"}
    result = _sanitize(data)
    assert result["token"] == "***REDACTED***"
    assert result["password"] == "***REDACTED***"
    assert result["name"] == "orders"


def test_sanitize_redacts_nested_dict_one_level_down() -> None:
    data = {"headers": {"authorization": "Bearer xyz", "content-type": "application/json"}}
    result = _sanitize(data)
    assert result["headers"]["authorization"] == "***REDACTED***"
    assert result["headers"]["content-type"] == "application/json"


def test_sanitize_redacts_sensitive_keys_in_list_of_dicts() -> None:
    data = {
        "request_history": [
            {"url": "http://x.com", "api_key": "sk-123"},
            {"url": "http://y.com", "token": "tok-456"},
        ]
    }
    result = _sanitize(data)
    assert result["request_history"][0]["api_key"] == "***REDACTED***"
    assert result["request_history"][0]["url"] == "http://x.com"
    assert result["request_history"][1]["token"] == "***REDACTED***"


def test_sanitize_redacts_authorization_and_bearer_keys() -> None:
    data = {"Authorization": "Bearer abc", "bearer_token": "xyz", "name": "orders"}
    result = _sanitize(data)
    assert result["Authorization"] == "***REDACTED***"
    assert result["bearer_token"] == "***REDACTED***"
    assert result["name"] == "orders"


def test_sanitize_passes_through_non_sensitive_nested_structure() -> None:
    data = {"schema": {"columns": ["id", "name"], "rows": [{"id": 1, "name": "a"}]}}
    result = _sanitize(data)
    assert result == data
