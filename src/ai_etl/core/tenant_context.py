"""Transient, never-persisted per-run tenant connection overrides (ADR-044,
ADR-045).

ADR-022 Decision 4 shipped tenant-scoped secret storage (`services/
secrets_service.py`) but deliberately left every DB source/destination
connector on the single shared `POSTGRES_URL`-style env var — wiring a
tenant's own secret into a running pipeline was investigated and explicitly
deferred, because the obvious places to carry it (`pipeline_plan`, or a new
`PipelineState` field) both get serialized wholesale into the run's JSON
snapshot (`audit/db/runs.py::save_run` -> `_write_json` calls
`_make_serializable(dict(state))` on the *entire* state, with no key-based
redaction the way `audit/logger.py::_sanitize` applies to `log_action()`
details). Putting a decrypted connection string in `PipelineState` would
mean writing it in plaintext to local disk or S3 on every run.

This module is the resolution: a plain `contextvars.ContextVar`, set by
`services/pipeline_service.py` (which already has `tenant_id`) for the
duration of one graph run or one `loader_node` call, read by
`sources/postgres_source.py`/`mysql_source.py`/`mongodb_source.py` and
`destinations/postgres_dest.py` at call time. It is not part of
`PipelineState`, so it never reaches `_write_json`'s snapshot, `log_action`,
or anything a LangGraph node returns — and it doesn't widen any node's
signature (ADR-002/ADR-006's constraint that kept `tenant_id` itself out of
`PipelineState`) since connectors already take no `tenant_id` parameter
before or after this change.

ADR-045 extends the same context with the raw `tenant_id` itself (not just
the 3 pre-resolved DB overrides above), for `sources/rest_source.py`'s
`secret_ref` auth fields. Unlike Postgres/MySQL/MongoDB, a REST source's
credential isn't one fixed secret name per source type — the LLM-produced
`pipeline_plan` names whichever secret a given REST source's `auth` should
use, and that plan is only known *during* the graph run (the Orchestrator
node builds it), not beforehand like `resolve_tenant_overrides` needs to
resolve DB overrides. So REST resolution is lazy: `get_rest_secret(name)`
looks up `name` for the current run's tenant at call time, inside
`rest_source.py`, rather than being pre-resolved into a dict up front. This
still satisfies the same non-negotiable this whole module exists for: the
decrypted value only ever exists in this `ContextVar` and the connector's
local stack, never in `PipelineState`/`pipeline_plan`/a run's JSON snapshot.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from ai_etl.services.secrets_service import SecretNotFoundError, get_secret

logger = logging.getLogger(__name__)

# One fixed secret name per connector — a tenant stores their own connection
# string under this exact name via the existing `POST /secrets` endpoint (no
# new API surface). Deliberately a single "whole connection string" secret
# per source type, not separate host/user/password fields: `secrets_service`
# already stores one arbitrary string per name, so this is the smallest
# schema-compatible slice, not a new secrets model.
POSTGRES_SECRET_NAME = "postgres_connection_string"  # nosec B105 — a secret's
# *name*/label passed to `secrets_service.get_secret`, never a credential
# value itself; bandit's hardcoded-password heuristic false-positives on the
# "_string" suffix the same way `secrets_service.py` already documents for
# the literal word "Secret" in its own log messages.
MYSQL_SECRET_NAME = "mysql_connection_string"  # nosec B105 — see above
MONGODB_SECRET_NAME = "mongodb_connection_string"  # nosec B105 — see above

_SECRET_NAMES_BY_SOURCE_TYPE = {
    "postgres": POSTGRES_SECRET_NAME,
    "mysql": MYSQL_SECRET_NAME,
    "mongodb": MONGODB_SECRET_NAME,
}

_connection_overrides: ContextVar[dict[str, str]] = ContextVar(
    "tenant_connection_overrides", default={}
)
# ADR-045 — the raw tenant_id for this run, so `get_rest_secret` can resolve
# an arbitrary secret name lazily. Separate from `_connection_overrides`
# (pre-resolved, fixed names) since REST secret names are plan-dependent,
# not known until the Orchestrator node runs.
_current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)


def resolve_tenant_overrides(tenant_id: str | None) -> dict[str, str]:
    """Look up this tenant's stored connection-string secrets, if any.

    Returns only the source types the tenant actually configured — a tenant
    with no `postgres_connection_string` secret gets nothing for `"postgres"`
    and every connector falls back to the shared env var exactly as before
    (ADR-022's original single-shared-credential behavior, now the fallback
    rather than the only path). `tenant_id=None` (every avulso run with no
    authenticated caller) always returns `{}` — this feature only applies to
    a real, resolved tenant.

    Never lets a lookup failure block the run: same posture
    `pipeline_service.py::run_silver_pipeline` already applies to
    `get_locale` (a real regression found 2026-08-23 — a DB hiccup there
    previously crashed the *entire* run before a single agent ran, for a
    cosmetic setting). A missing `AI_ETL_SECRETS_ENCRYPTION_KEY` or an
    unreachable audit database is a much smaller failure than never running
    the pipeline at all — falls back to the shared env var and logs a
    warning instead. Each of the three secret names is resolved
    independently, so one connector's lookup failing doesn't also block a
    different connector's tenant override in the same run.
    """
    if tenant_id is None:
        return {}
    overrides: dict[str, str] = {}
    for source_type, secret_name in _SECRET_NAMES_BY_SOURCE_TYPE.items():
        try:
            overrides[source_type] = get_secret(tenant_id, secret_name)
        except SecretNotFoundError:
            continue
        except Exception:  # noqa: BLE001 — see docstring: never block a run over this
            logger.warning(
                "resolve_tenant_overrides: failed to resolve %r for tenant, "
                "falling back to the shared env var for %r",
                secret_name,
                source_type,
                exc_info=True,
            )
    return overrides


@contextmanager
def tenant_connections(overrides: dict[str, str], tenant_id: str | None = None) -> Iterator[None]:
    """Make `overrides` (source_type -> connection string) and `tenant_id`
    (ADR-045, for lazy REST `secret_ref` lookups) visible to connectors
    called anywhere below this `with` block, for this asyncio/thread context
    only, then restore the previous values.

    Safe under Celery's thread-pool worker (`--pool=threads`, see
    `Dockerfile`/service start commands): `ContextVar` is per-context, not
    per-thread, but each Celery task runs in its own context, and nothing
    here is awaited across a context switch that would leak one task's
    overrides into another's.
    """
    overrides_token = _connection_overrides.set(overrides)
    tenant_token = _current_tenant_id.set(tenant_id)
    try:
        yield
    finally:
        _connection_overrides.reset(overrides_token)
        _current_tenant_id.reset(tenant_token)


def get_connection_override(source_type: str) -> str | None:
    """Return this run's tenant-supplied connection string for `source_type`,
    or `None` if the current context has none (no active `tenant_connections`
    block, or the tenant didn't configure this source type) — callers fall
    back to the shared env var in that case.
    """
    return _connection_overrides.get().get(source_type)


def get_rest_secret(secret_ref: str) -> str | None:
    """ADR-045 — resolve `secret_ref` (an arbitrary name a `pipeline_plan`'s
    REST `auth` block references) for the current run's tenant, or `None` if
    there's no active tenant in context, the tenant never saved a secret by
    that name, or the lookup itself fails — `rest_source.py` falls back to
    its existing `env_var` field in every one of those cases, mirroring
    `resolve_tenant_overrides`'s "never block a run over this" posture.
    """
    tenant_id = _current_tenant_id.get()
    if tenant_id is None:
        return None
    try:
        return get_secret(tenant_id, secret_ref)
    except SecretNotFoundError:
        return None
    except Exception:  # noqa: BLE001 — see docstring: never block a run over this
        logger.warning(
            "get_rest_secret: failed to resolve %r for tenant, falling back to env_var",
            secret_ref,
            exc_info=True,
        )
        return None


__all__ = [
    "MONGODB_SECRET_NAME",
    "MYSQL_SECRET_NAME",
    "POSTGRES_SECRET_NAME",
    "get_connection_override",
    "get_rest_secret",
    "resolve_tenant_overrides",
    "tenant_connections",
]
