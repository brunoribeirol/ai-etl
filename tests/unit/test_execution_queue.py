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

import base64
from pathlib import Path

import pytest
from celery.exceptions import Retry

from ai_etl.services import execution_queue as eq_module
from ai_etl.services.execution_queue import (
    BudgetExceededError,
    RateLimitExceededError,
    check_and_increment_rate_limit,
    check_budget_cap,
    enqueue_analysis,
    get_budget_status,
    get_task_status,
    run_full_analysis_task,
)


class _FakeRedis:
    """Minimal INCR/EXPIRE/SET-NX/DELETE fake — enough to exercise the
    fixed-window rate-limit counter and the Sprint 29 budget in-flight lock
    without a real Redis instance."""

    def __init__(self) -> None:
        self._store: dict[str, int] = {}
        self.expired_keys: dict[str, int] = {}
        self._locks: set[str] = set()

    def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    def expire(self, key: str, seconds: int) -> None:
        self.expired_keys[key] = seconds

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        """Mirrors `redis.Redis.set(..., nx=True)`: returns `True` and sets
        the key if (not `nx`) or the key is currently absent; returns `False`
        without touching the key if `nx` and the key already exists — the
        exact "one winner" semantics `_try_acquire_budget_inflight_lock`
        relies on."""
        if nx and key in self._locks:
            return False
        self._locks.add(key)
        return True

    def delete(self, key: str) -> None:
        self._locks.discard(key)


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(eq_module, "_redis_client", lambda: fake)
    monkeypatch.setattr(eq_module, "RATE_LIMIT_MAX_RUNS", 3)
    monkeypatch.setattr(eq_module, "RATE_LIMIT_WINDOW_SECONDS", 3600)
    # Sprint 29 (ADR-019): default every test to "no budget cap configured"
    # (a real Postgres call otherwise) — tests below that actually exercise
    # budget enforcement override this via monkeypatch themselves.
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: None)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 0.0)
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


