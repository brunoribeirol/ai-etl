"""Unit tests for `core/logging_config.py` (Sprint 34, ADR-033)."""

from __future__ import annotations

import json
import logging

import pytest

from ai_etl.core.logging_config import JsonFormatter, configure_logging


def _make_record(
    level: int = logging.INFO,
    msg: str = "hello",
    extra: dict | None = None,
    exc_info: bool = False,
) -> logging.LogRecord:
    logger = logging.getLogger("test-json-formatter")
    if exc_info:
        try:
            raise ValueError("boom")
        except ValueError:
            record = logger.makeRecord(
                logger.name, level, "test.py", 1, msg, (), __import__("sys").exc_info()
            )
    else:
        record = logger.makeRecord(logger.name, level, "test.py", 1, msg, (), None)
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_basic_fields_present(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(msg="pipeline started")
        payload = json.loads(formatter.format(record))

        assert payload["level"] == "INFO"
        assert payload["logger"] == "test-json-formatter"
        assert payload["message"] == "pipeline started"
        assert "timestamp" in payload

    def test_output_is_single_line_valid_json(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(msg="line1\nline2")
        rendered = formatter.format(record)

        assert "\n" not in rendered
        json.loads(rendered)  # does not raise

    def test_extra_fields_are_included(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(extra={"tenant_id": "t-1", "run_id": "r-1"})
        payload = json.loads(formatter.format(record))

        assert payload["tenant_id"] == "t-1"
        assert payload["run_id"] == "r-1"

    def test_exception_info_included_when_present(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(level=logging.ERROR, msg="failed", exc_info=True)
        payload = json.loads(formatter.format(record))

        assert "exception" in payload
        assert "ValueError: boom" in payload["exception"]

    def test_no_exception_key_when_absent(self) -> None:
        formatter = JsonFormatter()
        record = _make_record(msg="all good")
        payload = json.loads(formatter.format(record))

        assert "exception" not in payload


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def _reset_root_logger(self) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        yield
        root.handlers = original_handlers
        root.setLevel(original_level)

    def test_attaches_json_handler_to_root_logger(self) -> None:
        configure_logging(level="DEBUG")
        root = logging.getLogger()

        json_handlers = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
        assert len(json_handlers) == 1
        assert root.level == logging.DEBUG

    def test_idempotent_does_not_stack_handlers(self) -> None:
        configure_logging(level="INFO")
        configure_logging(level="INFO")
        root = logging.getLogger()

        json_handlers = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
        assert len(json_handlers) == 1

    def test_defaults_to_env_var_or_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_ETL_LOG_LEVEL", "WARNING")
        configure_logging()

        assert logging.getLogger().level == logging.WARNING
