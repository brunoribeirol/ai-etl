"""Per-tenant locale config (Sprint 25, ADR-036).

Mirrors `audit/db/retention.py`'s shape exactly — a single non-nullable scalar on `users`,
no history/versioning table needed (see ADR-036 §1 for why `locale` is `NOT NULL DEFAULT
'pt-BR'` rather than nullable like `retention_days`/the LLM override columns).
"""

from __future__ import annotations

from sqlalchemy import select, update

from ai_etl.audit.connection import get_engine
from ai_etl.audit.models import users
from ai_etl.core.locale import DEFAULT_LOCALE, resolve_locale


def get_locale(tenant_id: str) -> str:
    """Return the tenant's configured locale, defaulting to `DEFAULT_LOCALE` if the
    tenant does not exist yet (mirrors `get_retention_days`'s "treat missing as unset"
    contract) or if the stored value somehow isn't one of the supported locales
    (`resolve_locale`'s soft-fail — defensive against a hand-edited row or a future
    narrowing of `SUPPORTED_LOCALES`)."""
    stmt = select(users.c.locale).where(users.c.id == tenant_id)
    with get_engine().connect() as conn:
        row = conn.execute(stmt).first()
    if row is None or row[0] is None:
        return DEFAULT_LOCALE
    return resolve_locale(row[0])


def set_locale(tenant_id: str, locale: str) -> str:
    """Set the tenant's locale. `locale` must already be validated against
    `core.locale.SUPPORTED_LOCALES` by the caller (`PATCH /tenant/locale`) — this
    function trusts its input, same as `set_retention_days`/`set_monthly_budget`.
    Returns the value written back, for the caller to echo in its response."""
    stmt = update(users).where(users.c.id == tenant_id).values(locale=locale)
    with get_engine().begin() as conn:
        conn.execute(stmt)
    return locale