def test_enqueue_analysis_base64_encodes_file_bytes(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security/correctness regression for the cross-container upload fix:
    `enqueue_analysis` must base64-encode raw bytes before they cross the
    Celery/Redis JSON boundary (task_serializer="json" can't carry bytes
    directly) and must pass `file_path` through unchanged."""
    captured: dict = {}

    class _FakeAsyncResult:
        id = "task-123"

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> _FakeAsyncResult:
            captured["args"] = args
            return _FakeAsyncResult()

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    enqueue_analysis(
        "spec",
        "question",
        "./runs",
        "tenant-a",
        file_path="runs/uploads/abc123.csv",
        file_bytes=b"order_id,amt\n1,10.5\n",
    )

    spec, question, run_dir, tenant_id, file_path, file_bytes_b64, saved_pipeline_id = captured[
        "args"
    ]
    assert file_path == "runs/uploads/abc123.csv"
    assert base64.b64decode(file_bytes_b64) == b"order_id,amt\n1,10.5\n"
    assert saved_pipeline_id is None


def test_enqueue_analysis_omits_file_args_when_no_upload(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manual-spec textarea flow (no file upload) must still work --
    file_path/file_bytes default to None, not required kwargs."""
    captured: dict = {}

    class _FakeAsyncResult:
        id = "task-123"

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> _FakeAsyncResult:
            captured["args"] = args
            return _FakeAsyncResult()

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    enqueue_analysis("spec", "question", "./runs", "tenant-a")

    assert captured["args"][4] is None
    assert captured["args"][5] is None
    assert captured["args"][6] is None


def test_enqueue_analysis_threads_saved_pipeline_id_through_to_delay(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sprint 17 (ADR-017) — a scheduled fire's `saved_pipeline_id` must reach
    the Celery task payload, so `run_full_analysis_task` can forward it to
    `save_run`/`save_analysis` and link the resulting run back to its
    pipeline."""
    captured: dict = {}

    class _FakeAsyncResult:
        id = "task-123"

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> _FakeAsyncResult:
            captured["args"] = args
            return _FakeAsyncResult()

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    enqueue_analysis("spec", "question", "./runs", "tenant-a", saved_pipeline_id="pipeline-xyz")

    assert captured["args"][6] == "pipeline-xyz"


def test_run_full_analysis_task_rematerializes_file_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core of the cross-container fix: given file_path/file_bytes_b64,
    the task must write the file to disk on *this* (worker) filesystem
    before calling run_full_analysis -- otherwise the Extractor's file read
    fails exactly like the bug this test guards against."""
    dest = tmp_path / "uploads" / "abc123.csv"
    file_existed_at_call_time = {}

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        file_existed_at_call_time["exists"] = dest.exists()
        file_existed_at_call_time["content"] = dest.read_bytes() if dest.exists() else None
        return {"state": {"run_id": "r1", "status": "completed", "error": None}, "tokens": {}}

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)

    run_full_analysis_task(
        "spec",
        "question",
        str(tmp_path),
        "tenant-a",
        file_path=str(dest),
        file_bytes_b64=base64.b64encode(b"order_id,amt\n1,10.5\n").decode("ascii"),
    )

    assert file_existed_at_call_time["exists"] is True
    assert file_existed_at_call_time["content"] == b"order_id,amt\n1,10.5\n"


def test_run_full_analysis_task_skips_write_when_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The manual-spec flow (no upload) must not require file_path/file_bytes_b64."""

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {"state": {"run_id": "r1", "status": "completed", "error": None}, "tokens": {}}

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)

    result = run_full_analysis_task("spec", "question", "./runs", "tenant-a")
    assert result["run_id"] == "r1"


def test_run_full_analysis_task_forwards_saved_pipeline_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 17 (ADR-017) — the Celery task's own `saved_pipeline_id` kwarg
    must reach `run_full_analysis`, not just sit unused on the task."""
    captured: dict = {}

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        captured["saved_pipeline_id"] = saved_pipeline_id
        return {"state": {"run_id": "r1", "status": "completed", "error": None}, "tokens": {}}

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)
    # Sprint 14 (ADR-018): the merged task now also runs a best-effort drift
    # check whenever `saved_pipeline_id` is set and the run completed — mock
    # `get_saved_pipeline` so that path doesn't hit a real DB connection in
    # this unit test (its own behavior is covered by the dedicated drift
    # tests below).
    monkeypatch.setattr(eq_module, "get_saved_pipeline", lambda *a, **k: None)
    # Sprint 15 (ADR-020): same reasoning — the health-tracking cache write
    # is a real DB call, mocked here so this test stays a pure unit test.
    monkeypatch.setattr(eq_module, "record_pipeline_health", lambda *a, **k: None)

    run_full_analysis_task(
        "spec", "question", "./runs", "tenant-a", saved_pipeline_id="pipeline-xyz"
    )

    assert captured["saved_pipeline_id"] == "pipeline-xyz"


def test_run_full_analysis_task_runs_drift_check_when_scheduled_and_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {
            "state": {
                "run_id": "r1",
                "status": "completed",
                "error": None,
                "load_result": {"rows_loaded": 42},
            },
            "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "science": [],
            "advisor": {},
        }

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)
    monkeypatch.setattr(
        eq_module,
        "get_saved_pipeline",
        lambda pid, tid: {"id": pid, "tenant_id": tid, "name": "P", "is_active": True},
    )
    # Sprint 15 (ADR-020): mock the health-tracking write for the same
    # reason as every other DB-touching call in this test — pure unit test.
    monkeypatch.setattr(eq_module, "record_pipeline_health", lambda *a, **k: None)
    called = {}

    def _fake_check(**kwargs):
        called.update(kwargs)
        return {"triggered": False, "findings": [], "email_sent": False, "slack_sent": False}

    monkeypatch.setattr(eq_module, "check_drift_and_notify", _fake_check)

    run_full_analysis_task("spec", "question", "./runs", "tenant-a", saved_pipeline_id="pl-1")

    assert called["run_id"] == "r1"
    assert called["rows_loaded"] == 42


