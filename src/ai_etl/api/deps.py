"""FastAPI dependencies — auth (ADR-011, reuses ADR-006) and RBAC (ADR-022, ADR-032)."""

import os
from collections.abc import Callable
from typing import Annotated, Optional, TypedDict

from fastapi import Depends, Header, HTTPException

from ai_etl.audit.db import ensure_user
from ai_etl.services.auth_service import verify_session_token

# Sprint 19 (ADR-022) + Sprint 31 (ADR-032) — the app-level roles. `editor`
# can configure/mutate within its own tenant (create/patch pipelines, submit
# runs, manage secrets, set budget); `viewer` can only read within its own
# tenant; `admin` (ADR-032) is a platform-operator role, not a per-tenant
# org role — it is the only role allowed cross-tenant access at all, and
# every such access is written to a dedicated audit trail (`audit/admin_log.py`),
# never `PipelineState.audit_log`, which is per-run. Ordering matters for
# `_role_satisfies`.
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}

# Clerk role-key substring that means "full access" within an organization —
# covers both the legacy `"admin"` role key and the current `"org:admin"`
# convention. See ADR-022 Decision 1 for why this is a heuristic, not an
# exhaustive mapping of arbitrary custom Clerk roles.
#
# Deliberately distinct from the `admin` app role (ADR-032): a Clerk org
# admin only ever gets `editor` here — full control of *their own*
# organization's tenant, same as before ADR-032. The app's `admin` role is
# platform-operator-only (Bruno/support), resolved from the individual
# Clerk user id via `_platform_admin_user_ids()` below, independent of any
# organization the caller happens to belong to.
_EDITOR_ROLE_MARKER = "admin"


def _platform_admin_user_ids() -> set[str]:
    """Comma-separated Clerk user ids (`sub` claim) with platform `admin`
    access, from `AI_ETL_PLATFORM_ADMINS`. Read on every call (not cached at
    import time) so tests can set/unset the env var freely; the allowlist is
    tiny and this runs once per request, not a hot loop.

    Deliberately an env-var allowlist, not a DB column or a Clerk role —
    platform admin is an operational identity (Bruno, and later, support
    staff) orthogonal to any tenant's own Clerk Organization membership;
    encoding it as a Clerk org role would make it a *tenant's* decision who
    gets cross-tenant access to *other* tenants' data, which is backwards.
    """
    raw = os.getenv("AI_ETL_PLATFORM_ADMINS", "")
    return {uid.strip() for uid in raw.split(",") if uid.strip()}


class AuthContext(TypedDict):
    tenant_id: str
    role: str  # "editor" | "viewer" | "admin"
    user_id: str  # individual Clerk user id (`sub`) — the actor for admin audit logging


def _resolve_role(user_id: str, org_id: Optional[str], org_role: Optional[str]) -> str:
    """Map a Clerk identity to this app's viewer/editor/admin role.

    Platform admin (ADR-032) takes priority over any org-role heuristic: an
    individual Clerk user id listed in `AI_ETL_PLATFORM_ADMINS` is `admin`
    regardless of which organization is active in their session, or whether
    one is active at all.

    Otherwise: no active organization (`org_id` is `None`) -> `editor`, a
    solo tenant is its own sole owner, matching every pre-ADR-022 account's
    unrestricted behavior unchanged. Within an organization, only a role
    whose Clerk key looks like an admin role gets `editor`; every other org
    member is a `viewer` by default (fail toward less access, not more).
    """
    if user_id in _platform_admin_user_ids():
        return "admin"
    if org_id is None:
        return "editor"
    if org_role and _EDITOR_ROLE_MARKER in org_role.lower():
        return "editor"
    return "viewer"


def get_current_auth_context(authorization: str | None = Header(default=None)) -> AuthContext:
    """Verify the `Authorization: Bearer <token>` header once and resolve
    both `tenant_id` and `role` from it (ADR-022).

    Calls the same `verify_session_token()` as before — no new verification
    logic, no trusting anything Clerk's frontend SDK claims client-side
    without re-checking the JWT's signature/issuer/expiry server-side.
    Raises `HTTPException(401)` on any failure, matching
    `verify_session_token`'s fail-closed contract.

    `ensure_user()` runs on every call (ADR-006) — cheap, idempotent
    (`ON CONFLICT DO NOTHING`); unchanged from the pre-ADR-022 behavior.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    token = authorization.removeprefix("Bearer ").strip()
    result = verify_session_token(token)
    if not result["ok"]:
        raise HTTPException(status_code=401, detail=result["error"])

    user_id = result["user_id"]
    assert user_id is not None  # verify_session_token guarantees this when ok=True

    # ADR-022: an active Clerk Organization becomes the tenant; otherwise the
    # individual user id stays the tenant, unchanged from ADR-006.
    tenant_id = result["org_id"] or user_id
    role = _resolve_role(user_id, result["org_id"], result["org_role"])

    ensure_user(tenant_id)
    return AuthContext(tenant_id=tenant_id, role=role, user_id=user_id)


def get_current_tenant_id(
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> str:
    """Backward-compatible wrapper — every pre-ADR-022 call site keeps
    working unchanged, now backed by the shared auth-context resolution."""
    return auth["tenant_id"]


def require_role(min_role: str) -> Callable[..., str]:
    """Build a FastAPI dependency that requires at least `min_role`.

    Raises `HTTPException(403)` if the caller's resolved role doesn't meet
    `min_role`; otherwise returns `tenant_id`, so a route can depend on
    `Depends(require_role("editor"))` as a drop-in replacement for
    `Depends(get_current_tenant_id)` wherever the action mutates state.
    """

    def _dependency(auth: Annotated[AuthContext, Depends(get_current_auth_context)]) -> str:
        if _ROLE_RANK[auth["role"]] < _ROLE_RANK[min_role]:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{auth['role']}' cannot perform this action (requires '{min_role}').",
            )
        return auth["tenant_id"]

    return _dependency


def require_admin(
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> AuthContext:
    """Dependency for platform-admin-only, cross-tenant endpoints (ADR-032).

    Unlike `require_role()`, returns the full `AuthContext` rather than
    collapsing to `tenant_id` — an admin's *own* `tenant_id` is not the
    resource these endpoints operate on (the target tenant is a separate,
    explicit path/query parameter chosen by the caller); the router needs
    `user_id` too, as the actor recorded in `audit/admin_log.py`.
    """
    if _ROLE_RANK[auth["role"]] < _ROLE_RANK["admin"]:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{auth['role']}' cannot perform this action (requires 'admin').",
        )
    return auth
