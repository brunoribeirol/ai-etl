"""Unit tests for `audit/db/tenants.py::list_all_tenants` (Wave 6, 2026-08-25).

Previously untested at the DB layer (2026-09-04 coverage gap-closing pass) —
`test_api_admin.py`/`test_admin_log.py` only ever mock this function out at the
router boundary, so the actual `select`/ordering never ran for real. Same
in-memory-SQLite style as `test_saved_pipelines_db.py`.
"""

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy
from sqlalchemy import Engine

from ai_etl.audit.db import tenants as tenants_db
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import users as users_table


def _make_sqlite_engine() -> Engine:
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    audit_metadata.create_all(engine)
    return engine


def _insert_user_row(engine: Engine, user_id: str, created_at: datetime) -> None:
    with engine.begin() as conn:
        conn.execute(users_table.insert().values(id=user_id, created_at=created_at))


def test_list_all_tenants_returns_empty_list_with_no_users(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _make_sqlite_engine()
    monkeypatch.setattr(tenants_db, "get_engine", lambda: engine)

    assert tenants_db.list_all_tenants() == []


def test_list_all_tenants_returns_every_tenant_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_sqlite_engine()
    monkeypatch.setattr(tenants_db, "get_engine", lambda: engine)
    now = datetime.now(tz=timezone.utc)
    # Inserted newest-first, on purpose, to prove the query does the ordering
    # rather than happening to return insertion order.
    _insert_user_row(engine, "tenant-newest", now)
    _insert_user_row(engine, "tenant-oldest", now - timedelta(days=10))
    _insert_user_row(engine, "tenant-middle", now - timedelta(days=5))

    result = tenants_db.list_all_tenants()

    assert [t["tenant_id"] for t in result] == ["tenant-oldest", "tenant-middle", "tenant-newest"]


def test_list_all_tenants_includes_created_at(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _make_sqlite_engine()
    monkeypatch.setattr(tenants_db, "get_engine", lambda: engine)
    created_at = datetime.now(tz=timezone.utc)
    _insert_user_row(engine, "tenant-a", created_at)

    result = tenants_db.list_all_tenants()

    assert len(result) == 1
    assert result[0]["tenant_id"] == "tenant-a"
    assert result[0]["created_at"] is not None