def test_run_full_analysis_task_skips_drift_check_for_avulso_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {"state": {"run_id": "r1", "status": "completed", "error": None}, "tokens": {}}

    called = {"get_saved_pipeline": False}

    def _tracked_get_saved_pipeline(*args: object, **kwargs: object) -> None:
        called["get_saved_pipeline"] = True
        return None

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)
    monkeypatch.setattr(eq_module, "get_saved_pipeline", _tracked_get_saved_pipeline)

    # No saved_pipeline_id passed — must never touch get_saved_pipeline/drift check.
    run_full_analysis_task("spec", "question", "./runs", "tenant-a")

    assert called["get_saved_pipeline"] is False


def test_run_full_analysis_task_swallows_drift_check_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken drift check must never fail the run itself — the run already
    completed and was persisted before this best-effort step runs."""

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {"state": {"run_id": "r1", "status": "completed", "error": None}, "tokens": {}}

    def _raise_get_saved_pipeline(*args, **kwargs):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)
    monkeypatch.setattr(eq_module, "get_saved_pipeline", _raise_get_saved_pipeline)
    monkeypatch.setattr(eq_module, "record_pipeline_health", lambda *a, **k: None)

    result = run_full_analysis_task(
        "spec", "question", "./runs", "tenant-a", saved_pipeline_id="pl-1"
    )

    assert result["run_id"] == "r1"
    assert result["status"] == "completed"


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


# --- Sprint 29 (ADR-019): tenant budget cap -------------------------------


def test_budget_status_reports_no_cap_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: None)
    status = get_budget_status("tenant-a")
    assert status == {
        "cap_usd": None,
        "spent_usd": 0.0,
        "ratio": None,
        "near_limit": False,
        "exceeded": False,
    }


def test_budget_status_under_cap_is_not_near_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 10.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 1.0)
    status = get_budget_status("tenant-a")
    assert status["near_limit"] is False
    assert status["exceeded"] is False
    assert status["ratio"] == pytest.approx(0.1)


def test_budget_status_flags_near_limit_before_it_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 10.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 8.5)
    status = get_budget_status("tenant-a")
    assert status["near_limit"] is True
    assert status["exceeded"] is False


def test_budget_status_flags_exceeded_and_not_near_limit_once_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # exceeded and near_limit are mutually exclusive in this shape — "near
    # limit" only describes the warning zone strictly below the cap.
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 10.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 10.0)
    status = get_budget_status("tenant-a")
    assert status["exceeded"] is True
    assert status["near_limit"] is False


def test_check_budget_cap_raises_once_spend_meets_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 5.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 5.0)
    with pytest.raises(BudgetExceededError):
        check_budget_cap("tenant-a")


def test_check_budget_cap_does_not_raise_under_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 5.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 4.99)
    check_budget_cap("tenant-a")  # should not raise


def test_check_budget_cap_never_raises_when_no_cap_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: None)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 999_999.0)
    check_budget_cap("tenant-a")  # should not raise — no cap means unlimited


def test_enqueue_analysis_raises_before_touching_celery_when_over_budget(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 1.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 1.0)

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("delay() should not be called once over budget")

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    with pytest.raises(BudgetExceededError):
        enqueue_analysis("spec", "question", "./runs", "tenant-a")


def test_enqueue_analysis_checks_budget_before_rate_limit(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-PR-#63 code review fix: budget is checked *before* the rate
    limit, not after — a tenant over both must see the budget error, and
    must not have consumed a rate-limit slot for a run that never executed
    (see the next test for that second assertion)."""
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 1.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 1.0)
    for _ in range(3):
        check_and_increment_rate_limit("tenant-a")

    with pytest.raises(BudgetExceededError):
        enqueue_analysis("spec", "question", "./runs", "tenant-a")


