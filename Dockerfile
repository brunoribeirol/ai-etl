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
# required to run ai_etl.api.main, Sprint 6/ADR-011). This one shared image
# serves all Railway services (FastAPI web, Celery worker). Streamlit's
# `app` extra was retired in Sprint 6's PR 6 cutover — app.py is gone.
# No dev extras (pytest, mypy, ruff, ...) in the runtime image.
RUN uv sync --no-dev --no-editable --extra api

# Copy remaining files
COPY case_study/ ./case_study/
COPY .env.example .env.example

ENV PATH="/app/.venv/bin:$PATH"

# Railway injects $PORT at runtime; `sh -c` is required for it to expand —
# Railway runs ENTRYPOINT/startCommand without a shell otherwise (see
# docs/CURRENT_STATE.md's PR #20/#46 history).
ENTRYPOINT ["sh", "-c", "uvicorn ai_etl.api.main:app --host 0.0.0.0 --port $PORT"]
