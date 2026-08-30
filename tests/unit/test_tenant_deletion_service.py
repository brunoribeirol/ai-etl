"""Unit tests for `services/tenant_deletion_service.py` (Sprint 24, ADR-025).

Same convention as `test_secrets_service.py`: a real in-memory SQLite engine
(not a mocked `get_engine`) so the actual insert/select/delete statements
execute for real, plus a fake in-memory `StorageBackend` (no real S3/local
disk needed) so storage-artifact deletion is exercised for real too.

ADR-040 follow-up: tenant-scoped reads/writes now go through `tenant_scope()`
(faked here the same way `test_audit_db.py` established); `tenant_deletion_log`
writes stay on `get_engine()` (bypass role, RLS-enabled-with-no-policy table,
per ADR-040's documented exemption) — both are faked onto the same SQLite
engine here since this test has no real Postgres role split to exercise.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import pytest
import sqlalchemy
from sqlalchemy import Connection, Engine

from ai_etl.audit.models import analysis_runs as analysis_runs_table
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import runs as runs_table
from ai_etl.audit.models import saved_pipelines as saved_pipelines_table
from ai_etl.audit.models import stage_latencies as stage_latencies_table
from ai_etl.audit.models import tenant_deletion_log
from ai_etl.audit.models import tenant_secrets as tenant_secrets_table
from ai_etl.audit.models import users as users_table
from ai_etl.services import tenant_deletion_service as svc


class FakeStorageBackend:
    """In-memory `StorageBackend` — records writes/deletes for assertions."""

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


def _fake_scope(engine: Engine):  # noqa: ANN201
    @contextmanager
    def _scope(tenant_id: str) -> Iterator[Connection]:
        with engine.begin() as conn:
            yield conn

    return _scope


@pytest.fixture
def fake_storage() -> FakeStorageBackend:
    return FakeStorageBackend()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, fake_storage: FakeStorageBackend) -> Engine:
    eng = _make_sqlite_engine()
    monkeypatch.setattr(svc, "get_engine", lambda: eng)
    monkeypatch.setattr(svc, "tenant_scope", _fake_scope(eng))
    monkeypatch.setattr(svc, "get_storage_backend", lambda log_dir, tenant_id: fake_storage)

    now = datetime.now(tz=timezone.utc)
    with eng.begin() as conn:
        conn.execute(sqlalchemy.insert(users_table).values(id="tenant-a", created_at=now))
        conn.execute(sqlalchemy.insert(users_table).values(id="tenant-b", created_at=now))
        conn.execute(
            sqlalchemy.insert(runs_table).values(
                run_id="run-1",
                spec="spec",
                status="completed",
                rows_loaded=10,
                timestamp=now,
                tenant_id="tenant-a",
            )
        )
        conn.execute(
            sqlalchemy.insert(runs_table).values(
                run_id="run-other-tenant",
                spec="spec",
                status="completed",
                rows_loaded=10,
                timestamp=now,
                tenant_id="tenant-b",
            )
        )
        conn.execute(
            sqlalchemy.insert(analysis_runs_table).values(
                run_id="run-1",
                gold_subtasks=2,
                science_subtasks=1,
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                timestamp=now,
                tenant_id="tenant-a",
            )
        )
        conn.execute(
            sqlalchemy.insert(stage_latencies_table).values(
                run_id="run-1",
                run_type="silver",
                tenant_id="tenant-a",
                stage="extractor",
                duration_seconds=1.0,
                recorded_at=now,
            )
        )
        conn.execute(
            sqlalchemy.insert(saved_pipelines_table).values(
                id="pipeline-1",
                tenant_id="tenant-a",
                name="Daily",
                source_type="postgres",
                spec="spec",
                cron_schedule="0 3 * * *",
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            sqlalchemy.insert(tenant_secrets_table).values(
                id="secret-1",
                tenant_id="tenant-a",
                name="api_key",
                ciphertext="cipher",
                created_at=now,
                updated_at=now,
            )
        )

    # Storage artifacts a real save_run/save_analysis for run-1 would have
    # written, per the naming convention in audit/db.py.
    fake_storage.write_bytes("run-1.json", b"{}")
    fake_storage.write_bytes("run-1_silver.csv", b"a,b\n1,2\n")
    fake_storage.write_bytes("run-1_analysis.json", b"{}")
    fake_storage.write_bytes("run-1_gold_0.csv", b"x\n1\n")
    fake_storage.write_bytes("run-1_gold_1.csv", b"x\n1\n")
    fake_storage.write_bytes("run-1_science_0.csv", b"x\n1\n")
    return eng


def test_deletes_all_tenant_rows(engine: Engine) -> None:
    summary = svc.delete_tenant_data("tenant-a", requested_by="tenant-a")

    assert summary["status"] == "completed"
    assert summary["runs_deleted"] == 1
    assert summary["analysis_runs_deleted"] == 1
    assert summary["stage_latencies_deleted"] == 1
    assert summary["saved_pipelines_deleted"] == 1
    assert summary["secrets_deleted"] == 1

    with engine.connect() as conn:
        assert (
            conn.execute(
                sqlalchemy.select(users_table).where(users_table.c.id == "tenant-a")
            ).first()
            is None
        )
        assert (
            conn.execute(
                sqlalchemy.select(runs_table).where(runs_table.c.tenant_id == "tenant-a")
            ).first()
            is None
        )


def test_does_not_touch_another_tenants_data(engine: Engine) -> None:
    svc.delete_tenant_data("tenant-a", requested_by="tenant-a")

    with engine.connect() as conn:
        other = conn.execute(
            sqlalchemy.select(users_table).where(users_table.c.id == "tenant-b")
        ).first()
        other_run = conn.execute(
            sqlalchemy.select(runs_table).where(runs_table.c.tenant_id == "tenant-b")
        ).first()
    assert other is not None
    assert other_run is not None


def test_deletes_storage_artifacts(engine: Engine, fake_storage: FakeStorageBackend) -> None:
    summary = svc.delete_tenant_data("tenant-a", requested_by="tenant-a")

    # run-1.json, _silver.csv, _analysis.json, 2 gold csv, 1 science csv = 6
    assert summary["storage_keys_deleted"] == 6
    assert fake_storage.exists("run-1.json") is False
    assert fake_storage.exists("run-1_silver.csv") is False
    assert fake_storage.exists("run-1_gold_0.csv") is False


def test_raises_for_unknown_tenant(engine: Engine) -> None:
    with pytest.raises(svc.TenantNotFoundError):
        svc.delete_tenant_data("does-not-exist", requested_by="does-not-exist")


def test_writes_a_deletion_log_row(engine: Engine) -> None:
    svc.delete_tenant_data("tenant-a", requested_by="tenant-a")

    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.select(tenant_deletion_log).where(
                tenant_deletion_log.c.tenant_id == "tenant-a"
            )
        ).first()

    assert row is not None
    assert row.status == "completed"
    assert row.requested_by == "tenant-a"
    assert row.completed_at is not None
    assert row.runs_deleted == 1


def test_deletion_log_survives_after_users_row_is_gone(engine: Engine) -> None:
    """The whole point of a separate log table (ADR-025 Decision 2): it must
    still be readable after the tenant it describes no longer exists."""
    svc.delete_tenant_data("tenant-a", requested_by="tenant-a")

    with engine.connect() as conn:
        user = conn.execute(
            sqlalchemy.select(users_table).where(users_table.c.id == "tenant-a")
        ).first()
        log_row = conn.execute(
            sqlalchemy.select(tenant_deletion_log).where(
                tenant_deletion_log.c.tenant_id == "tenant-a"
            )
        ).first()

    assert user is None
    assert log_row is not None
