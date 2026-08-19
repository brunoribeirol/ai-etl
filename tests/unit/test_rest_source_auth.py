"""Unit tests for authenticated REST source support (Sprint 11, ADR-012).

Only the network transport (`httpx.get`) is mocked — same convention as
`tests/unit/test_connectors.py`'s existing `load_rest` tests and
`tests/e2e/test_scenario3_csv_postgres_rest.py`'s REST call. The auth
header/credential construction itself (`_build_auth`) runs for real, and is
also exercised directly, unmocked.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from ai_etl.sources.rest_source import _build_auth, load_rest


def _mock_response(data: object) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status.return_value = None
    return response


# --- _build_auth() direct tests ---


def test_build_auth_none_is_backward_compatible() -> None:
    headers, auth = _build_auth(None)
    assert headers == {}
    assert auth is None


def test_build_auth_api_key_default_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "secret-123")
    headers, auth = _build_auth({"type": "api_key", "env_var": "MY_API_KEY"})
    assert headers == {"X-API-Key": "secret-123"}
    assert auth is None


def test_build_auth_api_key_custom_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "secret-123")
    headers, _ = _build_auth({"type": "api_key", "header": "X-Custom-Key", "env_var": "MY_API_KEY"})
    assert headers == {"X-Custom-Key": "secret-123"}


def test_build_auth_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_TOKEN", "tok-abc")
    headers, auth = _build_auth({"type": "bearer", "env_var": "MY_API_TOKEN"})
    assert headers == {"Authorization": "Bearer tok-abc"}
    assert auth is None


def test_build_auth_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_USER", "alice")
    monkeypatch.setenv("MY_API_PASS", "hunter2")
    headers, auth = _build_auth(
        {"type": "basic", "username_env_var": "MY_API_USER", "password_env_var": "MY_API_PASS"}
    )
    assert headers == {}
    assert isinstance(auth, httpx.BasicAuth)


def test_build_auth_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="MY_API_KEY"):
        _build_auth({"type": "api_key", "env_var": "MY_API_KEY"})


def test_build_auth_unsupported_type_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported REST auth type"):
        _build_auth({"type": "oauth2_client_credentials"})


# --- load_rest() with auth, network mocked ---


def test_load_rest_api_key_auth_sends_header(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "secret-123")
    mock_get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get",
        return_value=_mock_response([{"id": 1}]),
    )

    load_rest(
        "http://example.com/api",
        auth={"type": "api_key", "header": "X-API-Key", "env_var": "MY_API_KEY"},
    )

    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"X-API-Key": "secret-123"}


def test_load_rest_bearer_auth_sends_header(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_TOKEN", "tok-abc")
    mock_get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get",
        return_value=_mock_response([{"id": 1}]),
    )

    load_rest("http://example.com/api", auth={"type": "bearer", "env_var": "MY_API_TOKEN"})

    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer tok-abc"}


def test_load_rest_basic_auth_sends_httpx_auth(mocker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_USER", "alice")
    monkeypatch.setenv("MY_API_PASS", "hunter2")
    mock_get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get",
        return_value=_mock_response([{"id": 1}]),
    )

    load_rest(
        "http://example.com/api",
        auth={
            "type": "basic",
            "username_env_var": "MY_API_USER",
            "password_env_var": "MY_API_PASS",
        },
    )

    _, kwargs = mock_get.call_args
    assert isinstance(kwargs["auth"], httpx.BasicAuth)


def test_load_rest_missing_env_var_raises_before_network_call(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    mock_get = mocker.patch("ai_etl.sources.rest_source.httpx.get")

    with pytest.raises(EnvironmentError, match="MISSING_VAR"):
        load_rest("http://example.com/api", auth={"type": "bearer", "env_var": "MISSING_VAR"})

    mock_get.assert_not_called()


def test_load_rest_no_auth_unchanged_behavior(mocker) -> None:
    mock_get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get",
        return_value=_mock_response([{"id": 1}, {"id": 2}]),
    )

    df = load_rest("http://example.com/api")

    assert len(df) == 2
    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {}
    assert kwargs["auth"] is None
