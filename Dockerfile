FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching. README.md is required here,
# not just at runtime: hatchling (the build backend) validates pyproject.toml's
# `readme = "README.md"` field exists on disk while building the local
# package wheel during `uv sync` below — without it, the build fails with
# "OSError: Readme file does not exist: README.md".
COPY pyproject.toml uv.lock* README.md ./
COPY src/ ./src/

# Install production dependencies + the `api` extra (fastapi/uvicorn,
# required to run ai_etl.api.main, Sprint 6/ADR-011) + the `vercel-sandbox`
# extra (the `vercel` package, ADR-039) — this one shared image serves all
# Railway services (FastAPI web, Celery worker, Celery beat), and it's the
# worker that actually executes core/sandbox_vercel.py when
# AI_ETL_SANDBOX_BACKEND=vercel, not the API. Found 2026-08-31: this image
# wasn't even being built for the worker service (Railway had it on the
# RAILPACK builder instead — separately fixed on the Railway side), and the
# `vercel-sandbox` extra was missing here regardless, so the worker raised
# VercelSandboxUnavailableError on every sandboxed run. Streamlit's `app`
# extra was retired in Sprint 6's PR 6 cutover — app.py is gone. No dev
# extras (pytest, mypy, ruff, ...) in the runtime image.
RUN uv sync --no-dev --no-editable --extra api --extra vercel-sandbox

# Copy remaining files
COPY case_study/ ./case_study/
COPY .env.example .env.example

ENV PATH="/app/.venv/bin:$PATH"

# Railway injects $PORT at runtime; `sh -c` is required for it to expand —
# Railway runs ENTRYPOINT/startCommand without a shell otherwise (see
# docs/CURRENT_STATE.md's PR #20/#46 history).
ENTRYPOINT ["sh", "-c", "uvicorn ai_etl.api.main:app --host 0.0.0.0 --port $PORT"]
