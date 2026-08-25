"""Platform-admin tenant directory (Wave 6, 2026-08-25 — admin panel + approval-gate UI).

`users.id` doubles as `tenant_id` throughout this codebase (a Clerk user id or
org id, never a separate UUID — see `audit/models.py`). There was previously
no way to enumerate every tenant at all; `api/routers/admin.py`'s 3 existing
routes (Sprint 31, ADR-032) all require a `tenant_id` the caller already knows.
This is the first cross-tenant *directory* read, not a policy/config lookup
like `retention.py::list_tenants_with_retention` (which this module's shape
otherwise mirrors) — every tenant is returned, not just ones with a policy set.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from typing_extensions import TypedDict

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import users


class TenantSummary(TypedDict):
    tenant_id: str
    created_at: datetime


def list_all_tenants() -> list[TenantSummary]:
    """Every tenant (`users` row), oldest first — `users.id` has no separate
    display name/email column (it's the raw Clerk id), so this is the whole
    directory an admin has to work with today; a caller cross-references
    `target_tenant_id` values from the admin audit log for context.
    """
    stmt = select(users.c.id, users.c.created_at).order_by(users.c.created_at)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [TenantSummary(tenant_id=row.id, created_at=row.created_at) for row in rows]