def test_enqueue_analysis_over_budget_does_not_consume_a_rate_limit_slot(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core bug from the code review: a 402 (budget) rejection must not
    also cost the tenant a 429 (rate-limit) slot — otherwise a tenant near
    the rate limit but already over budget stays locked out of legitimate
    calls for the rest of the window even after fixing their budget, for
    runs that never actually executed."""
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 1.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 1.0)

    for _ in range(3):
        with pytest.raises(BudgetExceededError):
            enqueue_analysis("spec", "question", "./runs", "tenant-a")

    # Rate limit is still fully available — none of the 3 rejected calls
    # above touched `check_and_increment_rate_limit`'s counter.
    for _ in range(3):
        check_and_increment_rate_limit("tenant-a")  # should not raise


def test_concurrent_enqueue_for_a_capped_tenant_only_one_passes(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-019 addendum (code review fix): two concurrent `enqueue_analysis`
    calls for the same capped tenant, both reading `spent < cap` before
    either's cost lands in Postgres, must not both be enqueued — the second
    must be rejected by the in-flight lock, not silently allowed through."""
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 10.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 1.0)

    class _FakeAsyncResult:
        id = "task-1"

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> _FakeAsyncResult:
            return _FakeAsyncResult()

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    first_task_id = enqueue_analysis("spec", "question", "./runs", "tenant-a")
    assert first_task_id == "task-1"

    # The first run's cost hasn't landed yet (still "in flight") — a second
    # concurrent call for the same tenant must be rejected, not enqueued.
    with pytest.raises(BudgetExceededError):
        enqueue_analysis("spec", "question", "./runs", "tenant-a")


def test_inflight_lock_is_released_after_the_task_finishes(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once `run_full_analysis_task` finishes (its `finally` releases the
    lock), a subsequent `enqueue_analysis` call for the same tenant must be
    allowed again."""
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 10.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 1.0)

    class _FakeAsyncResult:
        id = "task-1"

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> _FakeAsyncResult:
            return _FakeAsyncResult()

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    enqueue_analysis("spec", "question", "./runs", "tenant-a")

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {"state": {"run_id": "r1", "status": "completed", "error": None}, "tokens": {}}

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)
    run_full_analysis_task("spec", "question", "./runs", "tenant-a")

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())
    second_task_id = enqueue_analysis("spec", "question", "./runs", "tenant-a")
    assert second_task_id == "task-1"


def test_inflight_lock_is_released_when_enqueueing_itself_fails(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `check_budget_cap` acquires the lock but the rate limiter then
    rejects the call (the run never actually starts), the lock must be
    released immediately rather than held for the full safety-net TTL."""
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: 10.0)
    monkeypatch.setattr(eq_module, "get_monthly_spend_usd", lambda tenant_id: 1.0)
    for _ in range(3):
        check_and_increment_rate_limit("tenant-a")

    with pytest.raises(RateLimitExceededError):
        enqueue_analysis("spec", "question", "./runs", "tenant-a")

    # The lock must have been released, not left held by the failed attempt —
    # confirmed by directly acquiring it again.
    assert eq_module._try_acquire_budget_inflight_lock("tenant-a") is True


