"""Application database connections.

This is a separate Postgres database from `POSTGRES_URL` (`sources/postgres_source.py`,
`destinations/postgres_dest.py`), which is a pipeline data source/destination the user
points at their own data. `APP_DATABASE_URL`/`APP_DATABASE_URL_TENANT` are AI-ETL's own
database, holding the `runs`/`analysis_runs` audit tables — logically distinct even when
both run in the same docker-compose project.

Two engines, two roles (ADR-040, superseding ADR-032 Decision 1's "keep
`rolbypassrls=true`" posture):

- `get_engine()` — the original, bypass-capable role (`rolbypassrls=true`). Kept for
  the narrow, deliberate cross-tenant paths that must see every tenant's rows by
  design: `audit/admin_log.py` and the platform-admin routes it backs (ADR-032
  Decision 2), and background/scheduler jobs that are not acting on behalf of any
  single tenant's request (e.g. `audit/db/pipelines.py::list_due_pipelines`,
  `audit/db/retention.py::list_tenants_with_retention`).
- `get_tenant_engine()` / `tenant_scope()` — a new, non-bypass role
  (`APP_DATABASE_URL_TENANT`) for every per-tenant-request read/write in
  `audit/db/*.py`. Row Level Security policies (migration `0021`) enforce
  `tenant_id = current_setting('app.tenant_id')` as a real, database-level backstop
  against a future `WHERE tenant_id = :id` bug in application code — see ADR-040 for
  the full design and how its pooled-connection safety was verified
  (`tests/integration/test_tenant_isolation_rls.py`).
"""

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import Connection, Engine, create_engine, text


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a process-wide engine for the application database, using the
    bypass-capable role (`rolbypassrls=true`).

    Uses APP_DATABASE_URL from environment. Cached per-process: the variable is read
    once. Tests that need a different URL should call `get_engine.cache_clear()` after
    setting the environment variable.
    """
    url = os.getenv("APP_DATABASE_URL")
    if not url:
        raise EnvironmentError("APP_DATABASE_URL environment variable is not set.")
    return create_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_tenant_engine() -> Engine:
    """Return a process-wide engine for the application database, using the new
    restricted, non-bypass role (ADR-040).

    Uses `APP_DATABASE_URL_TENANT` from environment — a distinct Postgres role from
    `APP_DATABASE_URL`'s, with `NOBYPASSRLS` and no privileges beyond what the RLS
    policies in migration `0021` allow. Never connect with this engine outside
    `tenant_scope()` below — a bare connection from this engine has no `app.tenant_id`
    GUC set, so every tenant-scoped table's RLS policy would deny all rows (fail
    closed, not fail open — but also not the query the caller wanted).
    """
    url = os.getenv("APP_DATABASE_URL_TENANT")
    if not url:
        raise EnvironmentError("APP_DATABASE_URL_TENANT environment variable is not set.")
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[Connection]:
    """Open one transaction on the restricted tenant engine with `app.tenant_id`
    set for exactly this transaction (ADR-040).

    Every `audit/db/*.py` function that reads or writes one tenant's own data should
    open its connection through this context manager instead of calling
    `get_engine()`/`get_tenant_engine()` directly — it is the single place that binds
    the GUC, so every call site gets the same guarantee.

    Uses `SELECT set_config('app.tenant_id', :tenant_id, true)` rather than
    `SET LOCAL app.tenant_id = ...` for one reason: `SET LOCAL` does not accept a bound
    parameter (it is DDL-like session-config syntax, not a normal statement), which
    would force interpolating `tenant_id` directly into the SQL string — exactly the
    f-string-SQL pattern this project's non-negotiable rules forbid, even for an
    internally-sourced value. `set_config(name, value, is_local)`, by contrast, is a
    regular function call and takes `tenant_id` as a normal bound parameter; its third
    argument (`true`) gives it the identical transaction-local semantics as
    `SET LOCAL` — the setting reverts automatically at `COMMIT`/`ROLLBACK`, before the
    underlying connection is ever returned to the pool.

    This transaction-local reset is exactly what makes reusing a pooled connection
    across different tenants' requests safe: the GUC lives only inside the
    transaction `engine.begin()` opens here, so a later request that checks out the
    same physical connection starts a brand-new transaction with no `app.tenant_id`
    set at all (RLS then denies all rows — fail closed — until this function sets it
    again for that request). See `tests/integration/test_tenant_isolation_rls.py`
    for a real Postgres test proving this: two "requests" sharing one pooled
    connection (pool_size=1), the second not observing the first's tenant_id.
    """
    engine = get_tenant_engine()
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        yield conn


@contextmanager
def scoped_connection(tenant_id: str | None) -> Iterator[Connection]:
    """`tenant_scope(tenant_id)` when `tenant_id` is known, else a plain
    bypass-engine transaction (ADR-040).

    A handful of `audit/db/*.py` functions accept `tenant_id: str | None = None` for
    backward compatibility with callers that predate per-tenant scoping (ADR-006) —
    real callers always pass one today, and the tenant-scoped tables' own NOT NULL
    constraints make a genuine `None` write fail regardless of which engine handles
    it. This helper keeps each call site a single `with` statement instead of an
    `if tenant_id is not None: ... else: ...` branch repeated at every one of them.
    """
    if tenant_id is not None:
        with tenant_scope(tenant_id) as conn:
            yield conn
        return
    with get_engine().begin() as conn:
        yield conn
