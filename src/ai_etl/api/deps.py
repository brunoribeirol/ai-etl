"""FastAPI dependencies — auth (ADR-011, reuses ADR-006 unchanged)."""

from fastapi import Header, HTTPException

from ai_etl.audit.db import ensure_user
from ai_etl.services.auth_service import verify_session_token


def get_current_tenant_id(authorization: str | None = Header(default=None)) -> str:
    """Verify the `Authorization: Bearer <token>` header and return the tenant id.

    Calls the exact same `verify_session_token()` `_render_sign_in_gate()`
    (Streamlit) already used — no new verification logic, no trusting
    anything Clerk's frontend SDK claims client-side without re-checking the
    JWT's signature/issuer/expiry server-side. Raises `HTTPException(401)` on
    any failure, matching `verify_session_token`'s fail-closed contract.

    `ensure_user()` runs on every call (ADR-006: a brand-new Clerk account has
    no `users` row yet, and `runs`/`analysis_runs.tenant_id` are NOT NULL FKs
    to it) — cheap, idempotent (`ON CONFLICT DO NOTHING`), unlike
    `_render_sign_in_gate()` this has no per-session cache to skip repeat
    calls, since a stateless API has no `st.session_state` equivalent to
    cache against; acceptable at this project's scale (see `ensure_user`'s
    own docstring).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    token = authorization.removeprefix("Bearer ").strip()
    result = verify_session_token(token)
    if not result["ok"]:
        raise HTTPException(status_code=401, detail=result["error"])

    tenant_id = result["user_id"]
    assert tenant_id is not None  # verify_session_token guarantees this when ok=True
    ensure_user(tenant_id)
    return tenant_id
