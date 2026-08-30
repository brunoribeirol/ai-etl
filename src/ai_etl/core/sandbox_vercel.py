"""Vercel Sandbox production isolation backend for `execute_in_sandbox()`
(ADR-039 — resolves ADR-038's deferred "production rollout" item).

ADR-038 built a real container-isolated backend (`core/sandbox_docker.py`)
but explicitly could not ship it to production: this project's Railway
deployment runs in a non-privileged container, and `docker run` cannot run
Docker-in-Docker there. This module gives the same kernel-enforced isolation
guarantees (no network, read-only filesystem, no host secrets, resource
ceilings) via **Vercel Sandbox** (Firecracker microVMs), which Railway *can*
reach over the network — no Docker daemon required on the calling side.

Payload transport differs from the Docker backend for one concrete reason:
Vercel Sandbox's Python SDK does not support piping data to a process's
stdin ("Process standard input isn't supported" — Python SDK reference).
The request is instead written to a file inside the sandbox
(`box.fs.write_bytes`), the driver script
(`docker/sandbox/run_sandboxed_vercel.py`, baked into the same custom image
the Docker backend uses) is run with that file's path as an argument, and
the result is read back from a second file (`box.fs.read_bytes`) — the same
pickled values that already cross the `multiprocessing.Process` boundary
today, just carried over sandbox files instead of a pipe.

**Custom image required.** The default `vercel/sandbox/universal:latest`
managed image ships Python but not pandas/numpy/plotly/scikit-learn/
statsmodels (see ADR-039's "Image strategy" section) — every real
Transformer/Analyst/Science call needs those. This backend therefore always
passes an explicit `image` (default `ai-etl-sandbox:latest`, overridable via
`AI_ETL_SANDBOX_VERCEL_IMAGE`), which must already exist in this project's
Vercel Container Registry (`make sandbox-vcr-image` pushes it — see
ADR-039). There is no per-call `pip install` fallback: installing
scikit-learn/statsmodels fresh on every ephemeral sandbox would add tens of
seconds of latency to every Transformer/Analyst/Science call, defeating the
whole point of a fast, ephemeral, Active-CPU-billed sandbox.

Uses the **synchronous** `vercel.sandbox.sync` API deliberately — every
existing caller of `execute_in_sandbox()` (Transformer, Analyst, Science, via
Celery tasks) is synchronous code, not an async FastAPI request handler, and
the Python SDK reference explicitly warns against calling sync Sandbox
methods from inside an active async event loop.
"""

import os
import pickle  # nosec B403 -- see the two module-level notes at the bottom of this file
import time
import uuid
from typing import Any, Optional

from ai_etl.core.sandbox import SandboxResult

# Overridable for tests / alternate image tags; the image `make
# sandbox-vcr-image` pushes to Vercel Container Registry (same Dockerfile as
# the "docker" backend's local image — see docker/sandbox/Dockerfile).
SANDBOX_IMAGE_ENV_VAR = "AI_ETL_SANDBOX_VERCEL_IMAGE"
DEFAULT_SANDBOX_IMAGE = "ai-etl-sandbox:latest"

_DEFAULT_VCPUS = 1
_DEFAULT_MEMORY_MB = 2048  # each vCPU includes 2GB by default per Vercel Sandbox pricing

# How much slack, on top of the caller's timeout_seconds, to give the
# sandbox's own execution_time_limit (session-level ceiling) versus
# run_process's kill_after (this specific process's ceiling). kill_after is
# the one that actually bounds a hung script; the session limit is a
# second, looser backstop so sandbox creation/file transfer overhead never
# gets counted against the caller's timeout.
_SESSION_LIMIT_SLACK_SECONDS = 30

_REQUIRED_CREDENTIAL_ENV_VARS = ("VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID")

