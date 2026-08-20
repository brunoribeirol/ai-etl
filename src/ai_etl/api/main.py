"""FastAPI app entrypoint (Sprint 6, ADR-011).

Run locally: `uv run uvicorn ai_etl.api.main:app --reload`
Production (Railway, after the Sprint 6 cutover): `uvicorn ai_etl.api.main:app --host 0.0.0.0 --port $PORT`
"""

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_etl.api.deps import get_current_tenant_id
from ai_etl.api.routers import pipelines, runs
from ai_etl.core.llm import get_model_name

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
app.include_router(pipelines.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def config(tenant_id: str = Depends(get_current_tenant_id)) -> dict[str, str]:
    """Sprint 7: read-only — which model every agent in a run currently uses
    (`AI_ETL_LLM_MODEL`, `core/llm.py`). Streamlit's sidebar showed the same
    thing (`st.caption(f"Modelo: {LLM_MODEL}")`), also read-only — choosing a
    model per run is real business logic (allowlist, cost-tracking, ~6
    `get_llm()` call sites), deliberately out of scope here (see PR
    description). Behind the same auth as every other endpoint even though
    the value isn't sensitive, to keep one auth story rather than carving out
    a public-endpoint exception for one low-stakes field."""
    return {"model_name": get_model_name()}
