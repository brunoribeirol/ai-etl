"""FastAPI app entrypoint (Sprint 6, ADR-011).

Run locally: `uv run uvicorn ai_etl.api.main:app --reload`
Production (Railway, after the Sprint 6 cutover): `uvicorn ai_etl.api.main:app --host 0.0.0.0 --port $PORT`
"""

import os
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_etl.api.deps import AuthContext, get_current_auth_context
from ai_etl.api.routers import (
    admin,
    budget,
    cost_estimation,
    llm,
    onboarding,
    pipelines,
    runs,
    secrets,
    tenant,
)
from ai_etl.core.llm import get_model_name
from ai_etl.core.logging_config import configure_logging
from ai_etl.core.observability import init_sentry

# Sprint 34 (ADR-033) — both run before `FastAPI(...)` is constructed: JSON
# logging must be attached before any import-time log line fires (several
# routers/services log at module scope), and Sentry's `FastApiIntegration`
# patches Starlette internals that `FastAPI(...)` reads at construction time.
configure_logging()
init_sentry(component="api")

app = FastAPI(title="AI-ETL API", version="1.0.0")

# Vercel preview/production domains calling this Railway-hosted API are
# cross-origin — comma-separated allowlist via env var, never `["*"]` (would
# accept credentialed requests from any origin, defeating the point of
# Clerk-verified per-request auth). No default: an unconfigured deployment
# should reject every browser-origin request loudly, not silently allow none
# or (worse) all.
_allowed_origins = [
    origin.strip() for origin in os.getenv("API_ALLOWED_ORIGINS", "").split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(cost_estimation.router)
app.include_router(pipelines.router)
app.include_router(budget.router)
app.include_router(secrets.router)
app.include_router(tenant.router)
app.include_router(onboarding.router)
app.include_router(admin.router)
app.include_router(llm.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def config(auth: Annotated[AuthContext, Depends(get_current_auth_context)]) -> dict[str, str]:
    """Sprint 7: read-only — this deployment's global model (`AI_ETL_LLM_MODEL`,
    `core/llm.py::get_model_name()`). Streamlit's sidebar showed the same thing
    (`st.caption(f"Modelo: {LLM_MODEL}")`), also read-only. A per-`saved_pipeline`
    override now exists (`GET/PUT /pipelines/{id}/llm-config`, Sprint 30 ADR-031,
    actually wired into execution as of the Sprint 30 gap-closing fix) — this
    endpoint intentionally stays scoped to the deployment default, not a
    per-pipeline resolved value, since it takes no pipeline id. Behind the same
    auth as every other endpoint even though the value isn't sensitive, to keep
    one auth story rather than carving out a public-endpoint exception for one
    low-stakes field.

    `role` (Wave 6, 2026-08-25 admin panel/approval-gate UI plan): the
    frontend had no way to know the caller's resolved viewer/editor/admin
    role at all — `(app)/layout.tsx` already fetches `/config` once per page
    load (for the model badge), so this became the single source of truth
    for role-aware UI (e.g. showing the "Admin" nav link) instead of adding
    a dedicated endpoint. Cosmetic only — every admin/approval route still
    independently enforces its own role via `require_admin`/`require_role`,
    same as before; a hidden nav link is not the security boundary."""
    return {"model_name": get_model_name(), "role": auth["role"]}
