"""FastAPI dependencies — auth (ADR-011, reuses ADR-006) and RBAC (ADR-021)."""

from collections.abc import Callable
from typing import Annotated, Optional, TypedDict

from fastapi import Depends, Header, HTTPException

from ai_etl.audit.db import ensure_user
from ai_etl.services.auth_service import verify_session_token

# Sprint 19 (ADR-021) — the two app-level roles. `editor` can configure/mutate
# (create/patch pipelines, submit runs, manage secrets, set budget); `viewer`
# can only read. Ordering matters for `_role_satisfies`.
_ROLE_RANK = {"viewer": 0, "editor": 1}

# Clerk role-key substring that means "full access" within an organization —
# covers both the legacy `"admin"` role key and the current `"org:admin"`
# convention. See ADR-021 Decision 1 for why this is a heuristic, not an
# exhaustive mapping of arbitrary custom Clerk roles.
_EDITOR_ROLE_MARKER = "admin"


class AuthContext(TypedDict):
    tenant_id: str
    role: str  # "editor" | "viewer"


def _resolve_role(org_id: Optional[str], org_role: Optional[str]) -> str:
    """Map a Clerk org role claim to this app's editor/viewer role.

    No active organization (`org_id` is `None`) -> `editor`: a solo tenant
    is its own sole owner, matching every pre-ADR-021 account's unrestricted
    behavior unchanged. Within an organization, only a role whose Clerk key
    looks like an admin role gets `editor`; every other org member is a
    `viewer` by default (fail toward less access, not more).
    """
    if org_id is None:
        return "editor"
    if org_role and _EDITOR_ROLE_MARKER in org_role.lower():
        return "editor"
    return "viewer"


def get_current_auth_context(authorization: str | None = Header(default=None)) -> AuthContext:
    """Verify the `Authorization: Bearer <token>` header once and resolve
    both `tenant_id` and `role` from it (ADR-021).

    Calls the same `verify_session_token()` as before — no new verification
    logic, no trusting anything Clerk's frontend SDK claims client-side
    without re-checking the JWT's signature/issuer/expiry server-side.
    Raises `HTTPException(401)` on any failure, matching
    `verify_session_token`'s fail-closed contract.

    `ensure_user()` runs on every call (ADR-006) — cheap, idempotent
    (`ON CONFLICT DO NOTHING`); unchanged from the pre-ADR-021 behavior.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    token = authorization.removeprefix("Bearer ").strip()
    result = verify_session_token(token)
    if not result["ok"]:
        raise HTTPException(status_code=401, detail=result["error"])

    user_id = result["user_id"]
    assert user_id is not None  # verify_session_token guarantees this when ok=True

    # ADR-021: an active Clerk Organization becomes the tenant; otherwise the
    # individual user id stays the tenant, unchanged from ADR-006.
    tenant_id = result["org_id"] or user_id
    role = _resolve_role(result["org_id"], result["org_role"])

    ensure_user(tenant_id)
    return AuthContext(tenant_id=tenant_id, role=role)


def get_current_tenant_id(
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> str:
    """Backward-compatible wrapper — every pre-ADR-021 call site keeps
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
