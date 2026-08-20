"""Unit tests for saved-pipeline CRUD in audit/db.py (Sprint 13, ADR-016).

Same style as `test_audit_db.py`'s `load_history` tests — a real in-memory
SQLite engine, not a mocked `get_engine`, so the actual `select`/`insert`/
`update` statements execute for real (all plain SQLAlchemy Core, no
`ON CONFLICT`, so this is portable to SQLite without any dialect-specific
rewriting).
"""

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy
from sqlalchemy import Engine

from ai_etl.audit import db
from ai_etl.audit.models import metadata as audit_metadata


def _make_sqlite_engine() -> Engine:
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    audit_metadata.create_all(engine)
    return engine


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = _make_sqlite_engine()
    monkeypatch.setattr(db, "get_engine", lambda: eng)
    return eng


def test_create_saved_pipeline_returns_full_row(engine: Engine) -> None:
    created = db.create_saved_pipeline(
        tenant_id="tenant-a",
        name="Nightly Postgres sync",
        source_type="postgres",
        spec="Read schema.orders from postgres, load into schema.orders_clean",
        cron_schedule="0 3 * * *",
        business_question="",
    )

    assert created["tenant_id"] == "tenant-a"
    assert created["name"] == "Nightly Postgres sync"
    assert created["source_type"] == "postgres"
    assert created["is_active"] is True
    assert created["last_task_id"] is None
    assert created["next_run_at"] > datetime.now(tz=timezone.utc)


def test_list_saved_pipelines_scopes_by_tenant(engine: Engine) -> None:
    db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    db.create_saved_pipeline("tenant-b", "B", "rest", "spec b", "0 4 * * *")

    result = db.list_saved_pipelines("tenant-a")

    assert len(result) == 1
    assert result[0]["name"] == "A"


