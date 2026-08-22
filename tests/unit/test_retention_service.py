"""Unit tests for `services/retention_service.py` (Sprint 36, ADR-035).

Same convention as `test_tenant_deletion_service.py`: a real in-memory
SQLite engine, a fake in-memory `StorageBackend`, and monkeypatched
`get_engine`/`get_storage_backend` at the module's own import site.
"""

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy
from sqlalchemy import Engine

from ai_etl.audit.db import retention as retention_db
from ai_etl.audit.models import analysis_runs as analysis_runs_table
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import retention_cleanup_log
from ai_etl.audit.models import runs as runs_table
from ai_etl.audit.models import users as users_table
from ai_etl.services import retention_service as svc


class FakeStorageBackend:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def write_bytes(self, key: str, data: bytes) -> None:
        self._objects[key] = data

    def read_bytes(self, key: str) -> bytes:
        return self._objects[key]

    def exists(self, key: str) -> bool:
        return key in self._objects

    def delete_bytes(self, key: str) -> None:
        self._objects.pop(key, None)


def _make_sqlite_engine() -> Engine:
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    audit_metadata.create_all(engine)
    return engine


@pytest.fixture
def fake_storage() -> FakeStorageBackend:
    return FakeStorageBackend()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, fake_storage: FakeStorageBackend) -> Engine:
    eng = _make_sqlite_engine()
    monkeypatch.setattr(svc, "get_engine", lambda: eng)
    monkeypatch.setattr(retention_db, "get_engine", lambda: eng)
    monkeypatch.setattr(svc, "get_storage_backend", lambda log_dir, tenant_id: fake_storage)

    now = datetime.now(tz=timezone.utc)
    old = now - timedelta(days=100)
    recent = now - timedelta(days=1)

    with eng.begin() as conn:
        conn.execute(
            sqlalchemy.insert(users_table).values(id="tenant-a", created_at=now, retention_days=30)
        )
        conn.execute(sqlalchemy.insert(users_table).values(id="tenant-b", created_at=now))
        # Expired run for tenant-a (100 days old, retention window 30 days).
        conn.execute(
            sqlalchemy.insert(runs_table).values(
                run_id="run-old",
                spec="spec",
                status="completed",
                rows_loaded=10,
                timestamp=old,
                tenant_id="tenant-a",
            )
        )
        # Recent run for tenant-a (1 day old) — must survive the sweep.
        conn.execute(
            sqlalchemy.insert(runs_table).values(
                run_id="run-recent",
                spec="spec",
                status="completed",
                rows_loaded=10,
                timestamp=recent,
                tenant_id="tenant-a",
            )
        )
        conn.execute(
            sqlalchemy.insert(analysis_runs_table).values(
                run_id="run-old",
                gold_subtasks=1,
                science_subtasks=0,
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                timestamp=old,
                tenant_id="tenant-a",
            )
        )

    fake_storage.write_bytes("run-old.json", b"{}")
    fake_storage.write_bytes("run-old_silver.csv", b"a,b\n1,2\n")
    fake_storage.write_bytes("run-old_analysis.json", b"{}")
    fake_storage.write_bytes("run-old_gold_0.csv", b"x\n1\n")
    fake_storage.write_bytes("run-recent.json", b"{}")
    fake_storage.write_bytes("run-recent_silver.csv", b"a,b\n1,2\n")
    return eng


def test_deletes_only_expired_run_artifacts(
    engine: Engine, fake_storage: FakeStorageBackend
) -> None:
    summary = svc.cleanup_expired_retention_for_tenant("tenant-a", 30)

    assert summary["status"] == "completed"
    # run-old.json, _silver.csv, _analysis.json, _gold_0.csv = 4
    assert summary["storage_keys_deleted"] == 4
    assert fake_storage.exists("run-old.json") is False
    assert fake_storage.exists("run-old_gold_0.csv") is False


def test_does_not_touch_artifacts_within_the_retention_window(
    engine: Engine, fake_storage: FakeStorageBackend
) -> None:
    svc.cleanup_expired_retention_for_tenant("tenant-a", 30)

    assert fake_storage.exists("run-recent.json") is True
    assert fake_storage.exists("run-recent_silver.csv") is True


def test_does_not_delete_the_run_db_row(engine: Engine) -> None:
    svc.cleanup_expired_retention_for_tenant("tenant-a", 30)

    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.select(runs_table).where(runs_table.c.run_id == "run-old")
        ).first()

    assert row is not None


def test_writes_a_cleanup_log_row(engine: Engine) -> None:
    svc.cleanup_expired_retention_for_tenant("tenant-a", 30)

    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.select(retention_cleanup_log).where(
                retention_cleanup_log.c.tenant_id == "tenant-a"
            )
        ).first()

    assert row is not None
    assert row.status == "completed"
    assert row.storage_keys_deleted == 4
    assert row.runs_scanned == 1
    assert row.retention_days == 30


def test_cleanup_task_processes_only_tenants_with_a_retention_policy(
    engine: Engine, fake_storage: FakeStorageBackend
) -> None:
    result = svc.cleanup_expired_retention_task()

    assert result["processed"] == ["tenant-a"]
    assert result["skipped"] == []
    assert result["total_storage_keys_deleted"] == 4


def test_cleanup_task_is_a_noop_when_no_tenant_has_a_retention_policy(
    monkeypatch: pytest.MonkeyPatch, fake_storage: FakeStorageBackend
) -> None:
    eng = _make_sqlite_engine()
    monkeypatch.setattr(svc, "get_engine", lambda: eng)
    monkeypatch.setattr(retention_db, "get_engine", lambda: eng)
    monkeypatch.setattr(svc, "get_storage_backend", lambda log_dir, tenant_id: fake_storage)
    with eng.begin() as conn:
        conn.execute(
            sqlalchemy.insert(users_table).values(
                id="tenant-a", created_at=datetime.now(tz=timezone.utc)
            )
        )

    result = svc.cleanup_expired_retention_task()

    assert result == {"processed": [], "skipped": [], "total_storage_keys_deleted": 0}


def test_cleanup_task_skips_a_tenant_whose_sweep_raises_without_stopping_the_tick(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(tenant_id: str, retention_days: int, **kwargs: object) -> None:
        raise RuntimeError("storage backend unreachable")

    monkeypatch.setattr(svc, "cleanup_expired_retention_for_tenant", _boom)

    result = svc.cleanup_expired_retention_task()

    assert result["processed"] == []
    assert result["skipped"] == ["tenant-a"]
