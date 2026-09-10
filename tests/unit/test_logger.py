"""Unit tests for the audit logger."""

from ai_etl.audit.logger import _sanitize, log_action, scrub_secrets_in_text
from ai_etl.core.state import initial_state

# --- Audit finding M4 (2026-09-09): redaction gaps ---


def test_sanitize_redacts_connection_string_and_dsn_field_names() -> None:
    """BT-1: a DSN with an inline password in a field named connection_string /
    postgres_connection_string / dsn used to pass through unredacted."""
    data = {
        "connection_string": "postgresql://u:pw@host/db",
        "postgres_connection_string": "postgresql://u:pw@host/db",
        "dsn": "postgresql://u:pw@host/db",
        "table": "orders",
    }
    result = _sanitize(data)
    assert result["connection_string"] == "***REDACTED***"
    assert result["postgres_connection_string"] == "***REDACTED***"
    assert result["dsn"] == "***REDACTED***"
    assert result["table"] == "orders"


def test_sanitize_scrubs_dsn_password_embedded_in_a_value() -> None:
    """A secret inside a VALUE (not a field name) is scrubbed too — e.g. a raw
    connection error surfaced in a benignly-named field."""
    data = {"error": "could not connect: postgresql://tenant:supersecret@db-host:5432/prod"}
    result = _sanitize(data)
    assert "supersecret" not in result["error"]
    assert "postgresql://tenant:***@db-host:5432/prod" in result["error"]


def test_scrub_secrets_in_text_redacts_only_the_password() -> None:
    text = "OperationalError: FATAL: password authentication failed for mysql://root:hunter2@10.0.0.1:3306/app"
    scrubbed = scrub_secrets_in_text(text)
    assert "hunter2" not in scrubbed
    assert "mysql://root:***@10.0.0.1:3306/app" in scrubbed
    # non-DSN text is left intact
    assert "password authentication failed" in scrubbed


def test_scrub_secrets_in_text_passthrough_non_str() -> None:
    assert scrub_secrets_in_text(None) is None  # type: ignore[arg-type] -- defensive non-str path
    assert scrub_secrets_in_text("no secret here") == "no secret here"


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
