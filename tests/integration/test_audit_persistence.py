"""Integration tests for the application-database persistence layer.

Exercises `audit/db.py` and `audit/connection.py` against a live Postgres —
`app-postgres-test` in docker-compose.yml (`make app-db-test-up`). Skipped
automatically when that database isn't reachable, so `make test`/CI keep
passing on machines without Docker, matching how the rest of this test suite
treats live Postgres (no integration test in this repo depended on a running
container before this one).
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy
from alembic.config import Config
from sqlalchemy import text

from ai_etl.audit import connection, db
from ai_etl.audit.db import ensure_user
from ai_etl.audit.models import analysis_runs, runs
from ai_etl.core.state import initial_state
from alembic import command

_TEST_APP_DATABASE_URL = os.getenv(
    "TEST_APP_DATABASE_URL",
    "postgresql://ai_etl_app_test:ai_etl_app_test@localhost:5435/ai_etl_app_test_db",
)
# ADR-040 — the restricted, non-bypass role migration 0021 creates, same
# database as above. `tenant_scope()` (used by every `audit/db/*.py` function
# this file exercises) needs this set, same as `APP_DATABASE_URL_TENANT` in
# `.env.example`.
_TEST_APP_DATABASE_URL_TENANT = os.getenv(
    "TEST_APP_DATABASE_URL_TENANT",
    "postgresql://ai_etl_app_tenant:ai_etl_app_tenant@localhost:5435/ai_etl_app_test_db",
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


def _database_reachable() -> bool:
    try:
        engine = sqlalchemy.create_engine(_TEST_APP_DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_reachable(),
    reason="app-postgres-test not reachable; run `make app-db-test-up` to enable this test",
)


@pytest.fixture(autouse=True)
def _app_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_DATABASE_URL", _TEST_APP_DATABASE_URL)
    monkeypatch.setenv("APP_DATABASE_URL_TENANT", _TEST_APP_DATABASE_URL_TENANT)
    connection.get_engine.cache_clear()
    connection.get_tenant_engine.cache_clear()
    engine = connection.get_engine()
    # ADR-040: `command.upgrade` (not `metadata.create_all`) — this file's functions
    # now go through the restricted role + RLS policies migration 0021 creates,
    # which a bare `create_all()` would never set up.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(_alembic_config(), "head")
    yield
    with engine.begin() as conn:
        conn.execute(analysis_runs.delete())
        conn.execute(runs.delete())
    connection.get_engine.cache_clear()
    connection.get_tenant_engine.cache_clear()


def _run_id() -> str:
    return f"test-{uuid.uuid4()}"


def _tenant_id() -> str:
    """A real, valid `users.id` — migration 0003 (ADR-006) made
    `runs.tenant_id`/`analysis_runs.tenant_id` `NOT NULL` foreign keys to `users.id`,
    so every `save_run`/`save_analysis` call in this file must reference one that
    actually exists first. Same `ensure_user` upsert `tests/e2e/conftest.py`'s
    `test_tenant` fixture uses for the same reason.
    """
    tenant_id = f"test-tenant-{uuid.uuid4()}"
    ensure_user(tenant_id)
    return tenant_id


def test_save_run_inserts_row_readable_via_load_history(tmp_path) -> None:
    run_id = _run_id()
    tenant_id = _tenant_id()
    state = initial_state(spec="load sales.csv", run_id=run_id)
    state = {
        **state,
        "status": "completed",
        "load_result": {"rows_loaded": 42, "destination": "out.csv"},
    }

    db.save_run(state, log_dir=str(tmp_path), tenant_id=tenant_id)

    history = db.load_history(limit=50)
    row = history[history["run_id"] == run_id].iloc[0]
    assert row["status"] == "completed"
    assert row["rows_loaded"] == 42


def test_save_run_upserts_on_conflict(tmp_path) -> None:
    run_id = _run_id()
    tenant_id = _tenant_id()
    state = initial_state(spec="load sales.csv", run_id=run_id)
    state = {**state, "status": "running", "load_result": None}
    db.save_run(state, log_dir=str(tmp_path), tenant_id=tenant_id)

    updated_state = {
        **state,
        "status": "completed",
        "load_result": {"rows_loaded": 7, "destination": "out.csv"},
    }
    db.save_run(updated_state, log_dir=str(tmp_path), tenant_id=tenant_id)

    with connection.get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT status, rows_loaded FROM runs WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).fetchall()

    assert len(rows) == 1
    assert rows[0].status == "completed"
    assert rows[0].rows_loaded == 7


def test_save_analysis_aggregates_tokens_into_row(tmp_path) -> None:
    run_id = _run_id()
    gold = {
        "task_question": "q1",
        "gold_df": None,
        "narrative": "n",
        "code": "x",
        "attempts": 1,
        "error": None,
        "tokens": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }
    advisor = {
        "recommendations": [],
        "summary": "s",
        "error": None,
        "tokens": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    planner_tokens = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    tenant_id = _tenant_id()

    db.save_analysis(
        run_id, [gold], [], advisor, planner_tokens, log_dir=str(tmp_path), tenant_id=tenant_id
    )

    with connection.get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT gold_subtasks, science_subtasks, total_tokens "
                "FROM analysis_runs WHERE run_id = :run_id"
            ),
            {"run_id": run_id},
        ).fetchone()

    assert row is not None
    assert row.gold_subtasks == 1
    assert row.science_subtasks == 0
    assert row.total_tokens == 150 + 15 + 2


def test_load_history_orders_most_recent_first(tmp_path) -> None:
    run_id_1, run_id_2 = _run_id(), _run_id()
    tenant_id = _tenant_id()
    for rid in (run_id_1, run_id_2):
        state = initial_state(spec="s", run_id=rid)
        db.save_run(
            {**state, "status": "completed", "load_result": None},
            log_dir=str(tmp_path),
            tenant_id=tenant_id,
        )

    history = db.load_history(limit=50)
    ids = list(history["run_id"])
    assert ids.index(run_id_2) < ids.index(run_id_1)


def test_load_history_returns_empty_frame_when_database_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://nouser:nopass@localhost:1/nodb")
    connection.get_engine.cache_clear()
    try:
        result = db.load_history()
        assert result.empty
    finally:
        connection.get_engine.cache_clear()
