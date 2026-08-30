"""Integration tests for the Docker sandbox backend (ADR-038).

Exercises `core/sandbox_docker.py::execute_in_docker_sandbox()` (and
`execute_in_sandbox(..., backend="docker")`) against a real, locally built
`ai-etl-sandbox:latest` image and a real Docker daemon — not mocks, since the
whole point of ADR-038 is the isolation a real container boundary provides,
which nothing about the process-boundary "process" backend tests
(`test_sandbox.py`) exercises.

Skipped automatically when Docker isn't reachable or the sandbox image
hasn't been built (`make sandbox-image`), matching this project's existing
"real infrastructure, self-skipping" convention
(test_mysql_source_real.py/test_mongodb_source_real.py) — CI does not build
this image (ADR-038: this backend is dev/local-verified, Railway rollout is
explicit follow-up work), so these tests are expected to skip in CI and run
for real in local development once the image is built.
"""

import subprocess
import time

import pandas as pd
import pytest

from ai_etl.core.sandbox import execute_in_sandbox
from ai_etl.core.sandbox_docker import DEFAULT_SANDBOX_IMAGE, _docker_available


def _sandbox_image_built() -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", DEFAULT_SANDBOX_IMAGE],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_DOCKER_READY = _docker_available() and _sandbox_image_built()

pytestmark = pytest.mark.skipif(
    not _DOCKER_READY,
    reason=(
        f"Docker daemon not reachable or '{DEFAULT_SANDBOX_IMAGE}' image not built; "
        "run `make sandbox-image` (requires local Docker) to enable these tests."
    ),
)


# ---------------------------------------------------------------------------
# Contract parity with the "process" backend — same inputs, same outputs.
# ---------------------------------------------------------------------------


def test_valid_transform_returns_dataframe_via_docker_backend() -> None:
    code = """
def transform(dfs):
    df = dfs["orders"].copy()
    df["total"] = df["price"] * df["qty"]
    return df
"""
    dfs = {"orders": pd.DataFrame({"price": [10.0, 20.0], "qty": [2, 3]})}
    result = execute_in_sandbox(code, dfs, backend="docker", timeout_seconds=60)

    assert result["error"] is None, result["error"]
    assert result["timed_out"] is False
    out = result["values"]["result"]
    assert list(out["total"]) == [20.0, 60.0]


def test_script_mode_round_trip_via_docker_backend() -> None:
    code = """
summary = {"total": 6, "labels": ["a", "b", "c"]}
"""
    result = execute_in_sandbox(
        code,
        {},
        mode="script",
        result_vars=["summary"],
        backend="docker",
        timeout_seconds=60,
    )

    assert result["error"] is None, result["error"]
    assert result["values"]["summary"] == {"total": 6, "labels": ["a", "b", "c"]}


def test_syntax_error_returns_error_message_via_docker_backend() -> None:
    code = "def transform(dfs):\n    return dfs['x'  # missing bracket"
    result = execute_in_sandbox(code, {}, backend="docker", timeout_seconds=60)

    assert result["values"] == {}
    assert result["error"] is not None
    assert "SyntaxError" in result["error"]


def test_dangerous_builtins_still_blocked_via_docker_backend() -> None:
    """Same restricted-globals guarantee as the "process" backend — the
    Docker backend calls the identical `_execute_code()` helper, it does not
    relax SAFE_BUILTINS."""
    code = "result = open('/etc/passwd')"
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["result"], backend="docker", timeout_seconds=60
    )

    assert result["error"] is not None
    assert "not defined" in result["error"]


def test_timeout_kills_hung_container() -> None:
    code = "while True:\n    pass\n"

    start = time.monotonic()
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=[], backend="docker", timeout_seconds=3
    )
    wall_clock = time.monotonic() - start

    assert result["timed_out"] is True
    assert result["values"] == {}
    assert result["error"] is not None
    assert "Execution exceeded" in result["error"]
    # 3s timeout + docker kill + a short grace period — generous but still a
    # real regression guard against a reverted/broken kill path hanging.
    assert wall_clock < 30, f"timeout enforcement took {wall_clock}s, expected well under 30s"


# ---------------------------------------------------------------------------
# The actual point of ADR-038: the introspection bypass ADR-032 Decision 4
# documented as unmitigated is demonstrated as *contained* by the container
# boundary, not merely asserted in prose. Each test below reaches for the
# real os/socket module via the exact gadget named in ADR-032/ADR-003/
# ADR-007 (`().__class__.__mro__[1].__subclasses__()`), then attempts a
# concrete host-reaching action through it — proving the kernel-enforced
# container boundary (not Python-level restriction) is what stops it.
# ---------------------------------------------------------------------------

_OS_GADGET_PREAMBLE = """
os_module = None
for cls in ().__class__.__mro__[1].__subclasses__():
    g = getattr(cls.__init__, "__globals__", None)
    if g and "os" in g:
        os_module = g["os"]
        break
"""


def test_introspection_bypass_cannot_write_host_filesystem() -> None:
    """The documented `__mro__`/`__subclasses__()` escape can still reach a
    real `os` module reference inside the container (Python-level
    restriction does not stop it, matching ADR-032's own finding) — but the
    container's `--read-only` root filesystem stops the write itself, a
    kernel-enforced boundary the "process" backend never had."""
    code = (
        _OS_GADGET_PREAMBLE
        + """
fd = os_module.open("/app/pwned", os_module.O_WRONLY | os_module.O_CREAT)
os_module.write(fd, b"pwned")
os_module.close(fd)
result = "fs-write-succeeded"
"""
    )
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["result"], backend="docker", timeout_seconds=60
    )

    # The write itself raises inside the sandboxed exec(); _execute_code's
    # own exception handling surfaces it as `error`, not a silent success.
    assert result["values"] == {}
    assert result["error"] is not None
    assert "Read-only file system" in result["error"]


def test_introspection_bypass_cannot_reach_network() -> None:
    """Same escape, reaching for `socket` instead of `os` this time, to
    attempt an outbound connection — blocked by `--network none`, not by
    anything Python-level."""
    code = """
socket_module = None
for cls in ().__class__.__mro__[1].__subclasses__():
    g = getattr(cls.__init__, "__globals__", None)
    if g and "socket" in g:
        socket_module = g["socket"]
        break
s = socket_module.create_connection(("8.8.8.8", 53), timeout=3)
s.close()
result = "connected"
"""
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["result"], backend="docker", timeout_seconds=60
    )

    assert result["values"] == {}
    assert result["error"] is not None
    assert "Network is unreachable" in result["error"] or "unreachable" in result["error"].lower()


def test_introspection_bypass_env_is_empty_not_host_secrets() -> None:
    """The container is started with no `-e`/`--env-file` at all (stronger
    than the "process" backend's `os.environ.clear()`) — even a successful
    `os` gadget reach finds an empty/minimal environment, never a host
    secret like OPENAI_API_KEY."""
    code = (
        _OS_GADGET_PREAMBLE
        + """
result = dict(os_module.environ)
"""
    )
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["result"], backend="docker", timeout_seconds=60
    )

    assert result["error"] is None, result["error"]
    env = result["values"]["result"]
    secret_shaped_keys = [
        k
        for k in env
        if any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL"))
    ]
    assert secret_shaped_keys == [], (
        f"unexpected secret-shaped env vars leaked into container: {env}"
    )
