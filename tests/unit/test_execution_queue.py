"""Unit tests for services/execution_queue.py (Sprint 3, ADR-008).

Rate limiting is tested against a tiny in-memory fake standing in for
`redis.Redis` (no real Redis needed) — mirrors this project's existing
preference (see tests/unit/test_audit_db.py) for exercising real logic
against a lightweight fake rather than mocking away the behavior under test.
`enqueue_analysis`/`get_task_status` are tested with Celery's own `.delay()`/
`AsyncResult` monkeypatched, since actually connecting to a broker belongs in
tests/integration, not here.
"""

from __future__ import annotations

import pytest

from ai_etl.services import execution_queue as eq_module
from ai_etl.services.execution_queue import (
    RateLimitExceededError,
    check_and_increment_rate_limit,
    enqueue_analysis,
    get_task_status,
)


class _FakeRedis:
    """Minimal INCR/EXPIRE fake — enough to exercise the fixed-window counter
    without a real Redis instance."""

    def __init__(self) -> None:
        self._store: dict[str, int] = {}
        self.expired_keys: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    def expire(self, key: str, seconds: int) -> None:
        self.expired_keys[key] = seconds


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(eq_module, "_redis_client", lambda: fake)
    monkeypatch.setattr(eq_module, "RATE_LIMIT_MAX_RUNS", 3)
    monkeypatch.setattr(eq_module, "RATE_LIMIT_WINDOW_SECONDS", 3600)
    return fake


def test_rate_limit_allows_calls_under_the_cap(_fake_redis: _FakeRedis) -> None:
    for _ in range(3):
        check_and_increment_rate_limit("tenant-a")  # should not raise


def test_rate_limit_blocks_the_call_that_exceeds_the_cap(_fake_redis: _FakeRedis) -> None:
    for _ in range(3):
        check_and_increment_rate_limit("tenant-a")
    with pytest.raises(RateLimitExceededError):
        check_and_increment_rate_limit("tenant-a")


def test_rate_limit_is_scoped_per_tenant(_fake_redis: _FakeRedis) -> None:
    for _ in range(3):
        check_and_increment_rate_limit("tenant-a")
    # tenant-b has its own counter — must not be blocked by tenant-a's usage.
    check_and_increment_rate_limit("tenant-b")


def test_rate_limit_sets_ttl_only_on_first_increment(_fake_redis: _FakeRedis) -> None:
    check_and_increment_rate_limit("tenant-a")
    check_and_increment_rate_limit("tenant-a")
    assert len(_fake_redis.expired_keys) == 1


def test_enqueue_analysis_raises_before_touching_celery_when_over_cap(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"delay": False}

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> object:
            called["delay"] = True
            raise AssertionError("delay() should not be called once over the rate limit")

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    for _ in range(3):
        check_and_increment_rate_limit("tenant-a")

    with pytest.raises(RateLimitExceededError):
        enqueue_analysis("spec", "question", "./runs", "tenant-a")
    assert called["delay"] is False


def test_enqueue_analysis_enqueues_and_returns_task_id(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeAsyncResult:
        id = "task-123"

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> _FakeAsyncResult:
            return _FakeAsyncResult()

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    task_id = enqueue_analysis("spec", "question", "./runs", "tenant-a")
    assert task_id == "task-123"


def test_get_task_status_maps_success_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        state = "SUCCESS"

        def ready(self) -> bool:
            return True

        def successful(self) -> bool:
            return True

        def failed(self) -> bool:
            return False

        result = {"run_id": "abc", "status": "completed"}

    monkeypatch.setattr(eq_module, "AsyncResult", lambda task_id, app: _FakeResult())

    status = get_task_status("task-123")
    assert status["state"] == "SUCCESS"
    assert status["ready"] is True
    assert status["result"] == {"run_id": "abc", "status": "completed"}
    assert status["error"] is None


def test_get_task_status_maps_failure_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        state = "FAILURE"
        result = ValueError("boom")

        def ready(self) -> bool:
            return True

        def successful(self) -> bool:
            return False

        def failed(self) -> bool:
            return True

    monkeypatch.setattr(eq_module, "AsyncResult", lambda task_id, app: _FakeResult())

    status = get_task_status("task-123")
    assert status["state"] == "FAILURE"
    assert status["result"] is None
    assert status["error"] == "boom"
