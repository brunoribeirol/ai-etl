"""Integration tests: RLS defense-in-depth against a live Postgres (ADR-040).

Skipped when `app-postgres-test` isn't reachable (`make app-db-test-up`), same
convention as `test_alembic_migration.py`/`test_audit_persistence.py`.

Two things are proven here, against a *real* Postgres, not mocked or reasoned
about in the abstract:

1. `test_tenant_scope_blocks_cross_tenant_read_with_no_where_clause` — the
   actual backstop this whole feature exists to provide: a query through the
   restricted role that simulates the exact bug class ADR-040 defends
   against (a missing `WHERE tenant_id = :id`) returns zero rows for another
   tenant's data, not both tenants' rows.
2. `test_set_local_guc_does_not_leak_across_pooled_connection_reuse` — ADR-032's
   single biggest concern about this design: with a pool of exactly one
   connection, a second "request" that reuses the same physical connection
   after the first request's transaction ended must not see the first
   request's `app.tenant_id` GUC still set. Proves `SET LOCAL`
   (`set_config(..., true)`) really does reset at transaction end, before the
   connection returns to the pool.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy
from alembic.config import Config
from sqlalchemy import text

from ai_etl.audit import connection
from alembic import command

_TEST_APP_DATABASE_URL = os.getenv(
    "TEST_APP_DATABASE_URL",
    "postgresql://ai_etl_app_test:ai_etl_app_test@localhost:5435/ai_etl_app_test_db",
)
_TEST_APP_DATABASE_URL_TENANT = os.getenv(
    "TEST_APP_DATABASE_URL_TENANT",
    "postgresql://ai_etl_app_tenant:ai_etl_app_tenant@localhost:5435/ai_etl_app_test_db",
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
def _clean_slate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Same `DROP SCHEMA ... CASCADE` + full migration replay as
    `test_alembic_migration.py` — this is the one test file that most needs the
    restricted role's grants and the RLS policies to genuinely exist, not just
    a `metadata.create_all()` shape-alike."""
    monkeypatch.setenv("APP_DATABASE_URL", _TEST_APP_DATABASE_URL)
    monkeypatch.setenv("APP_DATABASE_URL_TENANT", _TEST_APP_DATABASE_URL_TENANT)
    connection.get_engine.cache_clear()
    connection.get_tenant_engine.cache_clear()
    engine = sqlalchemy.create_engine(_TEST_APP_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(_alembic_config(), "head")
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    connection.get_engine.cache_clear()
    connection.get_tenant_engine.cache_clear()


def _insert_user_and_run(bypass_engine: sqlalchemy.Engine, tenant_id: str, run_id: str) -> None:
    with bypass_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, created_at) VALUES (:id, now())"),
            {"id": tenant_id},
        )
        conn.execute(
            text(
                "INSERT INTO runs (run_id, spec, status, timestamp, tenant_id) "
                "VALUES (:run_id, 'spec', 'completed', now(), :tenant_id)"
            ),
            {"run_id": run_id, "tenant_id": tenant_id},
        )


def test_tenant_scope_blocks_cross_tenant_read_with_no_where_clause() -> None:
    """The actual backstop: a query run through the restricted engine's
    `tenant_scope()` that *has no `WHERE tenant_id = ...` clause at all* —
    simulating the exact application bug class ADR-040 exists to catch —
    must still return only the GUC-scoped tenant's own row, never another
    tenant's, thanks to the RLS policy alone."""
    bypass_engine = connection.get_engine()
    tenant_a, tenant_b = f"tenant-a-{uuid.uuid4().hex[:8]}", f"tenant-b-{uuid.uuid4().hex[:8]}"
    run_a, run_b = f"run-a-{uuid.uuid4().hex[:8]}", f"run-b-{uuid.uuid4().hex[:8]}"
    _insert_user_and_run(bypass_engine, tenant_a, run_a)
    _insert_user_and_run(bypass_engine, tenant_b, run_b)

    # No `WHERE tenant_id = ...` at all — the RLS policy is the only thing
    # standing between this query and both tenants' rows.
    with connection.tenant_scope(tenant_a) as conn:
        rows = conn.execute(text("SELECT run_id, tenant_id FROM runs")).fetchall()

    assert [r.run_id for r in rows] == [run_a]
    assert rows[0].tenant_id == tenant_a

    with connection.tenant_scope(tenant_b) as conn:
        rows = conn.execute(text("SELECT run_id, tenant_id FROM runs")).fetchall()

    assert [r.run_id for r in rows] == [run_b]


