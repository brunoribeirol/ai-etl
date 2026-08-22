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
from ai_etl.audit.models import analysis_runs
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import runs as runs_table
from ai_etl.audit.models import users as users_table


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


def test_create_saved_pipeline_defaults_drift_threshold(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    assert created["drift_threshold_pct"] == 20.0


def test_create_saved_pipeline_accepts_custom_drift_threshold(engine: Engine) -> None:
    created = db.create_saved_pipeline(
        "tenant-a", "A", "postgres", "spec a", "0 3 * * *", drift_threshold_pct=5.0
    )
    assert created["drift_threshold_pct"] == 5.0


def test_update_saved_pipeline_changes_drift_threshold(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")

    updated = db.update_saved_pipeline(created["id"], "tenant-a", drift_threshold_pct=50.0)

    assert updated is not None
    assert updated["drift_threshold_pct"] == 50.0


# ---------------------------------------------------------------------------
# quality_rules (Sprint 16, ADR-023)
# ---------------------------------------------------------------------------


def test_create_saved_pipeline_defaults_quality_rules_to_empty_list(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    assert created["quality_rules"] == []


def test_create_saved_pipeline_accepts_quality_rules(engine: Engine) -> None:
    rules = [{"column": "amount", "operator": "gte", "value": 0, "severity": "error"}]
    created = db.create_saved_pipeline(
        "tenant-a", "A", "postgres", "spec a", "0 3 * * *", quality_rules=rules
    )
    assert created["quality_rules"] == rules

    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["quality_rules"] == rules


def test_update_saved_pipeline_replaces_quality_rules(engine: Engine) -> None:
    original_rules = [{"column": "amount", "operator": "gte", "value": 0, "severity": "error"}]
    created = db.create_saved_pipeline(
        "tenant-a", "A", "postgres", "spec a", "0 3 * * *", quality_rules=original_rules
    )
    new_rules = [{"column": "customer_id", "operator": "not_null", "severity": "warning"}]

    updated = db.update_saved_pipeline(created["id"], "tenant-a", quality_rules=new_rules)

    assert updated is not None
    assert updated["quality_rules"] == new_rules


def test_update_saved_pipeline_omitting_quality_rules_leaves_them_untouched(
    engine: Engine,
) -> None:
    rules = [{"column": "amount", "operator": "gte", "value": 0}]
    created = db.create_saved_pipeline(
        "tenant-a", "A", "postgres", "spec a", "0 3 * * *", quality_rules=rules
    )

    updated = db.update_saved_pipeline(created["id"], "tenant-a", name="Renamed")

    assert updated is not None
    assert updated["quality_rules"] == rules


def test_update_saved_pipeline_can_clear_quality_rules_with_empty_list(engine: Engine) -> None:
    rules = [{"column": "amount", "operator": "gte", "value": 0}]
    created = db.create_saved_pipeline(
        "tenant-a", "A", "postgres", "spec a", "0 3 * * *", quality_rules=rules
    )

    updated = db.update_saved_pipeline(created["id"], "tenant-a", quality_rules=[])

    assert updated is not None
    assert updated["quality_rules"] == []


def _insert_completed_run(
    engine: Engine,
    run_id: str,
    saved_pipeline_id: str,
    tenant_id: str,
    timestamp: datetime,
    rows_loaded: int,
    cost_usd: float | None = None,
    total_tokens: int | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            runs_table.insert().values(
                run_id=run_id,
                spec="spec",
                status="completed",
                error=None,
                rows_loaded=rows_loaded,
                timestamp=timestamp,
                tenant_id=tenant_id,
                saved_pipeline_id=saved_pipeline_id,
            )
        )
        if cost_usd is not None or total_tokens is not None:
            conn.execute(
                analysis_runs.insert().values(
                    run_id=run_id,
                    gold_subtasks=0,
                    science_subtasks=0,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=total_tokens or 0,
                    timestamp=timestamp,
                    tenant_id=tenant_id,
                    model_name="gpt-4o-mini",
                    cost_usd=cost_usd,
                )
            )


def test_get_previous_completed_run_returns_most_recent_excluding_current(
    engine: Engine,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            users_table.insert().values(id="tenant-a", created_at=datetime.now(tz=timezone.utc))
        )
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    now = datetime.now(tz=timezone.utc)
    _insert_completed_run(
        engine, "run-1", pipeline["id"], "tenant-a", now - timedelta(hours=2), 100, 0.01, 500
    )
    _insert_completed_run(
        engine, "run-2", pipeline["id"], "tenant-a", now - timedelta(hours=1), 150, 0.02, 600
    )
    _insert_completed_run(engine, "run-3", pipeline["id"], "tenant-a", now, 200, 0.03, 700)

    previous = db.get_previous_completed_run(pipeline["id"], exclude_run_id="run-3")

    assert previous is not None
    assert previous["run_id"] == "run-2"
    assert previous["rows_loaded"] == 150
    assert previous["cost_usd"] == 0.02
    assert previous["total_tokens"] == 600


def test_get_previous_completed_run_returns_none_for_first_fire(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            users_table.insert().values(id="tenant-a", created_at=datetime.now(tz=timezone.utc))
        )
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    now = datetime.now(tz=timezone.utc)
    _insert_completed_run(engine, "run-1", pipeline["id"], "tenant-a", now, 100, 0.01, 500)

    previous = db.get_previous_completed_run(pipeline["id"], exclude_run_id="run-1")

    assert previous is None


def test_get_previous_completed_run_ignores_other_pipelines(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            users_table.insert().values(id="tenant-a", created_at=datetime.now(tz=timezone.utc))
        )
    pipeline_a = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    pipeline_b = db.create_saved_pipeline("tenant-a", "B", "postgres", "spec b", "0 3 * * *")
    now = datetime.now(tz=timezone.utc)
    _insert_completed_run(engine, "run-a1", pipeline_a["id"], "tenant-a", now, 100)
    _insert_completed_run(engine, "run-b1", pipeline_b["id"], "tenant-a", now, 999)

    previous = db.get_previous_completed_run(pipeline_a["id"], exclude_run_id="run-does-not-exist")

    assert previous is not None
    assert previous["run_id"] == "run-a1"


def test_get_previous_completed_run_ignores_null_cost_when_no_analysis(engine: Engine) -> None:
    """A Silver-only scheduled run (no business_question) never wrote an
    `analysis_runs` row — cost/tokens must read back as `None`, not `0`."""
    with engine.begin() as conn:
        conn.execute(
            users_table.insert().values(id="tenant-a", created_at=datetime.now(tz=timezone.utc))
        )
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    now = datetime.now(tz=timezone.utc)
    _insert_completed_run(engine, "run-1", pipeline["id"], "tenant-a", now, 100)  # no analysis row

    previous = db.get_previous_completed_run(pipeline["id"], exclude_run_id="run-does-not-exist")

    assert previous is not None
    assert previous["cost_usd"] is None
    assert previous["total_tokens"] is None


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


# ---------------------------------------------------------------------------
# list_pipeline_run_history (Sprint 17, ADR-017)
# ---------------------------------------------------------------------------
#
# `save_run`/`save_analysis` use `postgresql.insert` (ON CONFLICT), which
# doesn't translate to SQLite — so these tests insert directly into
# `runs`/`analysis_runs` via plain, dialect-agnostic `sqlalchemy.insert`,
# the same pattern `tests/unit/test_audit_db.py`'s `load_history` tests use.


def _insert_user(engine: Engine, user_id: str) -> None:
    from ai_etl.audit.models import users

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.insert(users).values(id=user_id, created_at=datetime.now(timezone.utc))
        )


def _insert_run(
    engine: Engine,
    run_id: str,
    tenant_id: str,
    saved_pipeline_id: str | None,
    timestamp: datetime,
    rows_loaded: int | None = 10,
    status: str = "completed",
) -> None:
    from ai_etl.audit.models import runs

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.insert(runs).values(
                run_id=run_id,
                spec="spec",
                status=status,
                error=None,
                rows_loaded=rows_loaded,
                timestamp=timestamp,
                tenant_id=tenant_id,
                saved_pipeline_id=saved_pipeline_id,
            )
        )


def _insert_analysis(
    engine: Engine,
    run_id: str,
    tenant_id: str,
    saved_pipeline_id: str | None,
    cost_usd: float = 0.01,
) -> None:
    from ai_etl.audit.models import analysis_runs

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.insert(analysis_runs).values(
                run_id=run_id,
                gold_subtasks=1,
                science_subtasks=0,
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                timestamp=datetime.now(timezone.utc),
                tenant_id=tenant_id,
                model_name="gpt-4o-mini",
                cost_usd=cost_usd,
                saved_pipeline_id=saved_pipeline_id,
            )
        )


