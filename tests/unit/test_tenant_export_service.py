"""Unit tests for `services/tenant_export_service.py` (Sprint 36, ADR-035).

Same convention as `test_tenant_deletion_service.py`: a real in-memory
SQLite engine (not a mocked `get_engine`) so the actual select statements
execute for real, plus a fake in-memory `StorageBackend`.
"""

from datetime import datetime, timezone

import pytest
import sqlalchemy
from sqlalchemy import Engine

from ai_etl.audit.models import analysis_runs as analysis_runs_table
from ai_etl.audit.models import metadata as audit_metadata
from ai_etl.audit.models import runs as runs_table
from ai_etl.audit.models import saved_pipelines as saved_pipelines_table
from ai_etl.audit.models import stage_latencies as stage_latencies_table
from ai_etl.audit.models import tenant_secrets as tenant_secrets_table
from ai_etl.audit.models import users as users_table
from ai_etl.services import tenant_export_service as svc
from ai_etl.services.tenant_deletion_service import TenantNotFoundError


class FakeStorageBackend:
    """In-memory `StorageBackend` — same fake used by
    `test_tenant_deletion_service.py`."""

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
    monkeypatch.setattr(svc, "get_storage_backend", lambda log_dir, tenant_id: fake_storage)

    now = datetime.now(tz=timezone.utc)
    with eng.begin() as conn:
        conn.execute(
            sqlalchemy.insert(users_table).values(
                id="tenant-a", created_at=now, monthly_budget_usd=100.0
            )
        )
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
                gold_subtasks=1,
                science_subtasks=0,
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
                ciphertext="super-secret-cipher",
                created_at=now,
                updated_at=now,
            )
        )

    fake_storage.write_bytes("run-1.json", b"{}")
    fake_storage.write_bytes("run-1_silver.csv", b"a,b\n1,2\n")
    fake_storage.write_bytes("run-1_analysis.json", b"{}")
    fake_storage.write_bytes("run-1_gold_0.csv", b"x\n1\n")
    return eng


def test_export_includes_all_own_tenant_rows(engine: Engine) -> None:
    export = svc.export_tenant_data("tenant-a")

    assert export["tenant_id"] == "tenant-a"
    assert len(export["runs"]) == 1
    assert export["runs"][0]["run_id"] == "run-1"
    assert len(export["analysis_runs"]) == 1
    assert len(export["stage_latencies"]) == 1
    assert len(export["saved_pipelines"]) == 1


def test_export_does_not_leak_another_tenants_rows(engine: Engine) -> None:
    export = svc.export_tenant_data("tenant-a")

    run_ids = {row["run_id"] for row in export["runs"]}
    assert "run-other-tenant" not in run_ids


def test_export_secrets_metadata_never_includes_ciphertext(engine: Engine) -> None:
    export = svc.export_tenant_data("tenant-a")

    assert len(export["tenant_secrets"]) == 1
    secret = export["tenant_secrets"][0]
    assert secret["name"] == "api_key"
    assert "ciphertext" not in secret


def test_export_lists_only_existing_storage_artifacts(
    engine: Engine, fake_storage: FakeStorageBackend
) -> None:
    export = svc.export_tenant_data("tenant-a")

    keys = {row["key"] for row in export["storage_artifacts"]}
    assert keys == {"run-1.json", "run-1_silver.csv", "run-1_analysis.json", "run-1_gold_0.csv"}


def test_export_omits_artifact_keys_that_do_not_exist_in_storage(
    engine: Engine, fake_storage: FakeStorageBackend
) -> None:
    fake_storage.delete_bytes("run-1_gold_0.csv")

    export = svc.export_tenant_data("tenant-a")

    keys = {row["key"] for row in export["storage_artifacts"]}
    assert "run-1_gold_0.csv" not in keys


def test_export_raises_for_unknown_tenant(engine: Engine) -> None:
    with pytest.raises(TenantNotFoundError):
        svc.export_tenant_data("does-not-exist")


def test_export_includes_user_summary(engine: Engine) -> None:
    export = svc.export_tenant_data("tenant-a")

    assert export["user"]["id"] == "tenant-a"
    assert export["user"]["monthly_budget_usd"] == 100.0
