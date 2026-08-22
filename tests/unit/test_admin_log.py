"""Unit tests for `audit/admin_log.py` (Sprint 31, ADR-032).

Same convention as `test_tenant_deletion_service.py`: a real in-memory
SQLite engine (not a mocked `get_engine`) so the actual insert/select
statements execute for real.
"""

import pytest
import sqlalchemy
from sqlalchemy import Engine

from ai_etl.audit import admin_log
from ai_etl.audit.models import metadata as audit_metadata


def _make_sqlite_engine() -> Engine:
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    audit_metadata.create_all(engine)
    return engine


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    eng = _make_sqlite_engine()
    monkeypatch.setattr(admin_log, "get_engine", lambda: eng)
    return eng


def test_log_admin_action_persists_row(engine: Engine) -> None:
    admin_log.log_admin_action(
        "user_admin1",
        "list_tenant_runs",
        target_tenant_id="tenant-a",
        detail="limit=20",
    )

    records = admin_log.list_admin_actions()
    assert len(records) == 1
    assert records[0]["actor_user_id"] == "user_admin1"
    assert records[0]["action"] == "list_tenant_runs"
    assert records[0]["target_tenant_id"] == "tenant-a"
    assert records[0]["detail"] == "limit=20"
    assert records[0]["created_at"] is not None


def test_log_admin_action_target_tenant_id_optional(engine: Engine) -> None:
    """Some admin actions (e.g. listing all tenants) have no single target."""
    admin_log.log_admin_action("user_admin1", "list_all_tenants")

    records = admin_log.list_admin_actions()
    assert len(records) == 1
    assert records[0]["target_tenant_id"] is None
    assert records[0]["detail"] is None


def test_list_admin_actions_orders_most_recent_first(engine: Engine) -> None:
    admin_log.log_admin_action("user_admin1", "action_1", target_tenant_id="tenant-a")
    admin_log.log_admin_action("user_admin1", "action_2", target_tenant_id="tenant-a")
    admin_log.log_admin_action("user_admin1", "action_3", target_tenant_id="tenant-a")

    records = admin_log.list_admin_actions()
    assert [r["action"] for r in records] == ["action_3", "action_2", "action_1"]


def test_list_admin_actions_filters_by_actor(engine: Engine) -> None:
    admin_log.log_admin_action("user_admin1", "action_a", target_tenant_id="tenant-a")
    admin_log.log_admin_action("user_admin2", "action_b", target_tenant_id="tenant-a")

    records = admin_log.list_admin_actions(actor_user_id="user_admin1")
    assert len(records) == 1
    assert records[0]["actor_user_id"] == "user_admin1"


def test_list_admin_actions_filters_by_target_tenant(engine: Engine) -> None:
    admin_log.log_admin_action("user_admin1", "action_a", target_tenant_id="tenant-a")
    admin_log.log_admin_action("user_admin1", "action_b", target_tenant_id="tenant-b")

    records = admin_log.list_admin_actions(target_tenant_id="tenant-b")
    assert len(records) == 1
    assert records[0]["target_tenant_id"] == "tenant-b"


def test_list_admin_actions_respects_limit(engine: Engine) -> None:
    for i in range(5):
        admin_log.log_admin_action("user_admin1", f"action_{i}", target_tenant_id="tenant-a")

    records = admin_log.list_admin_actions(limit=2)
    assert len(records) == 2


def test_list_admin_actions_empty_when_no_actions(engine: Engine) -> None:
    assert admin_log.list_admin_actions() == []