def test_get_saved_pipeline_returns_none_for_other_tenant(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")

    assert db.get_saved_pipeline(created["id"], "tenant-b") is None
    assert db.get_saved_pipeline(created["id"], "tenant-a") is not None


def test_update_saved_pipeline_partial_update_only_touches_given_fields(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")

    updated = db.update_saved_pipeline(created["id"], "tenant-a", name="Renamed")

    assert updated is not None
    assert updated["name"] == "Renamed"
    assert updated["spec"] == "spec a"
    assert updated["cron_schedule"] == "0 3 * * *"


def test_update_saved_pipeline_returns_none_for_unknown_id(engine: Engine) -> None:
    assert db.update_saved_pipeline("no-such-id", "tenant-a", name="x") is None


def test_update_saved_pipeline_recomputes_next_run_when_cron_changes(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    original_next_run = created["next_run_at"]

    updated = db.update_saved_pipeline(created["id"], "tenant-a", cron_schedule="0 4 * * *")

    assert updated is not None
    assert updated["next_run_at"] != original_next_run


def test_update_saved_pipeline_resuming_recomputes_next_run_from_now(engine: Engine) -> None:
    """Resuming a paused pipeline must schedule from "now", not silently
    catch up on every tick missed while paused."""
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "* * * * *")
    db.update_saved_pipeline(created["id"], "tenant-a", is_active=False)

    resumed = db.update_saved_pipeline(created["id"], "tenant-a", is_active=True)

    assert resumed is not None
    assert resumed["is_active"] is True
    # SQLite (unlike Postgres) doesn't round-trip tzinfo on DateTime columns
    # — compare naive-vs-naive here; the tz-awareness itself is exercised by
    # test_create_saved_pipeline_returns_full_row against the live object
    # returned before any DB round-trip.
    next_run_naive = resumed["next_run_at"].replace(tzinfo=None)
    cutoff_naive = (datetime.now(tz=timezone.utc) - timedelta(seconds=5)).replace(tzinfo=None)
    assert next_run_naive > cutoff_naive


def test_list_due_pipelines_only_returns_active_and_past_next_run(engine: Engine) -> None:
    now = datetime.now(tz=timezone.utc)
    due = db.create_saved_pipeline("tenant-a", "Due", "postgres", "spec", "* * * * *")
    not_due = db.create_saved_pipeline("tenant-a", "NotDue", "postgres", "spec", "0 3 * * *")
    paused = db.create_saved_pipeline("tenant-a", "Paused", "postgres", "spec", "* * * * *")

    # Force `due`/`paused` into the past so they'd otherwise be picked up.
    db.update_saved_pipeline(due["id"], "tenant-a")  # no-op, keep as-is
    with engine.begin() as conn:
        from ai_etl.audit.models import saved_pipelines

        conn.execute(
            saved_pipelines.update()
            .where(saved_pipelines.c.id == due["id"])
            .values(next_run_at=now - timedelta(minutes=1))
        )
        conn.execute(
            saved_pipelines.update()
            .where(saved_pipelines.c.id == paused["id"])
            .values(next_run_at=now - timedelta(minutes=1), is_active=False)
        )

    due_pipelines = db.list_due_pipelines(now)

    ids = {p["id"] for p in due_pipelines}
    assert due["id"] in ids
    assert not_due["id"] not in ids
    assert paused["id"] not in ids


def test_claim_due_pipeline_succeeds_when_next_run_at_matches(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "* * * * *")
    expected = created["next_run_at"].replace(tzinfo=None)
    new_next_run = expected + timedelta(minutes=1)

    won = db.claim_due_pipeline(created["id"], expected, new_next_run)

    assert won is True
    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["next_run_at"] == new_next_run


def test_claim_due_pipeline_is_a_compare_and_swap_second_caller_loses(engine: Engine) -> None:
    """The concurrency guard itself (Sprint 13 code review fix): two
    "ticks" racing to claim the same due fire — only the first wins, the
    second (whose `expected_next_run_at` no longer matches once the winner
    committed) must get `False`, never overwrite the winner's claim."""
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "* * * * *")
    expected = created["next_run_at"].replace(tzinfo=None)
    tick_a_next = expected + timedelta(minutes=1)
    tick_b_next = expected + timedelta(minutes=1)  # computed independently, same value

    won_by_a = db.claim_due_pipeline(created["id"], expected, tick_a_next)
    won_by_b = db.claim_due_pipeline(created["id"], expected, tick_b_next)

    assert won_by_a is True
    assert won_by_b is False  # lost the race — next_run_at no longer equals `expected`

    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["next_run_at"] == tick_a_next  # tick A's claim, untouched by tick B


def test_claim_due_pipeline_returns_false_for_unknown_id(engine: Engine) -> None:
    now = datetime.now(tz=timezone.utc)
    won = db.claim_due_pipeline("no-such-id", now, now + timedelta(minutes=1))
    assert won is False


def test_release_pipeline_claim_reverts_next_run_at(engine: Engine) -> None:
    """A failed `enqueue_analysis` after a successful claim must roll the
    claim back so the pipeline is retried on the very next tick, not
    silently skipped for a full cron period."""
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "* * * * *")
    original = created["next_run_at"].replace(tzinfo=None)
    claimed_next = original + timedelta(minutes=1)

    db.claim_due_pipeline(created["id"], original, claimed_next)
    db.release_pipeline_claim(created["id"], claimed_next, original)

    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["next_run_at"] == original


def test_record_pipeline_run_updates_task_and_leaves_next_run_at_untouched(
    engine: Engine,
) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "* * * * *")
    original = created["next_run_at"].replace(tzinfo=None)
    claimed_next = original + timedelta(minutes=1)
    db.claim_due_pipeline(created["id"], original, claimed_next)

    db.record_pipeline_run(created["id"], "task-123")

    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["last_task_id"] == "task-123"
    assert reloaded["last_run_at"] is not None
    # next_run_at is exactly what claim_due_pipeline set — record_pipeline_run
    # must not touch it (the claim, not the record step, owns that field).
    assert reloaded["next_run_at"] == claimed_next
