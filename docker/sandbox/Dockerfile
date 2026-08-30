# ADR-038 / ADR-039: shared image for both sandbox backends of
# core/sandbox.py's execute_in_sandbox() that isolate code inside a
# container rather than a bare `multiprocessing.Process`:
#   - backend="docker" (ADR-038): `docker run` locally/dev, driven by
#     `run_sandboxed.py` as this image's ENTRYPOINT.
#   - backend="vercel" (ADR-039): the same image, pushed to Vercel
#     Container Registry (`make sandbox-vcr-image`) and booted as a
#     Firecracker microVM by Vercel Sandbox in production. Vercel Sandbox
#     ignores a custom image's ENTRYPOINT/CMD entirely, so that backend
#     instead runs `run_sandboxed_vercel.py` explicitly via
#     `box.run_process(...)` — see core/sandbox_vercel.py.
#
# Deliberately installs the real `ai_etl` package (same as the top-level
# Dockerfile) so both driver scripts can import
# `ai_etl.core.sandbox._execute_code` directly — the restricted-globals /
# mode ("function" vs "script") dispatch logic stays defined in exactly one
# place (core/sandbox.py), not duplicated into this image. This image only
# plumbs a pickle payload in (stdin for the Docker backend, a file for the
# Vercel backend — Vercel Sandbox's Python SDK does not support piping to a
# process's stdin) and a pickle result out; it does not re-implement any
# security-relevant logic.
#
# Not used by the top-level Dockerfile/Railway deploy — see ADR-038 for why
# Docker-in-Docker doesn't work from inside a Railway service. Built and run
# locally via `make sandbox-image` / core/sandbox_docker.py for
# AI_ETL_SANDBOX_BACKEND=docker opt-in, and pushed to VCR via `make
# sandbox-vcr-image` for the AI_ETL_SANDBOX_BACKEND=vercel production path
# (ADR-039).
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
COPY docker/sandbox/run_sandboxed_vercel.py ./run_sandboxed_vercel.py

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
