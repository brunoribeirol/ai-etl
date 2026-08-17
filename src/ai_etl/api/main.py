"""FastAPI app entrypoint (Sprint 6, ADR-011).

Run locally: `uv run uvicorn ai_etl.api.main:app --reload`
Production (Railway, after the Sprint 6 cutover): `uvicorn ai_etl.api.main:app --host 0.0.0.0 --port $PORT`
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_etl.api.routers import runs

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
