"""Unit tests for `core/observability.py` (Sprint 34, ADR-033).

Tests the conditional-init contract (`SENTRY_DSN` unset -> no-op,
`SENTRY_DSN` set -> `sentry_sdk.init` called) via mocked `sentry_sdk` calls —
no real Sentry account/DSN is available in this environment (see the
module's own docstring), so this does NOT confirm an event ever reaches a
real Sentry dashboard.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_etl.core.observability import init_sentry


class TestInitSentryNoOp:
    def test_returns_false_when_dsn_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        with patch("ai_etl.core.observability.sentry_sdk") as mock_sdk:
            result = init_sentry(component="api")

        assert result is False
        mock_sdk.init.assert_not_called()

    def test_returns_false_when_dsn_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_DSN", "")
        with patch("ai_etl.core.observability.sentry_sdk") as mock_sdk:
            result = init_sentry(component="worker")

        assert result is False
        mock_sdk.init.assert_not_called()


class TestInitSentryConfigured:
    def test_initializes_sdk_when_dsn_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
        monkeypatch.setenv("AI_ETL_ENV", "staging")

        with patch("ai_etl.core.observability.sentry_sdk") as mock_sdk:
            result = init_sentry(component="api")

            assert result is True
            mock_sdk.init.assert_called_once()
            _, kwargs = mock_sdk.init.call_args
            assert kwargs["dsn"] == "https://public@example.ingest.sentry.io/1"
            assert kwargs["environment"] == "staging"
            mock_sdk.set_tag.assert_called_once_with("component", "api")

    def test_traces_sample_rate_reads_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")

        with patch("ai_etl.core.observability.sentry_sdk") as mock_sdk:
            init_sentry(component="worker")

        _, kwargs = mock_sdk.init.call_args
        assert kwargs["traces_sample_rate"] == 0.5

    def test_defaults_environment_to_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
        monkeypatch.delenv("AI_ETL_ENV", raising=False)

        with patch("ai_etl.core.observability.sentry_sdk") as mock_sdk:
            init_sentry(component="api")

        _, kwargs = mock_sdk.init.call_args
        assert kwargs["environment"] == "dev"
