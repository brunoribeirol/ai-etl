FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock* ./
COPY src/ ./src/

# Install production dependencies only (no dev extras)
RUN uv sync --no-dev --no-editable

# Copy remaining files
COPY case_study/ ./case_study/
COPY .env.example .env.example

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python", "-m", "ai_etl"]
CMD ["--help"]
