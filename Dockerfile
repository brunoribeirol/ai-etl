FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock* ./
COPY src/ ./src/

# Install production dependencies + the `app` extra (plotly, scikit-learn,
# statsmodels, streamlit) — required to run the Streamlit app (app.py).
# No dev extras (pytest, mypy, ruff, ...) in the runtime image.
RUN uv sync --no-dev --no-editable --extra app

# Copy remaining files
COPY case_study/ ./case_study/
COPY .env.example .env.example
COPY app.py ./app.py

ENV PATH="/app/.venv/bin:$PATH"

# Railway injects $PORT at runtime; do not hardcode 8501.
ENTRYPOINT ["sh", "-c", "streamlit run app.py --server.port=$PORT --server.address=0.0.0.0"]
