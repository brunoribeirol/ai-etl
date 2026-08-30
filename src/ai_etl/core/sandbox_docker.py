"""Docker-isolated sandbox backend for `execute_in_sandbox()` (ADR-038).

Runs LLM-generated code inside a `docker run` container instead of a
`multiprocessing.Process` child, closing the gap ADR-032 Decision 4 accepted
as an unmitigated risk: `exec()` with restricted globals can be bypassed via
introspection (`().__class__.__mro__[1].__subclasses__()`) to reach
arbitrary code execution *within* the process boundary. A real OS container
contains that arbitrary code execution behind kernel-enforced boundaries the
"process" backend never had: no network (`--network none`), a read-only root
filesystem, no Linux capabilities (`--cap-drop ALL`), no privilege escalation
(`--security-opt no-new-privileges`), a non-root user, and hard memory/CPU/
process-count ceilings — instead of relying only on restricted `__builtins__`
and a cleared `os.environ`.

**Not the production default.** This project deploys to Railway
(`railway.json` — `DOCKERFILE` builder), whose containers are documented as
non-privileged: Railway's own docs state that Docker-in-Docker (building or
running a nested container from inside an already-containerized service)
does not work there (see ADR-038's "Railway feasibility" section for the
investigation). Calling `docker run` from inside the Railway-deployed API/
worker process is therefore not expected to work in that environment. This
backend is dev/local-verified (opt in with `AI_ETL_SANDBOX_BACKEND=docker`,
requires a local Docker daemon and the image built via `make sandbox-image`)
with a separate, out-of-process sandboxing service tracked as the production
follow-up — see ADR-038.

Payload transport: the request (code, dfs, mode, entry_point, result_vars,
extra_globals, extra_modules, extra_builtins) and the response (values,
error) are pickled and piped over the container's stdin/stdout — the exact
same values that already cross the `multiprocessing.Process` "spawn"
boundary in `core/sandbox.py` today via pickling, just carried over a pipe to
a container instead of to a child process. No volume mount is used for the
payload itself, so no host path is ever exposed inside the container.
"""

import os
import pickle  # nosec B403 — see the two module-level notes at the bottom of this file
import shutil
import subprocess  # nosec B404 — every call below uses a fixed argv list, never shell=True
import time
import uuid
from typing import Any, Optional

from ai_etl.core.sandbox import SandboxResult

# Overridable for tests / alternate local image tags; the image `make
# sandbox-image` builds from docker/sandbox/Dockerfile.
SANDBOX_IMAGE_ENV_VAR = "AI_ETL_SANDBOX_DOCKER_IMAGE"
DEFAULT_SANDBOX_IMAGE = "ai-etl-sandbox:latest"

# Same grace-period pattern as core/sandbox.py's _TERMINATE_GRACE_SECONDS:
# short on purpose, a container that ignores `docker kill` on timeout is
# already misbehaving and this only bounds how long that can delay the
# caller before the parent gives up waiting on the docker CLI process itself.
_KILL_GRACE_SECONDS = 5

_DEFAULT_MEMORY_LIMIT = "512m"
_DEFAULT_CPU_LIMIT = "1"
_DEFAULT_PIDS_LIMIT = "128"


class DockerSandboxUnavailableError(RuntimeError):
    """Raised when `backend="docker"` is requested but Docker isn't usable.

    Deliberately does NOT fall back to the `"process"` backend — an explicit
    opt-in into stronger isolation that silently downgrades to weaker
    isolation on a missing dependency would be worse than a loud failure
    (ADR-038): a caller relying on `"docker"` for its isolation guarantees
    must find out immediately, not discover later that every call had
    quietly been running with the weaker sandbox all along.
    """


