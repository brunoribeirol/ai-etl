"""Unit tests for authenticated REST source support (Sprint 11, ADR-012).

Only the network transport (`httpx.get`) is mocked — same convention as
`tests/unit/test_connectors.py`'s existing `load_rest` tests and
`tests/e2e/test_scenario3_csv_postgres_rest.py`'s REST call. The auth
header/credential construction itself (`_build_auth`) runs for real, and is
also exercised directly, unmocked.
"""

import time
from collections.abc import Iterator
from unittest.mock import MagicMock

import httpx
import pytest

from ai_etl.sources import rest_source
from ai_etl.sources.rest_source import _build_auth, _fetch_oauth2_token, load_rest


def _mock_response(data: object) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status.return_value = None
    return response


def _mock_token_response(access_token: str = "tok-xyz", expires_in: int = 3600) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"access_token": access_token, "expires_in": expires_in}
    response.raise_for_status.return_value = None
    return response


@pytest.fixture(autouse=True)
def _clear_token_cache() -> Iterator[None]:
    rest_source._TOKEN_CACHE.clear()
    yield
    rest_source._TOKEN_CACHE.clear()


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
        _build_auth({"type": "digest"})


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


# --- oauth2_client_credentials — token fetch, caching, and load_rest wiring ---


def test_fetch_oauth2_token_posts_client_credentials_grant(mocker) -> None:
    mock_post = mocker.patch(
        "ai_etl.sources.rest_source.httpx.post", return_value=_mock_token_response("tok-xyz")
    )

    token = _fetch_oauth2_token("https://auth.example.com/token", "cid", "csecret", None)

    assert token == "tok-xyz"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://auth.example.com/token"
    assert kwargs["data"] == {
        "grant_type": "client_credentials",
        "client_id": "cid",
        "client_secret": "csecret",
    }


def test_fetch_oauth2_token_includes_optional_scope(mocker) -> None:
    mock_post = mocker.patch(
        "ai_etl.sources.rest_source.httpx.post", return_value=_mock_token_response("tok-xyz")
    )

    _fetch_oauth2_token("https://auth.example.com/token", "cid", "csecret", "read:data")

    _, kwargs = mock_post.call_args
    assert kwargs["data"]["scope"] == "read:data"


def test_fetch_oauth2_token_caches_until_expiry(mocker) -> None:
    mock_post = mocker.patch(
        "ai_etl.sources.rest_source.httpx.post", return_value=_mock_token_response("tok-1", 3600)
    )

    first = _fetch_oauth2_token("https://auth.example.com/token", "cid", "csecret", None)
    second = _fetch_oauth2_token("https://auth.example.com/token", "cid", "csecret", None)

    assert first == second == "tok-1"
    mock_post.assert_called_once()  # second call served from cache, no new HTTP request


def test_fetch_oauth2_token_refetches_after_expiry(mocker) -> None:
    mocker.patch(
        "ai_etl.sources.rest_source.httpx.post",
        side_effect=[_mock_token_response("tok-1", 1), _mock_token_response("tok-2", 3600)],
    )
    mock_monotonic = mocker.patch("ai_etl.sources.rest_source.time.monotonic")
    mock_monotonic.side_effect = [0.0, 100.0, 100.0]  # fetch @0, check-and-refetch @100

    first = _fetch_oauth2_token("https://auth.example.com/token", "cid", "csecret", None)
    second = _fetch_oauth2_token("https://auth.example.com/token", "cid", "csecret", None)

    assert first == "tok-1"
    assert second == "tok-2"


def test_fetch_oauth2_token_different_client_ids_not_shared(mocker) -> None:
    mocker.patch(
        "ai_etl.sources.rest_source.httpx.post",
        side_effect=[_mock_token_response("tok-a"), _mock_token_response("tok-b")],
    )

    token_a = _fetch_oauth2_token("https://auth.example.com/token", "client-a", "secret", None)
    token_b = _fetch_oauth2_token("https://auth.example.com/token", "client-b", "secret", None)

    assert token_a == "tok-a"
    assert token_b == "tok-b"


def test_build_auth_oauth2_reads_client_id_and_secret_from_env(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_CLIENT_ID", "cid-123")
    monkeypatch.setenv("MY_CLIENT_SECRET", "csecret-456")
    mock_post = mocker.patch(
        "ai_etl.sources.rest_source.httpx.post", return_value=_mock_token_response("tok-abc")
    )

    headers, auth = _build_auth(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://auth.example.com/token",
            "client_id_env_var": "MY_CLIENT_ID",
            "client_secret_env_var": "MY_CLIENT_SECRET",
        }
    )

    assert headers == {"Authorization": "Bearer tok-abc"}
    assert auth is None
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["client_id"] == "cid-123"
    assert kwargs["data"]["client_secret"] == "csecret-456"


def test_build_auth_oauth2_missing_client_id_env_var_raises(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSING_CLIENT_ID", raising=False)
    mock_post = mocker.patch("ai_etl.sources.rest_source.httpx.post")

    with pytest.raises(EnvironmentError, match="MISSING_CLIENT_ID"):
        _build_auth(
            {
                "type": "oauth2_client_credentials",
                "token_url": "https://auth.example.com/token",
                "client_id_env_var": "MISSING_CLIENT_ID",
                "client_secret_env_var": "MY_CLIENT_SECRET",
            }
        )
    mock_post.assert_not_called()


def test_load_rest_oauth2_sends_bearer_token_from_fetched_token(
    mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_CLIENT_ID", "cid-123")
    monkeypatch.setenv("MY_CLIENT_SECRET", "csecret-456")
    mocker.patch(
        "ai_etl.sources.rest_source.httpx.post", return_value=_mock_token_response("tok-abc")
    )
    mock_get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get",
        return_value=_mock_response([{"id": 1}]),
    )

    load_rest(
        "http://example.com/api",
        auth={
            "type": "oauth2_client_credentials",
            "token_url": "https://auth.example.com/token",
            "client_id_env_var": "MY_CLIENT_ID",
            "client_secret_env_var": "MY_CLIENT_SECRET",
        },
    )

    _, kwargs = mock_get.call_args
    assert kwargs["headers"] == {"Authorization": "Bearer tok-abc"}


def test_fetch_oauth2_token_default_ttl_when_expires_in_omitted(mocker) -> None:
    response = MagicMock()
    response.json.return_value = {"access_token": "tok-1"}  # no expires_in
    response.raise_for_status.return_value = None
    mocker.patch("ai_etl.sources.rest_source.httpx.post", return_value=response)

    token = _fetch_oauth2_token("https://auth.example.com/token", "cid", "csecret", None)

    assert token == "tok-1"
    cached_token, expires_at = rest_source._TOKEN_CACHE[("https://auth.example.com/token", "cid")]
    assert cached_token == "tok-1"
    # default TTL (3600s) minus the leeway (30s), roughly — just assert it's
    # comfortably in the future relative to "now".
    assert expires_at > time.monotonic()
