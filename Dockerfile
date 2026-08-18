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

# Install production dependencies + the `app` extra (streamlit, required to
# run app.py) and the `api` extra (fastapi/uvicorn, required to run the new
# ai_etl.api.main FastAPI app, Sprint 6/ADR-011). One shared image serves
# all three Railway services (Streamlit web, FastAPI web, Celery worker)
# during the transition — both extras stay installed until app.py/`app` is
# retired in Sprint 6's PR 6 cutover. No dev extras (pytest, mypy, ruff, ...)
# in the runtime image.
RUN uv sync --no-dev --no-editable --extra app --extra api

# Copy remaining files
COPY case_study/ ./case_study/
COPY .env.example .env.example
COPY app.py ./app.py

ENV PATH="/app/.venv/bin:$PATH"

# Railway injects $PORT at runtime; do not hardcode 8501.
ENTRYPOINT ["sh", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0"]