def test_uncapped_tenant_never_touches_the_inflight_lock(
    _fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `monthly_budget_usd` configured — concurrent enqueues must not be
    serialized at all (the lock is only acquired once a cap exists)."""
    monkeypatch.setattr(eq_module, "get_monthly_budget", lambda tenant_id: None)

    class _FakeAsyncResult:
        id = "task-1"

    class _FakeTask:
        def delay(self, *args: object, **kwargs: object) -> _FakeAsyncResult:
            return _FakeAsyncResult()

    monkeypatch.setattr(eq_module, "run_full_analysis_task", _FakeTask())

    enqueue_analysis("spec", "question", "./runs", "tenant-a")
    enqueue_analysis("spec", "question", "./runs", "tenant-a")  # should not raise


# --- Sprint 15 (ADR-020): scheduled-pipeline reliability -------------------
#
# `self.retry()`'s real machinery isn't exercised here — it involves a real
# Celery request/broker context this project's local sandbox has a known,
# unresolved hang bug with (see Vault `bugs-solved/mypy-pytest-hang-agent-
# sandbox.md`'s 2026-08-21 update). Instead: `run_full_analysis_task.retry`
# is monkeypatched directly (it's a bound method on the Task instance the
# module-level `run_full_analysis_task` name *is*, since Celery's `Task
# .__call__` binds `self` to that same instance when called directly, the
# way every test in this file already calls it) — this tests the task's own
# retry *decision* logic without touching Celery's real retry transport.
# `push_request`/`pop_request` are Celery's own lightweight, in-memory,
# officially-supported way to simulate `self.request.retries` — no broker,
# no I/O, no hang risk.


def test_level_b_retry_countdown_doubles_per_attempt() -> None:
    assert eq_module._level_b_retry_countdown(0) == 60
    assert eq_module._level_b_retry_countdown(1) == 120
    assert eq_module._level_b_retry_countdown(2) == 240


def test_run_full_analysis_task_retries_on_logical_failure_for_scheduled_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The roadmap's own worked example: a source-connection failure caught
    internally by `agents/extractor.py` (`status="failed"`, no exception) on
    a *scheduled* fire must trigger a real retry, not be treated as done."""

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {
            "state": {"run_id": "r1", "status": "failed", "error": "source unavailable"},
            "tokens": {},
        }

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)

    retry_calls: list[int | None] = []

    def _fake_retry(countdown: int | None = None, **kwargs: object) -> None:
        retry_calls.append(countdown)
        # Raise the *real* `celery.exceptions.Retry`, not a plain custom
        # exception: the task decorator's own `autoretry_for=(Exception,)`
        # (Level A) wraps this call, and Celery's real machinery explicitly
        # excludes `Retry` from being re-caught there -- a plain `Exception`
        # subclass would instead get caught by that wrapper and fed back
        # into this same (patched) `.retry()` a second time, double-calling
        # it with a *different*, Celery-computed backoff. Raising the real
        # `Retry` type reproduces production's actual exclusion behavior.
        raise Retry()

    monkeypatch.setattr(run_full_analysis_task, "retry", _fake_retry)

    with pytest.raises(Retry):
        run_full_analysis_task("spec", "question", "./runs", "tenant-a", saved_pipeline_id="pl-1")

    assert retry_calls == [60]  # attempt 1 (retries=0 by default) -> 60s countdown


def test_run_full_analysis_task_never_retries_a_failed_avulso_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-off `POST /runs` call (no `saved_pipeline_id`) must never be
    auto-retried — the human watching the result is the retry decision-maker
    for that flow, per ADR-020 Decision 1."""

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {"state": {"run_id": "r1", "status": "failed", "error": "boom"}, "tokens": {}}

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("retry() must not be called for an avulso run")

    monkeypatch.setattr(run_full_analysis_task, "retry", _fail_if_called)

    result = run_full_analysis_task("spec", "question", "./runs", "tenant-a")
    assert result["status"] == "failed"


def test_run_full_analysis_task_never_retries_or_alerts_on_awaiting_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 27 (ADR-028 Decision 3): a gated write (`status="awaiting_approval"`)
    on a scheduled fire is a legitimate pause, not a failure — must never trigger
    Level-B retry nor `record_pipeline_health` (which would otherwise increment
    `consecutive_failures` and eventually mis-fire Sprint 15's health alert)."""

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {
            "state": {"run_id": "r1", "status": "awaiting_approval", "error": None},
            "tokens": {},
        }

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("retry() must not be called for an awaiting_approval fire")

    monkeypatch.setattr(run_full_analysis_task, "retry", _fail_if_called)

    def _fail_if_recorded(*args: object, **kwargs: object) -> None:
        raise AssertionError("record_pipeline_health must not be called for awaiting_approval")

    monkeypatch.setattr(eq_module, "record_pipeline_health", _fail_if_recorded)

    result = run_full_analysis_task(
        "spec", "question", "./runs", "tenant-a", saved_pipeline_id="pl-1"
    )

    assert result["status"] == "awaiting_approval"


