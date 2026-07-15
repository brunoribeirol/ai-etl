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
    monkeypatch.setenv("APP_DATABASE_URL", _TEST_APP_DATABASE_URL)
    engine = sqlalchemy.create_engine(_TEST_APP_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS analysis_runs"))
        conn.execute(text("DROP TABLE IF EXISTS runs"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS analysis_runs"))
        conn.execute(text("DROP TABLE IF EXISTS runs"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
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

    assert run_columns == {"run_id", "spec", "status", "error", "rows_loaded", "timestamp"}
    assert analysis_columns == {
        "run_id",
        "gold_subtasks",
        "science_subtasks",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "timestamp",
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