try:
    from vercel.sandbox import (
        NetworkPolicy,
        SandboxApiError,
        SandboxCredentialsError,
        SandboxError,
        SandboxResources,
        SandboxTerminalStateError,
        SandboxTimeoutError,
    )
    from vercel.sandbox import sync as _vercel_sandbox_module

    _VERCEL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover -- exercised by unit tests via monkeypatch, not a real
    # missing-package run, since `vercel` isn't a base dependency (opt-in
    # extra: `pip install ai-etl-framework[vercel-sandbox]` / `uv add vercel`)
    _VERCEL_SDK_AVAILABLE = False


class VercelSandboxUnavailableError(RuntimeError):
    """Raised when `backend="vercel"` is requested but the backend can't run.

    Deliberately does NOT fall back to the `"process"` backend — same
    fail-closed contract as `DockerSandboxUnavailableError` (ADR-038): a
    caller relying on `"vercel"` for its isolation guarantees must find out
    immediately (missing SDK package, missing credentials, or the Sandbox
    API itself refusing the request) rather than silently running with a
    weaker sandbox.
    """


def _missing_credential_env_vars() -> list[str]:
    return [name for name in _REQUIRED_CREDENTIAL_ENV_VARS if not os.environ.get(name)]


def execute_in_vercel_sandbox(
    code: str,
    dfs: dict[str, Any],
    *,
    mode: str,
    entry_point: str,
    result_vars: list[str],
    extra_globals: Optional[dict[str, Any]],
    extra_modules: Optional[dict[str, str]],
    extra_builtins: Optional[dict[str, Any]],
    timeout_seconds: int,
) -> SandboxResult:
    """Run one `execute_in_sandbox()` call inside a Vercel Sandbox microVM.

    Same `SandboxResult` shape and semantics as the "process"/"docker"
    backends: `timed_out=True` on the wall-clock/`kill_after` budget being
    exceeded, a plain-dict `error` message on failure, and `values` always a
    picklable, already-collected dict.
    """
    if not _VERCEL_SDK_AVAILABLE:
        raise VercelSandboxUnavailableError(
            "AI_ETL_SANDBOX_BACKEND=vercel (or backend='vercel') requires the "
            "'vercel' Python package ('uv add vercel', or install this "
            "project's 'vercel-sandbox' extra). See ADR-039."
        )

    missing = _missing_credential_env_vars()
    if missing:
        raise VercelSandboxUnavailableError(
            "AI_ETL_SANDBOX_BACKEND=vercel (or backend='vercel') requires "
            f"{', '.join(_REQUIRED_CREDENTIAL_ENV_VARS)} to be set (missing: "
            f"{', '.join(missing)}) when running off a Vercel deployment "
            "(this project runs on Railway, so OIDC auto-auth never applies "
            "here). See ADR-039 and .env.example."
        )

    image = os.environ.get(SANDBOX_IMAGE_ENV_VAR, DEFAULT_SANDBOX_IMAGE)
    vcpus = int(os.environ.get("AI_ETL_SANDBOX_VERCEL_VCPUS", _DEFAULT_VCPUS))
    memory = int(os.environ.get("AI_ETL_SANDBOX_VERCEL_MEMORY_MB", _DEFAULT_MEMORY_MB))

    payload = pickle.dumps(  # nosemgrep: python.lang.security.deserialization.pickle.avoid-pickle -- see note at the bottom of this file
        {
            "code": code,
            "dfs": dfs,
            "mode": mode,
            "entry_point": entry_point,
            "result_vars": result_vars,
            "extra_globals": extra_globals,
            "extra_modules": extra_modules,
            "extra_builtins": extra_builtins,
        }
    )

    run_id = uuid.uuid4().hex
    input_path = f"/tmp/{run_id}-payload.pkl"  # nosec B108 -- the sandbox's own /tmp, not a host path
    output_path = f"/tmp/{run_id}-result.pkl"  # nosec B108 -- ditto

    start = time.monotonic()
    try:
        with _vercel_sandbox_module.create_sandbox(
            image=image,
            resources=SandboxResources(vcpus=vcpus, memory=memory),
            # No network reachable from sandboxed code at all — matches the
            # Docker backend's `--network none`.
            network_policy=NetworkPolicy.deny_all(),
            execution_time_limit=timeout_seconds + _SESSION_LIMIT_SLACK_SECONDS,
            # One-shot ephemeral execution, not a dev environment — no
            # snapshotting, no persistence between calls.
            persistent=False,
            # Deliberately NOT set: no host env vars are ever forwarded into
            # the sandbox. The runner script needs none — everything it
            # needs arrives via the payload file.
        ) as box:
            box.fs.write_bytes(input_path, payload)

            result = box.run_process(
                "python",
                ["/app/run_sandboxed_vercel.py", input_path, output_path],
                kill_after=timeout_seconds,
                capture_output=True,
                check=False,
            )

            duration = time.monotonic() - start

            if result.returncode != 0:
                # A nonzero exit this close to the requested budget is the
                # kill_after SIGKILL firing (same "the thing itself must be
                # stopped, not just stop waiting on it" outcome ADR-007/
                # ADR-038 document — here Vercel's own server-side kill_after
                # is the mechanism, not a `docker kill`/SIGTERM we send
                # ourselves). A nonzero exit well under the budget is a real
                # crash in the runner script instead.
                timed_out = duration >= timeout_seconds - 1
                if timed_out:
                    return SandboxResult(
                        values={},
                        error=f"Execution exceeded {timeout_seconds}s — simplify the computation",
                        timed_out=True,
                        duration_seconds=duration,
                    )
                stderr = result.stderr or ""
                return SandboxResult(
                    values={},
                    error=(
                        f"Sandboxed Vercel process exited with code "
                        f"{result.returncode}: {stderr[-4000:]}"
                    ),
                    timed_out=False,
                    duration_seconds=duration,
                )

            try:
                result_bytes = box.fs.read_bytes(output_path)
            except Exception as e:
                return SandboxResult(
                    values={},
                    error=f"Failed to read sandboxed Vercel result file: {e}",
                    timed_out=False,
                    duration_seconds=duration,
                )
    except (
        SandboxApiError,
        SandboxCredentialsError,
        SandboxTerminalStateError,
        SandboxTimeoutError,
        SandboxError,
    ) as e:
        # The Sandbox API/service itself failed (bad credentials, image not
        # found/not ready, quota, transient outage, ...) — this is a backend
        # availability problem, not a sandboxed-code error, so it fails
        # closed the same way missing credentials/the SDK package do above,
        # rather than being reported as an ordinary `SandboxResult` error.
        raise VercelSandboxUnavailableError(
            f"Vercel Sandbox request failed: {type(e).__name__}: {e}"
        ) from e

    try:
        response: dict[str, Any] = pickle.loads(result_bytes)  # nosec B301  # nosemgrep: python.lang.security.deserialization.pickle.avoid-pickle -- see note at bottom
    except Exception as e:
        return SandboxResult(
            values={},
            error=f"Failed to decode sandboxed Vercel output: {e}",
            timed_out=False,
            duration_seconds=duration,
        )

    return SandboxResult(
        values=response.get("values", {}),
        error=response.get("error"),
        timed_out=False,
        duration_seconds=duration,
    )


# Note on `pickle.loads` (S301/B301, "possible security issue"): the bytes
# decoded here are this project's own `run_sandboxed_vercel.py` output,
# produced by a sandbox this same function just created for this exact call,
# read back via `box.fs.read_bytes()` from a path this function generated
# with `uuid.uuid4()` — not attacker-controlled network input. The untrusted
# part of this whole call (`code`) never gets pickled or unpickled anywhere
# in this file; it is passed as a plain string inside the request payload
# and only ever reaches Python's restricted `exec()` inside the sandbox, via
# `core/sandbox.py::_execute_code` — the same reviewed call site every other
# backend uses.
