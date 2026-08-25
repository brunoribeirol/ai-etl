"""Integration test: applying Alembic migrations from zero creates the expected schema.

Skipped when `app-postgres-test` isn't reachable (see test_audit_persistence.py for
the same pattern/rationale).
"""

import os
from pathlib import Path

import pytest
import sqlalchemy
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command

_TEST_APP_DATABASE_URL = os.getenv(
    "TEST_APP_DATABASE_URL",
    "postgresql://ai_etl_app_test:ai_etl_app_test@localhost:5435/ai_etl_app_test_db",
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch: pytest.MonkeyPatch):
    """Drops the entire `public` schema (not an enumerated table list) before and
    after every test.

    An enumerated `DROP TABLE IF EXISTS runs/analysis_runs/alembic_version` was the
    original approach here, but it predates migrations 0003-0020 (users,
    stage_latencies, saved_pipelines, tenant_secrets, tenant_deletion_log,
    admin_action_log, retention_cleanup_log) and silently stopped covering the full
    table set those migrations create. That let one test's `upgrade(head)` leave
    e.g. `users` behind after its own (incomplete) teardown, so the next test's
    `upgrade(head)` — starting from a dropped `alembic_version` but a still-present
    `users` table — failed with `DuplicateTable: relation "users" already exists`.
    `DROP SCHEMA ... CASCADE` + recreate is the actual fix: it stays correct
    regardless of which/how many tables a future migration adds, with no list to
    keep in sync here.
    """
    monkeypatch.setenv("APP_DATABASE_URL", _TEST_APP_DATABASE_URL)
    engine = sqlalchemy.create_engine(_TEST_APP_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def test_upgrade_head_creates_expected_tables() -> None:
    command.upgrade(_alembic_config(), "head")

    engine = sqlalchemy.create_engine(_TEST_APP_DATABASE_URL)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    engine.dispose()

    assert {"runs", "analysis_runs"} <= tables


def test_upgrade_head_creates_expected_columns() -> None:
    command.upgrade(_alembic_config(), "head")

    engine = sqlalchemy.create_engine(_TEST_APP_DATABASE_URL)
    inspector = inspect(engine)
    run_columns = {c["name"] for c in inspector.get_columns("runs")}
    analysis_columns = {c["name"] for c in inspector.get_columns("analysis_runs")}
    engine.dispose()

    # Ground truth as of migration 0020 (introspected against a real Postgres
    # after running the full chain, not just copied from models.py — see
    # ai_etl.audit.models.runs/analysis_runs for what added each column):
    # 0002 tenant_id, 0003 required tenant_id FK, 0007 saved_pipeline_id.
    assert run_columns == {
        "run_id",
        "spec",
        "status",
        "error",
        "rows_loaded",
        "timestamp",
        "tenant_id",
        "saved_pipeline_id",
    }
    assert analysis_columns == {
        "run_id",
        "gold_subtasks",
        "science_subtasks",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "timestamp",
        "tenant_id",
        "model_name",
        "cost_usd",
        "saved_pipeline_id",
    }


def test_downgrade_base_drops_tables() -> None:
    command.upgrade(_alembic_config(), "head")
    command.downgrade(_alembic_config(), "base")

    engine = sqlalchemy.create_engine(_TEST_APP_DATABASE_URL)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    engine.dispose()

    assert "runs" not in tables
    assert "analysis_runs" not in tables