def test_run_full_analysis_task_stops_retrying_once_max_retries_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once `self.request.retries` reaches `SCHEDULED_PIPELINE_MAX_RETRIES`,
    the task must stop retrying and record the fire's health instead —
    never retry forever."""

    def _fake_run_full_analysis(
        spec,
        business_question,
        run_dir,
        progress_callback=None,
        tenant_id=None,
        saved_pipeline_id=None,
    ):
        return {
            "state": {"run_id": "r1", "status": "failed", "error": "still broken"},
            "tokens": {},
        }

    monkeypatch.setattr(eq_module, "run_full_analysis", _fake_run_full_analysis)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("retry() must not be called once retries are exhausted")

    monkeypatch.setattr(run_full_analysis_task, "retry", _fail_if_called)

    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        eq_module,
        "record_pipeline_health",
        lambda pipeline_id, status, error=None: recorded.update(
            pipeline_id=pipeline_id, status=status, error=error
        ),
    )
    monkeypatch.setattr(eq_module, "get_saved_pipeline", lambda *a, **k: None)

    run_full_analysis_task.push_request(retries=eq_module.SCHEDULED_PIPELINE_MAX_RETRIES)
    try:
        result = run_full_analysis_task(
            "spec", "question", "./runs", "tenant-a", saved_pipeline_id="pl-1"
        )
    finally:
        run_full_analysis_task.pop_request()

    assert result["status"] == "failed"
    assert recorded == {"pipeline_id": "pl-1", "status": "failed", "error": "still broken"}


# --- Sprint 15 (ADR-020): health-tracking + alerting side effect ----------


def test_record_scheduled_pipeline_health_resets_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        eq_module,
        "record_pipeline_health",
        lambda pipeline_id, status, error=None: recorded.update(
            pipeline_id=pipeline_id, status=status, error=error
        ),
    )

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("a successful fire must never trigger a health alert")

    monkeypatch.setattr(eq_module, "check_and_alert_pipeline_health", _fail_if_called)

    eq_module._record_scheduled_pipeline_health_best_effort("pl-1", "tenant-a", "completed", None)

    assert recorded == {"pipeline_id": "pl-1", "status": "completed", "error": None}


def test_record_scheduled_pipeline_health_alerts_exactly_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`== HEALTH_ALERT_FAILURE_THRESHOLD`, not `>=` — a pipeline crossing
    the threshold alerts once; staying broken past it must not re-alert on
    every subsequent failure."""
    monkeypatch.setattr(eq_module, "record_pipeline_health", lambda *a, **k: None)
    monkeypatch.setattr(eq_module, "HEALTH_ALERT_FAILURE_THRESHOLD", 3)

    alert_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        eq_module, "check_and_alert_pipeline_health", lambda pipeline: alert_calls.append(pipeline)
    )

    def _pipeline_with(failures: int) -> dict[str, object]:
        return {
            "id": "pl-1",
            "tenant_id": "tenant-a",
            "name": "P",
            "consecutive_failures": failures,
            "last_error": "boom",
        }

    monkeypatch.setattr(eq_module, "get_saved_pipeline", lambda *a, **k: _pipeline_with(2))
    eq_module._record_scheduled_pipeline_health_best_effort("pl-1", "tenant-a", "failed", "boom")
    assert alert_calls == []  # below threshold — no alert yet

    monkeypatch.setattr(eq_module, "get_saved_pipeline", lambda *a, **k: _pipeline_with(3))
    eq_module._record_scheduled_pipeline_health_best_effort("pl-1", "tenant-a", "failed", "boom")
    assert len(alert_calls) == 1  # crossed the threshold — exactly one alert

    monkeypatch.setattr(eq_module, "get_saved_pipeline", lambda *a, **k: _pipeline_with(4))
    eq_module._record_scheduled_pipeline_health_best_effort("pl-1", "tenant-a", "failed", "boom")
    assert len(alert_calls) == 1  # past the threshold — no re-alert


def test_record_scheduled_pipeline_health_swallows_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A health-tracking/alerting hiccup must never propagate — the run it
    describes has already completed (successfully or not) and was already
    persisted before this best-effort step runs."""

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(eq_module, "record_pipeline_health", _raise)

    eq_module._record_scheduled_pipeline_health_best_effort(
        "pl-1", "tenant-a", "failed", "boom"
    )  # should not raise