def test_list_pipeline_run_history_scopes_by_pipeline_and_tenant(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    other_pipeline = db.create_saved_pipeline("tenant-a", "B", "postgres", "spec b", "0 4 * * *")

    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    _insert_run(engine, "run-1", "tenant-a", pipeline["id"], t0)
    _insert_run(engine, "run-2", "tenant-a", pipeline["id"], t0 + timedelta(days=1))
    # A different saved pipeline's run must never show up here.
    _insert_run(engine, "run-other", "tenant-a", other_pipeline["id"], t0)
    # An avulso (one-off) run — saved_pipeline_id is NULL — must never show up either.
    _insert_run(engine, "run-avulso", "tenant-a", None, t0)

    history = db.list_pipeline_run_history(pipeline["id"], "tenant-a")

    assert [row["run_id"] for row in history] == ["run-1", "run-2"]  # oldest first


def test_list_pipeline_run_history_returns_empty_for_other_tenant(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    _insert_run(engine, "run-1", "tenant-a", pipeline["id"], datetime.now(timezone.utc))

    # Same pipeline id, wrong tenant — must not leak another tenant's data.
    assert db.list_pipeline_run_history(pipeline["id"], "tenant-b") == []


def test_list_pipeline_run_history_joins_analysis_kpis(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    now = datetime.now(timezone.utc)
    _insert_run(engine, "run-1", "tenant-a", pipeline["id"], now, rows_loaded=250)
    _insert_analysis(engine, "run-1", "tenant-a", pipeline["id"], cost_usd=0.0123)
    # A Silver-only scheduled fire (no business_question) has no analysis_runs row.
    _insert_run(engine, "run-2", "tenant-a", pipeline["id"], now + timedelta(hours=1))

    history = db.list_pipeline_run_history(pipeline["id"], "tenant-a")

    assert history[0]["run_id"] == "run-1"
    assert history[0]["rows_loaded"] == 250
    assert history[0]["cost_usd"] == 0.0123
    assert history[0]["gold_subtasks"] == 1
    assert history[1]["run_id"] == "run-2"
    assert history[1]["cost_usd"] is None
    assert history[1]["gold_subtasks"] is None


def test_list_pipeline_run_history_limit_keeps_most_recent_runs_oldest_first(
    engine: Engine,
) -> None:
    """Sprint 17 code review (PR #64) — a tight-cron pipeline can accumulate
    thousands of runs; `limit` must bound the query to the *most recent* N
    executions (not an arbitrary N from the start of history), while still
    returning them oldest-first — the shape every other caller/the chart
    expects."""
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(5):
        _insert_run(engine, f"run-{i}", "tenant-a", pipeline["id"], t0 + timedelta(days=i))

    history = db.list_pipeline_run_history(pipeline["id"], "tenant-a", limit=2)

    # The 2 most recent runs (run-3, run-4), still oldest-first.
    assert [row["run_id"] for row in history] == ["run-3", "run-4"]


def test_list_pipeline_run_history_default_limit_is_200(engine: Engine) -> None:
    assert db.DEFAULT_PIPELINE_HISTORY_LIMIT == 200


# ---------------------------------------------------------------------------
# record_pipeline_health / get_pipeline_health (Sprint 15, ADR-020)
# ---------------------------------------------------------------------------


def test_record_pipeline_health_resets_consecutive_failures_on_success(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    db.record_pipeline_health(pipeline["id"], "failed", "boom")
    db.record_pipeline_health(pipeline["id"], "failed", "boom again")

    db.record_pipeline_health(pipeline["id"], "completed", None)

    reloaded = db.get_saved_pipeline(pipeline["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["consecutive_failures"] == 0
    assert reloaded["last_status"] == "completed"
    assert reloaded["last_error"] is None


def test_record_pipeline_health_increments_atomically_on_failure(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")

    db.record_pipeline_health(pipeline["id"], "failed", "first error")
    reloaded = db.get_saved_pipeline(pipeline["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["consecutive_failures"] == 1
    assert reloaded["last_status"] == "failed"
    assert reloaded["last_error"] == "first error"

    db.record_pipeline_health(pipeline["id"], "failed", "second error")
    reloaded = db.get_saved_pipeline(pipeline["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["consecutive_failures"] == 2
    assert reloaded["last_error"] == "second error"


def test_new_saved_pipeline_starts_with_zero_consecutive_failures(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    assert created["consecutive_failures"] == 0
    assert created["last_status"] is None
    assert created["last_error"] is None


def test_get_pipeline_health_returns_none_fields_when_never_fired(engine: Engine) -> None:
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")

    health = db.get_pipeline_health(pipeline["id"], "tenant-a")

    assert health == {"success_rate": None, "avg_latency_seconds": None, "sample_size": 0}


def test_get_pipeline_health_computes_success_rate(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    now = datetime.now(timezone.utc)
    _insert_run(engine, "run-1", "tenant-a", pipeline["id"], now, status="completed")
    _insert_run(
        engine, "run-2", "tenant-a", pipeline["id"], now + timedelta(hours=1), status="failed"
    )
    _insert_run(
        engine, "run-3", "tenant-a", pipeline["id"], now + timedelta(hours=2), status="completed"
    )
    _insert_run(
        engine, "run-4", "tenant-a", pipeline["id"], now + timedelta(hours=3), status="completed"
    )

    health = db.get_pipeline_health(pipeline["id"], "tenant-a")

    assert health["success_rate"] == pytest.approx(0.75)
    assert health["sample_size"] == 4


def test_get_pipeline_health_windows_to_most_recent_fires(engine: Engine) -> None:
    """A pipeline with more history than `window` only counts the most
    recent `window` fires — an old streak of failures must not permanently
    drag down a success rate that has since recovered."""
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(3):
        _insert_run(
            engine,
            f"old-fail-{i}",
            "tenant-a",
            pipeline["id"],
            t0 + timedelta(hours=i),
            status="failed",
        )
    for i in range(2):
        _insert_run(
            engine,
            f"recent-ok-{i}",
            "tenant-a",
            pipeline["id"],
            t0 + timedelta(hours=10 + i),
            status="completed",
        )

    health = db.get_pipeline_health(pipeline["id"], "tenant-a", window=2)

    assert health["success_rate"] == 1.0
    assert health["sample_size"] == 2


def test_get_pipeline_health_ignores_other_pipelines_and_tenants(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline_a = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    pipeline_b = db.create_saved_pipeline("tenant-a", "B", "postgres", "spec b", "0 3 * * *")
    now = datetime.now(timezone.utc)
    _insert_run(engine, "run-a1", "tenant-a", pipeline_a["id"], now, status="completed")
    _insert_run(engine, "run-b1", "tenant-a", pipeline_b["id"], now, status="failed")

    health = db.get_pipeline_health(pipeline_a["id"], "tenant-a")

    assert health["success_rate"] == 1.0
    assert health["sample_size"] == 1


def test_get_pipeline_health_averages_silver_stage_latency(engine: Engine) -> None:
    from ai_etl.audit.models import stage_latencies

    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    now = datetime.now(timezone.utc)
    _insert_run(engine, "run-1", "tenant-a", pipeline["id"], now, status="completed")
    _insert_run(
        engine, "run-2", "tenant-a", pipeline["id"], now + timedelta(hours=1), status="completed"
    )

    with engine.begin() as conn:
        # run-1: 2 Silver stages, 10s total. run-2: 1 Silver stage, 20s total.
        # Average across the 2 runs: (10 + 20) / 2 = 15s -- not a per-stage
        # average, a per-run-total average across runs.
        conn.execute(
            sqlalchemy.insert(stage_latencies).values(
                run_id="run-1",
                run_type="silver",
                tenant_id="tenant-a",
                stage="extractor",
                seq=1,
                duration_seconds=4.0,
                timed_out=False,
                recorded_at=now,
            )
        )
        conn.execute(
            sqlalchemy.insert(stage_latencies).values(
                run_id="run-1",
                run_type="silver",
                tenant_id="tenant-a",
                stage="transformer",
                seq=1,
                duration_seconds=6.0,
                timed_out=False,
                recorded_at=now,
            )
        )
        conn.execute(
            sqlalchemy.insert(stage_latencies).values(
                run_id="run-2",
                run_type="silver",
                tenant_id="tenant-a",
                stage="extractor",
                seq=1,
                duration_seconds=20.0,
                timed_out=False,
                recorded_at=now,
            )
        )

    health = db.get_pipeline_health(pipeline["id"], "tenant-a")

    assert health["avg_latency_seconds"] == pytest.approx(15.0)


def test_get_pipeline_health_latency_is_none_without_stage_latencies(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    _insert_run(
        engine, "run-1", "tenant-a", pipeline["id"], datetime.now(timezone.utc), status="completed"
    )

    health = db.get_pipeline_health(pipeline["id"], "tenant-a")

    assert health["avg_latency_seconds"] is None


# ---------------------------------------------------------------------------
# Sprint 27 (ADR-028) — write-approval gate
# ---------------------------------------------------------------------------


def test_create_saved_pipeline_defaults_approval_gate_off(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")

    assert created["require_approval"] is False
    assert created["approval_threshold_rows"] is None
    assert created["last_approved_at"] is None


def test_create_saved_pipeline_with_approval_gate(engine: Engine) -> None:
    created = db.create_saved_pipeline(
        "tenant-a",
        "A",
        "postgres",
        "spec a",
        "0 3 * * *",
        require_approval=True,
        approval_threshold_rows=500,
    )

    assert created["require_approval"] is True
    assert created["approval_threshold_rows"] == 500

    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["require_approval"] is True
    assert reloaded["approval_threshold_rows"] == 500


def test_update_saved_pipeline_can_turn_on_approval_gate(engine: Engine) -> None:
    created = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")

    updated = db.update_saved_pipeline(
        created["id"], "tenant-a", require_approval=True, approval_threshold_rows=100
    )

    assert updated is not None
    assert updated["require_approval"] is True
    assert updated["approval_threshold_rows"] == 100


def test_mark_pipeline_approved_sets_last_approved_at(engine: Engine) -> None:
    created = db.create_saved_pipeline(
        "tenant-a", "A", "postgres", "spec a", "0 3 * * *", require_approval=True
    )
    assert created["last_approved_at"] is None

    db.mark_pipeline_approved(created["id"], "tenant-a")

    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["last_approved_at"] is not None


def test_mark_pipeline_approved_scoped_by_tenant_is_a_noop_for_wrong_tenant(engine: Engine) -> None:
    created = db.create_saved_pipeline(
        "tenant-a", "A", "postgres", "spec a", "0 3 * * *", require_approval=True
    )

    db.mark_pipeline_approved(created["id"], "tenant-b")

    reloaded = db.get_saved_pipeline(created["id"], "tenant-a")
    assert reloaded is not None
    assert reloaded["last_approved_at"] is None


def test_get_run_status_and_pipeline_returns_status_and_link(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    _insert_run(
        engine,
        "run-1",
        "tenant-a",
        pipeline["id"],
        datetime.now(timezone.utc),
        status="awaiting_approval",
    )

    result = db.get_run_status_and_pipeline("run-1", "tenant-a")

    assert result == {"status": "awaiting_approval", "saved_pipeline_id": pipeline["id"]}


def test_get_run_status_and_pipeline_none_for_wrong_tenant(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    _insert_run(engine, "run-1", "tenant-a", None, datetime.now(timezone.utc))

    assert db.get_run_status_and_pipeline("run-1", "tenant-b") is None


def test_list_pending_approvals_scoped_by_tenant_and_status(engine: Engine) -> None:
    _insert_user(engine, "tenant-a")
    _insert_user(engine, "tenant-b")
    pipeline = db.create_saved_pipeline("tenant-a", "A", "postgres", "spec a", "0 3 * * *")
    _insert_run(
        engine,
        "run-1",
        "tenant-a",
        pipeline["id"],
        datetime.now(timezone.utc),
        status="awaiting_approval",
    )
    _insert_run(
        engine, "run-2", "tenant-a", pipeline["id"], datetime.now(timezone.utc), status="completed"
    )
    _insert_run(
        engine, "run-3", "tenant-b", None, datetime.now(timezone.utc), status="awaiting_approval"
    )

    pending = db.list_pending_approvals("tenant-a")

    assert len(pending) == 1
    assert pending[0]["run_id"] == "run-1"
    assert pending[0]["pipeline_name"] == "A"
