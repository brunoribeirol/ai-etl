# ADR-038: image for the Docker-isolated sandbox backend of
# core/sandbox.py's execute_in_sandbox(backend="docker").
#
# Deliberately installs the real `ai_etl` package (same as the top-level
# Dockerfile) so `run_sandboxed.py` can import
# `ai_etl.core.sandbox._execute_code` directly — the restricted-globals /
# mode ("function" vs "script") dispatch logic stays defined in exactly one
# place (core/sandbox.py), not duplicated into this image. This image only
# plumbs a pickle payload in on stdin and a pickle result out on stdout; it
# does not re-implement any security-relevant logic.
#
# Not used by the top-level Dockerfile/Railway deploy — see ADR-038 for why
# (Railway containers are non-privileged; Docker-in-Docker from inside an
# already-containerized Railway service is not supported). Built and run
# locally via `make sandbox-image` / core/sandbox_docker.py, for
# AI_ETL_SANDBOX_BACKEND=docker opt-in only.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* README.md ./
COPY src/ ./src/

# No extras: plotly/scikit-learn/statsmodels are already base dependencies
# (pyproject.toml — Analyst/Science need them unconditionally). No `api`
# extra: fastapi/uvicorn are never imported by sandboxed code.
RUN uv sync --no-dev --no-editable

COPY docker/sandbox/run_sandboxed.py ./run_sandboxed.py

ENV PATH="/app/.venv/bin:$PATH"
# Belt-and-suspenders: PYTHONDONTWRITEBYTECODE avoids the interpreter trying
# to write .pyc files onto the read-only root filesystem `docker run
# --read-only` enforces at the call site (core/sandbox_docker.py).
ENV PYTHONDONTWRITEBYTECODE=1

# Non-root at image level too — `docker run --user 65534:65534` (the
# call site's own flag) is the layer that actually matters at runtime, but a
# non-root default here means the image is safe even if some future caller
# forgets that flag.
RUN useradd --uid 65534 --no-create-home --shell /usr/sbin/nologin sandboxuser || true
USER 65534:65534

ENTRYPOINT ["python", "/app/run_sandboxed.py"]