def _docker_available() -> bool:
    """Best-effort check that the `docker` CLI can reach a live daemon."""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(  # nosec B603 B607 — fixed argv, no shell, "docker" resolved via PATH deliberately
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def execute_in_docker_sandbox(
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
    """Run one `execute_in_sandbox()` call inside a locked-down container.

    Same `SandboxResult` shape and semantics as the "process" backend:
    `timed_out=True` on a wall-clock timeout (the container is killed via
    `docker kill`, not merely stopped-waiting-on), a plain-dict `error`
    message on failure, and `values` always a picklable, already-collected
    dict — nothing about the container (its filesystem, its network
    namespace) is reachable from the caller once this returns.
    """
    if not _docker_available():
        raise DockerSandboxUnavailableError(
            "AI_ETL_SANDBOX_BACKEND=docker (or backend='docker') requires a "
            "reachable Docker daemon via the 'docker' CLI. Start Docker "
            "locally (or unset AI_ETL_SANDBOX_BACKEND to use the default "
            "'process' backend) and retry. See ADR-038."
        )

    image = os.environ.get(SANDBOX_IMAGE_ENV_VAR, DEFAULT_SANDBOX_IMAGE)
    memory_limit = os.environ.get("AI_ETL_SANDBOX_DOCKER_MEMORY", _DEFAULT_MEMORY_LIMIT)
    cpu_limit = os.environ.get("AI_ETL_SANDBOX_DOCKER_CPUS", _DEFAULT_CPU_LIMIT)

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

    container_name = f"ai-etl-sandbox-{uuid.uuid4().hex}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        container_name,
        # No network reachable from sandboxed code at all — closes the
        # "exfiltrate over HTTP" and "reach internal services" angles the
        # "process" backend never bounded.
        "--network",
        "none",
        # Root filesystem read-only; only /tmp is writable, and even that
        # cannot execute anything placed there or hold setuid binaries.
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=128m",  # nosec B108 — a `docker run` CLI arg naming the
        # container's own /tmp, not a host path this process reads/writes
        f"--memory={memory_limit}",
        f"--memory-swap={memory_limit}",  # no swap headroom beyond the memory cap
        f"--cpus={cpu_limit}",
        f"--pids-limit={_DEFAULT_PIDS_LIMIT}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        # Matches the Dockerfile's own USER, set again here so the isolation
        # doesn't depend on the image never being rebuilt without it.
        "--user",
        "65534:65534",
        # Deliberately NO `-e`/`--env-file` — the container's own minimal,
        # image-default environment is all sandboxed code ever sees, which is
        # a stronger guarantee than "process"'s os.environ.clear() (there is
        # no host env to clear here in the first place, and none is ever
        # passed in).
        image,
    ]

    start = time.monotonic()
    process = subprocess.Popen(  # nosec B603 — fixed argv built above, no shell, no user input
        cmd,  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args -- cmd is a fixed argv list built above from a hardcoded "docker run ..." template + this project's own resource-limit env vars, never external/attacker-controlled input
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        stdout, stderr = process.communicate(input=payload, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        # `docker kill` stops the *container* — killing the `docker run` CLI
        # process alone (process.kill()) would leave the container itself
        # running server-side, the container-boundary equivalent of the
        # pipe-buffer deadlock ADR-007 documents for the "process" backend:
        # the thing actually doing work must be stopped, not just the local
        # handle waiting on it.
        subprocess.run(  # nosec B603 B607 — fixed argv, no shell, container_name is our own uuid4
            ["docker", "kill", container_name],
            capture_output=True,
            timeout=_KILL_GRACE_SECONDS,
            check=False,
        )
        try:
            process.communicate(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        duration = time.monotonic() - start
        return SandboxResult(
            values={},
            error=f"Execution exceeded {timeout_seconds}s — simplify the computation",
            timed_out=True,
            duration_seconds=duration,
        )

    duration = time.monotonic() - start

    if process.returncode != 0:
        return SandboxResult(
            values={},
            error=(
                f"Sandboxed container exited with code {process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace')[-4000:]}"
            ),
            timed_out=False,
            duration_seconds=duration,
        )

    try:
        result: dict[str, Any] = pickle.loads(stdout)  # nosec B301  # nosemgrep: python.lang.security.deserialization.pickle.avoid-pickle -- see note at the bottom of this file
    except Exception as e:
        return SandboxResult(
            values={},
            error=f"Failed to decode sandboxed container output: {e}",
            timed_out=False,
            duration_seconds=duration,
        )

    return SandboxResult(
        values=result.get("values", {}),
        error=result.get("error"),
        timed_out=False,
        duration_seconds=duration,
    )


# Note on `pickle.loads` (S301/B301, "possible security issue"): the bytes
# decoded here are this project's own `run_sandboxed.py` output, produced by
# a container this same function just started for this exact call, read back
# over the same pipe pair `Popen` created — not attacker-controlled network
# input. The untrusted part of this whole call (`code`) never gets pickled
# or unpickled anywhere in this file; it is passed as a plain string inside
# the request payload and only ever reaches Python's restricted `exec()`
# inside the container, via `core/sandbox.py::_execute_code` — the same
# reviewed call site the "process" backend uses.