def test_tenant_scope_blocks_write_to_another_tenants_row() -> None:
    """The `WITH CHECK` half of the policy: an `INSERT` attempting to write a
    row under a `tenant_id` that doesn't match the GUC is rejected outright by
    Postgres — a tenant cannot plant a row claiming to belong to another
    tenant, even if it tries to."""
    bypass_engine = connection.get_engine()
    tenant_a, tenant_b = f"tenant-a-{uuid.uuid4().hex[:8]}", f"tenant-b-{uuid.uuid4().hex[:8]}"
    with bypass_engine.begin() as conn:
        for tenant_id in (tenant_a, tenant_b):
            conn.execute(
                text("INSERT INTO users (id, created_at) VALUES (:id, now())"), {"id": tenant_id}
            )

    with pytest.raises(sqlalchemy.exc.DBAPIError):
        with connection.tenant_scope(tenant_b) as conn:
            # Scoped as tenant_b, but this row claims tenant_a — the policy's
            # WITH CHECK clause must reject it before it ever commits.
            conn.execute(
                text(
                    "INSERT INTO runs (run_id, spec, status, timestamp, tenant_id) "
                    "VALUES (:run_id, 'spec', 'completed', now(), :wrong_tenant)"
                ),
                {"run_id": f"forged-{uuid.uuid4().hex[:8]}", "wrong_tenant": tenant_a},
            )


def test_set_local_guc_does_not_leak_across_pooled_connection_reuse() -> None:
    """ADR-032's single biggest concern, verified for real: with a pool of
    exactly one connection, a second `tenant_scope()` call that reuses the
    same physical connection after the first one's transaction committed
    must not still see the first call's `app.tenant_id` — proving `SET LOCAL`
    (`set_config(..., true)`) truly resets at transaction end rather than
    persisting on the pooled connection for whichever tenant's request
    happens to reuse it next.
    """
    tenant_first = f"tenant-first-{uuid.uuid4().hex[:8]}"
    small_pool_engine = sqlalchemy.create_engine(
        _TEST_APP_DATABASE_URL_TENANT, pool_size=1, max_overflow=0
    )
    try:
        # "Request 1": sets the GUC, then ends its transaction (commits).
        with small_pool_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_first},
            )
            observed_in_txn_1 = conn.execute(
                text("SELECT current_setting('app.tenant_id', true)")
            ).scalar()

        # "Request 2": a brand-new transaction on what is, with pool_size=1,
        # necessarily the *same* underlying DBAPI connection request 1 used —
        # and does not itself call set_config at all.
        with small_pool_engine.begin() as conn:
            observed_in_txn_2 = conn.execute(
                text("SELECT current_setting('app.tenant_id', true)")
            ).scalar()
    finally:
        small_pool_engine.dispose()

    assert observed_in_txn_1 == tenant_first
    # The GUC did NOT survive into the next transaction on the reused
    # connection — it reads back empty/NULL, not tenant_first's value. This is
    # exactly the property that makes reusing a pooled connection across two
    # different tenants' requests safe: RLS then denies all rows (fail closed)
    # until that request's own `tenant_scope()` call sets it again.
    assert observed_in_txn_2 in ("", None)


def test_tenant_scope_sees_own_row_when_where_clause_is_present() -> None:
    """Sanity check alongside the no-WHERE-clause test above: the normal,
    correctly-filtered case still works exactly as before — RLS is additive,
    not a replacement for the application's own `WHERE tenant_id = ...`."""
    bypass_engine = connection.get_engine()
    tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
    run_a = f"run-a-{uuid.uuid4().hex[:8]}"
    _insert_user_and_run(bypass_engine, tenant_a, run_a)

    with connection.tenant_scope(tenant_a) as conn:
        row = conn.execute(
            text("SELECT run_id FROM runs WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_a},
        ).first()

    assert row is not None
    assert row.run_id == run_a
