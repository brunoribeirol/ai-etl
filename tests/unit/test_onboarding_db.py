"""Unit tests for `audit/db.py::get_onboarding_status` (Sprint 26, ADR-027).

Same in-memory SQLite pattern as `test_saved_pipelines_db.py`/
`test_audit_db.py`'s `load_history` tests — plain `select(func.count())`
statements, no dialect-specific SQL, so the real query executes against a
throwaway SQLite engine instead of being mocked.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import sqlalchemy
from sqlalchemy import Engine

from ai_etl.audit import db
from ai_etl.audit.db import onboarding as onboarding_db
from ai_etl.audit.db import pipelines as pipelines_db
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import runs as runs_table
from ai_etl.audit.models import saved_pipelines as saved_pipelines_table


def _make_sqlite_engine() -> Engine:
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    audit_metadata.create_all(engine)
    return engine


def _fake_scope(engine: Engine):
    """Stand-in for `tenant_scope` in these unit tests (ADR-040) — see
    `test_saved_pipelines_db.py`'s identical helper for the full rationale."""

    @contextmanager
    def _scope(tenant_id):
        with engine.begin() as conn:
            yield conn

    return _scope


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = _make_sqlite_engine()
    monkeypatch.setattr(onboarding_db, "tenant_scope", _fake_scope(eng))
    monkeypatch.setattr(pipelines_db, "get_engine", lambda: eng)
    monkeypatch.setattr(pipelines_db, "tenant_scope", _fake_scope(eng))
    return eng


def _insert_run(engine: Engine, run_id: str, tenant_id: str, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            runs_table.insert().values(
                run_id=run_id,
                spec="load sales.csv",
                status=status,
                error=None,
                rows_loaded=1,
                timestamp=datetime.now(tz=timezone.utc),
                tenant_id=tenant_id,
            )
        )


def test_new_tenant_has_no_activation_yet(engine: Engine) -> None:
    status = db.get_onboarding_status("brand-new-tenant")

    assert status == {
        "run_count": 0,
        "completed_run_count": 0,
        "has_completed_run": False,
        "saved_pipeline_count": 0,
        "has_saved_pipeline": False,
        "has_pipeline_run": False,
    }


def test_a_completed_run_activates_the_first_checklist_item(engine: Engine) -> None:
    _insert_run(engine, "run-1", "tenant-a", status="completed")

    status = db.get_onboarding_status("tenant-a")

    assert status["run_count"] == 1
    assert status["completed_run_count"] == 1
    assert status["has_completed_run"] is True
    assert status["has_saved_pipeline"] is False


def test_a_failed_run_counts_toward_run_count_but_not_completion(engine: Engine) -> None:
    _insert_run(engine, "run-1", "tenant-a", status="failed")

    status = db.get_onboarding_status("tenant-a")

    assert status["run_count"] == 1
    assert status["completed_run_count"] == 0
    assert status["has_completed_run"] is False


def test_runs_are_scoped_by_tenant(engine: Engine) -> None:
    _insert_run(engine, "run-1", "tenant-a", status="completed")
    _insert_run(engine, "run-2", "tenant-b", status="completed")

    status_a = db.get_onboarding_status("tenant-a")
    status_b = db.get_onboarding_status("tenant-other")

    assert status_a["has_completed_run"] is True
    assert status_b["has_completed_run"] is False
    assert status_b["run_count"] == 0


def test_a_saved_pipeline_activates_the_second_checklist_item(engine: Engine) -> None:
    db.create_saved_pipeline(
        tenant_id="tenant-a",
        name="Nightly sync",
        source_type="postgres",
        spec="Read schema.orders from postgres",
        cron_schedule="0 3 * * *",
    )

    status = db.get_onboarding_status("tenant-a")

    assert status["saved_pipeline_count"] == 1
    assert status["has_saved_pipeline"] is True


def test_a_saved_pipeline_that_has_run_activates_the_third_checklist_item(engine: Engine) -> None:
    # The third step ("see the executive summary") is available only once a scheduled
    # pipeline has run; before 2026-10-16 it was ticked by `has_saved_pipeline` alone.
    created = db.create_saved_pipeline(
        tenant_id="tenant-a",
        name="Nightly sync",
        source_type="postgres",
        spec="Read schema.orders from postgres",
        cron_schedule="0 3 * * *",
    )
    assert db.get_onboarding_status("tenant-a")["has_pipeline_run"] is False

    with engine.begin() as conn:
        conn.execute(
            saved_pipelines_table.update()
            .where(saved_pipelines_table.c.id == created["id"])
            .values(last_run_at=datetime.now(tz=timezone.utc))
        )

    status = db.get_onboarding_status("tenant-a")
    assert status["has_saved_pipeline"] is True
    assert status["has_pipeline_run"] is True
    assert db.get_onboarding_status("tenant-other")["has_pipeline_run"] is False
