"""Integration tests for the Vercel Sandbox production backend (ADR-039).

Exercises `core/sandbox_vercel.py::execute_in_vercel_sandbox()` (and
`execute_in_sandbox(..., backend="vercel")`) against a **real** Vercel
Sandbox — not mocks, since the whole point of ADR-039 is the isolation a
real Firecracker microVM boundary provides.

Skipped automatically unless all of the following hold, matching this
project's existing "real infrastructure, self-skipping" convention
(test_mysql_source_real.py / test_mongodb_source_real.py /
test_sandbox_docker.py):
  1. the `vercel` Python package is installed (`uv add vercel`, or this
     project's `vercel-sandbox` extra);
  2. VERCEL_TOKEN, VERCEL_TEAM_ID, and VERCEL_PROJECT_ID are all set; and
  3. the `ai-etl-sandbox:latest` custom image has been pushed to that
     project's Vercel Container Registry (`make sandbox-vcr-image`).

As of this ADR being written, none of the three held in this development
environment (no `VERCEL_TOKEN`/`VERCEL_TEAM_ID`/`VERCEL_PROJECT_ID` in the
shell, and the `vercel` package is not installed) — these tests are
expected to skip here and in CI (which never builds/pushes the VCR image
either) until someone runs them with real Vercel credentials and a pushed
image.
"""

import os
import time

import pandas as pd
import pytest

_VERCEL_SDK_AVAILABLE = True
try:
    import vercel.sandbox  # noqa: F401
except ImportError:
    _VERCEL_SDK_AVAILABLE = False

_HAS_CREDENTIALS = all(
    os.environ.get(name) for name in ("VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID")
)

_VERCEL_READY = _VERCEL_SDK_AVAILABLE and _HAS_CREDENTIALS

pytestmark = pytest.mark.skipif(
    not _VERCEL_READY,
    reason=(
        "Vercel Sandbox not configured: requires the 'vercel' package "
        "installed and VERCEL_TOKEN/VERCEL_TEAM_ID/VERCEL_PROJECT_ID set "
        "(plus the ai-etl-sandbox:latest image pushed via "
        "`make sandbox-vcr-image`) to run these tests for real. See ADR-039."
    ),
)


def _import_execute_in_sandbox():
    # Imported lazily, inside the (possibly skipped) test module body rather
    # than at collection time, so a missing `vercel` package never breaks
    # collection for every other test file in the suite.
    from ai_etl.core.sandbox import execute_in_sandbox

    return execute_in_sandbox


# ---------------------------------------------------------------------------
# Contract parity with the "process"/"docker" backends — same inputs, same
# outputs.
# ---------------------------------------------------------------------------


def test_valid_transform_returns_dataframe_via_vercel_backend() -> None:
    execute_in_sandbox = _import_execute_in_sandbox()
    code = """
def transform(dfs):
    df = dfs["orders"].copy()
    df["total"] = df["price"] * df["qty"]
    return df
"""
    dfs = {"orders": pd.DataFrame({"price": [10.0, 20.0], "qty": [2, 3]})}
    result = execute_in_sandbox(code, dfs, backend="vercel", timeout_seconds=90)

    assert result["error"] is None, result["error"]
    assert result["timed_out"] is False
    out = result["values"]["result"]
    assert list(out["total"]) == [20.0, 60.0]


def test_script_mode_round_trip_via_vercel_backend() -> None:
    execute_in_sandbox = _import_execute_in_sandbox()
    code = """
summary = {"total": 6, "labels": ["a", "b", "c"]}
"""
    result = execute_in_sandbox(
        code,
        {},
        mode="script",
        result_vars=["summary"],
        backend="vercel",
        timeout_seconds=90,
    )

    assert result["error"] is None, result["error"]
    assert result["values"]["summary"] == {"total": 6, "labels": ["a", "b", "c"]}


def test_syntax_error_returns_error_message_via_vercel_backend() -> None:
    execute_in_sandbox = _import_execute_in_sandbox()
    code = "def transform(dfs):\n    return dfs['x'  # missing bracket"
    result = execute_in_sandbox(code, {}, backend="vercel", timeout_seconds=90)

    assert result["values"] == {}
    assert result["error"] is not None
    assert "SyntaxError" in result["error"]


def test_dangerous_builtins_still_blocked_via_vercel_backend() -> None:
    """Same restricted-globals guarantee as every other backend — the
    Vercel backend calls the identical `_execute_code()` helper."""
    execute_in_sandbox = _import_execute_in_sandbox()
    code = "result = open('/etc/passwd')"
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["result"], backend="vercel", timeout_seconds=90
    )

    assert result["error"] is not None
    assert "not defined" in result["error"]


def test_timeout_kills_hung_sandbox_process() -> None:
    execute_in_sandbox = _import_execute_in_sandbox()
    code = "while True:\n    pass\n"

    start = time.monotonic()
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=[], backend="vercel", timeout_seconds=5
    )
    wall_clock = time.monotonic() - start

    assert result["timed_out"] is True
    assert result["values"] == {}
    assert result["error"] is not None
    assert "Execution exceeded" in result["error"]
    # 5s timeout + sandbox creation/kill_after overhead — generous but still
    # a real regression guard against a broken kill path hanging.
    assert wall_clock < 60, f"timeout enforcement took {wall_clock}s, expected well under 60s"


# ---------------------------------------------------------------------------
# The actual point of ADR-039 (same as ADR-038 for the Docker backend): the
# introspection bypass ADR-032 Decision 4 documented as unmitigated is
# demonstrated as *contained* by the Firecracker microVM boundary, not
# merely asserted in prose. Reuses the exact gadget and assertions from
# test_sandbox_docker.py's containment tests, against the real Vercel
# backend instead of a local Docker container.
# ---------------------------------------------------------------------------

_OS_GADGET_PREAMBLE = """
os_module = None
for cls in ().__class__.__mro__[1].__subclasses__():
    g = getattr(cls.__init__, "__globals__", None)
    if g and "os" in g:
        os_module = g["os"]
        break
"""


def test_introspection_bypass_cannot_reach_network() -> None:
    """The documented `__mro__`/`__subclasses__()` escape can still reach a
    real `socket` module reference inside the sandbox (Python-level
    restriction does not stop it) — but `NetworkPolicy.deny_all()` should
    stop the connection itself, a kernel/hypervisor-enforced boundary the
    "process" backend never had."""
    execute_in_sandbox = _import_execute_in_sandbox()
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
        code, {}, mode="script", result_vars=["result"], backend="vercel", timeout_seconds=90
    )

    assert result["values"] == {}
    assert result["error"] is not None


def test_introspection_bypass_env_is_empty_not_host_secrets() -> None:
    """No host env vars are ever passed via the sandbox's `env=` kwarg —
    even a successful `os` gadget reach should find no host secret like
    OPENAI_API_KEY, matching the Docker backend's equivalent guarantee."""
    execute_in_sandbox = _import_execute_in_sandbox()
    code = (
        _OS_GADGET_PREAMBLE
        + """
result = dict(os_module.environ)
"""
    )
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["result"], backend="vercel", timeout_seconds=90
    )

    assert result["error"] is None, result["error"]
    env = result["values"]["result"]
    secret_shaped_keys = [
        k
        for k in env
        if any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL"))
    ]
    assert secret_shaped_keys == [], f"unexpected secret-shaped env vars leaked into sandbox: {env}"
