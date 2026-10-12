"""Retry of the REST source on connection failures, and the query string of the URL
(sources/rest_source.py).

Real runs, 2026-10-12: 6 runs of scenario 3 ended at extraction with a TLS handshake
timeout to the weather API, after a single request.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from ai_etl.sources import rest_source
from ai_etl.sources.rest_source import load_rest


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def no_sleep(mocker):
    return mocker.patch("ai_etl.sources.rest_source.time.sleep")


def test_retries_a_handshake_timeout_and_returns_the_data(mocker, no_sleep) -> None:
    get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get",
        side_effect=[
            httpx.ConnectTimeout("_ssl.c:993: The handshake operation timed out"),
            _response([{"day": "2025-01-06", "max_temp_c": -1.6}]),
        ],
    )
    df = load_rest("https://example.test/weather")
    assert get.call_count == 2
    assert df.to_dict(orient="records") == [{"day": "2025-01-06", "max_temp_c": -1.6}]
    no_sleep.assert_called_once_with(rest_source._RETRY_BACKOFF_SECONDS[0])


def test_gives_up_after_three_connection_failures(mocker, no_sleep) -> None:
    get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get",
        side_effect=httpx.ConnectError("connection refused"),
    )
    with pytest.raises(httpx.ConnectError):
        load_rest("https://example.test/weather")
    assert get.call_count == rest_source._REQUEST_ATTEMPTS == 3
    assert no_sleep.call_count == 2


def test_does_not_retry_an_http_error_status(mocker, no_sleep) -> None:
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )
    get = mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=response)
    with pytest.raises(httpx.HTTPStatusError):
        load_rest("https://example.test/weather")
    assert get.call_count == 1
    no_sleep.assert_not_called()


def test_a_first_success_makes_one_request_with_a_shorter_connect_timeout(mocker, no_sleep) -> None:
    get = mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=_response([{"a": 1}]))
    load_rest("https://example.test/data", params={"q": "x"})
    assert get.call_count == 1
    timeout = get.call_args.kwargs["timeout"]
    assert timeout.connect == 10.0
    assert timeout.read == 30.0
    no_sleep.assert_not_called()


def _sent_url(get: MagicMock) -> str:
    """The address httpx would request for the call recorded by the mock."""
    call = get.call_args
    return str(httpx.Request("GET", call.args[0], params=call.kwargs["params"]).url)


def test_keeps_the_query_string_written_in_the_url(mocker, no_sleep) -> None:
    """Real failure, 2026-10-12: the parameters inline in the URL were dropped
    (httpx 0.28 replaces the URL's query with `params`, even an empty one)."""
    get = mocker.patch(
        "ai_etl.sources.rest_source.httpx.get", return_value=_response([{"data": "01/09/2026"}])
    )
    load_rest("https://example.test/dados?formato=json&dataInicial=01/09/2026")
    assert _sent_url(get) == "https://example.test/dados?formato=json&dataInicial=01%2F09%2F2026"


def test_merges_the_url_query_with_separate_params(mocker, no_sleep) -> None:
    get = mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=_response([{"a": 1}]))
    load_rest("https://example.test/data?formato=json", params={"page": 2})
    assert _sent_url(get) == "https://example.test/data?formato=json&page=2"


def test_separate_params_alone_still_work(mocker, no_sleep) -> None:
    get = mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=_response([{"a": 1}]))
    load_rest("https://example.test/data", params={"latitude": 40.71})
    assert _sent_url(get) == "https://example.test/data?latitude=40.71"


def test_no_query_at_all_sends_none(mocker, no_sleep) -> None:
    get = mocker.patch("ai_etl.sources.rest_source.httpx.get", return_value=_response([{"a": 1}]))
    load_rest("https://example.test/data")
    assert get.call_args.kwargs["params"] is None
