"""Unit tests for the sandbox module (ADR-007 unified sandbox + enforced timeout).

`execute_in_sandbox()` runs the sandboxed code in a real `multiprocessing.Process`
(spawn context) — these tests exercise the actual process boundary (real pickling
across processes, real SIGTERM/SIGKILL timeout enforcement via join/terminate/kill),
not mocks, since that boundary is exactly what ADR-007 introduced and is the part
of this change with the least prior empirical coverage (see ADR-007 and the
Sprint 2 backend-agent handoff notes). This module cannot be executed in this
project's interactive dev sandbox — `multiprocessing`/pandas execution hangs there
— so CI is the first real run of everything in this file.
"""

import os
import time

import pandas as pd
import pytest

from ai_etl.core.sandbox import (
    LARGE_DATASET_ROW_THRESHOLD,
    LARGE_DATASET_TIMEOUT_MULTIPLIER,
    SAFE_BUILTINS,
    execute_in_sandbox,
    scale_timeout_for_rows,
)

# ---------------------------------------------------------------------------
# mode="function" (Transformer contract) — rewritten for the SandboxResult
# TypedDict return shape (was a `(result, error)` tuple before ADR-007).
# ---------------------------------------------------------------------------


def test_valid_transform_returns_dataframe() -> None:
    code = """
def transform(dfs):
    df = dfs["orders"].copy()
    df["total"] = df["price"] * df["qty"]
    return df
"""
    dfs = {"orders": pd.DataFrame({"price": [10.0, 20.0], "qty": [2, 3]})}
    result = execute_in_sandbox(code, dfs)

    assert result["error"] is None
    assert result["timed_out"] is False
    out = result["values"]["result"]
    assert "total" in out.columns
    assert list(out["total"]) == [20.0, 60.0]


def test_syntax_error_returns_error_message() -> None:
    code = "def transform(dfs):\n    return dfs['x'  # missing bracket"
    result = execute_in_sandbox(code, {})

    assert result["values"] == {}
    assert result["error"] is not None
    assert "SyntaxError" in result["error"]
    assert result["timed_out"] is False


def test_runtime_error_returns_error_message() -> None:
    code = """
def transform(dfs):
    return dfs["nonexistent_key"]
"""
    result = execute_in_sandbox(code, {"orders": pd.DataFrame()})

    assert result["values"] == {}
    assert result["error"] is not None


def test_missing_transform_function_returns_error() -> None:
    code = "x = 1 + 1"
    result = execute_in_sandbox(code, {})

    assert result["values"] == {}
    assert result["error"] is not None
    assert "transform" in result["error"]


def test_non_dataframe_return_returns_error() -> None:
    code = """
def transform(dfs):
    return [1, 2, 3]
"""
    result = execute_in_sandbox(code, {})

    assert result["values"] == {}
    assert result["error"] is not None
    assert "pd.DataFrame" in result["error"]


def test_blocked_imports_raise_error() -> None:
    code = """
def transform(dfs):
    import os
    return dfs.get("x", __import__("pandas").DataFrame())
"""
    result = execute_in_sandbox(code, {})

    assert result["values"].get("result") is None or result["error"] is not None


# ---------------------------------------------------------------------------
# mode="script" (Analyst/Science contract) — new coverage, not previously
# exercised by this file at all.
# ---------------------------------------------------------------------------


def test_script_mode_round_trip_returns_named_vars() -> None:
    """A dict and a small non-trivial object (a pandas Series, so it exercises
    real cross-process pickling of a non-builtin type, same as gold_df/fig would)
    both come back correctly through `result_vars`."""
    code = """
summary = {"total": 6, "labels": ["a", "b", "c"]}
series = pd.Series([1, 2, 3], name="values")
"""
    result = execute_in_sandbox(code, {}, mode="script", result_vars=["summary", "series"])

    assert result["error"] is None, result["error"]
    assert result["timed_out"] is False
    assert result["values"]["summary"] == {"total": 6, "labels": ["a", "b", "c"]}
    assert list(result["values"]["series"]) == [1, 2, 3]
    assert result["values"]["series"].name == "values"


def test_script_mode_exposes_dfs_keys_directly_as_globals() -> None:
    """`dfs`' keys (typically `{"df": ...}`) are exposed directly as globals to
    script-mode code, matching what the Analyst/Science prompts assume."""
    code = "shape = list(df.shape)"
    dfs = {"df": pd.DataFrame({"a": [1, 2, 3]})}
    result = execute_in_sandbox(code, dfs, mode="script", result_vars=["shape"])

    assert result["error"] is None, result["error"]
    assert result["values"]["shape"] == [3, 1]


# Module-level so it pickles by reference (qualified name) under the spawn
# context — the same way science.py's sklearn/statsmodels classes cross the
# process boundary via `extra_globals` (ADR-007). Whole *module* objects
# (plotly, math, os) don't pickle at all and must go through `extra_modules`
# instead (imported by dotted path inside the child) — see
# core/sandbox.py's execute_in_sandbox() docstring.
class _FakeExtraModule:
    LABEL = "extra-global-marker"

    @staticmethod
    def make(value: int) -> dict:
        return {"from_extra_global": value}


def _double(x: int) -> int:
    """Module-level (not a lambda) so it's picklable by reference — a lambda
    cannot be pickled, and using one here would fail for a reason unrelated to
    what this test is checking."""
    return x * 2


def test_extra_globals_are_available_to_executed_code() -> None:
    code = """
marker = fake_mod.LABEL
built = fake_mod.make(42)
"""
    result = execute_in_sandbox(
        code,
        {},
        mode="script",
        result_vars=["marker", "built"],
        extra_globals={"fake_mod": _FakeExtraModule},
    )

    assert result["error"] is None, result["error"]
    assert result["values"]["marker"] == "extra-global-marker"
    assert result["values"]["built"] == {"from_extra_global": 42}


def test_extra_builtins_are_available_to_executed_code() -> None:
    code = "doubled = my_double(21)"
    result = execute_in_sandbox(
        code,
        {},
        mode="script",
        result_vars=["doubled"],
        extra_builtins={"my_double": _double},
    )

    assert result["error"] is None, result["error"]
    assert result["values"]["doubled"] == 42


# ---------------------------------------------------------------------------
# Timeout enforcement — a hung child must actually be killed, not just have
# the parent stop waiting for it (ADR-007 Question 2).
# ---------------------------------------------------------------------------


def test_timeout_kills_hung_process() -> None:
    """A script that never returns must come back as timed_out=True within a
    bounded wall-clock time. If timeout enforcement regresses (e.g. back to a
    no-op), this test hangs/fails loudly instead of silently passing."""
    code = "while True:\n    pass\n"

    start = time.monotonic()
    result = execute_in_sandbox(code, {}, mode="script", result_vars=[], timeout_seconds=1)
    wall_clock = time.monotonic() - start

    assert result["timed_out"] is True
    assert result["values"] == {}
    assert result["error"] is not None
    assert "Execution exceeded" in result["error"]
    # 1s timeout + a short SIGTERM grace period + spawn/join overhead. Generous
    # on purpose so this isn't flaky on a loaded CI runner, but still a real
    # regression guard: a broken timeout (e.g. reverted to no-op) would hang
    # well past this instead of merely running a bit slow.
    assert wall_clock < 10, f"timeout enforcement took {wall_clock}s wall-clock, expected < 10s"


# ---------------------------------------------------------------------------
# The pipe-buffer-deadlock risk flagged by backend-agent-sprint2 / ADR-007:
# multiprocessing.Queue is backed by an OS pipe with a bounded (~64KB) buffer.
# If the parent only drains the queue *after* process.join() returns, a child
# blocked on queue.put() for a result larger than the pipe buffer deadlocks
# until the join timeout fires — which would show up here as a false
# timed_out=True for a call that actually completed correctly and quickly.
# ---------------------------------------------------------------------------


def test_large_result_does_not_falsely_report_timeout() -> None:
    """UNVERIFIED LOCALLY: this project's dev sandbox cannot run multiprocessing,
    so this test has never actually executed before CI. If it fails with
    timed_out=True (or hangs until the 30s timeout), that is the real
    pipe-buffer-deadlock bug ADR-007 flagged — do not silently "fix" this test
    to tolerate it; report it (see ADR-007 / core/sandbox.py's
    `execute_in_sandbox()` docstring) so the parent can be changed to drain the
    result queue with a background thread started before/while joining, rather
    than strictly after.
    """
    n_rows = 300_000
    code = f"big = pd.DataFrame({{'a': range({n_rows}), 'b': range({n_rows})}})"

    result = execute_in_sandbox(code, {}, mode="script", result_vars=["big"], timeout_seconds=30)

    assert result["error"] is None, result["error"]
    assert result["timed_out"] is False, (
        "Large result incorrectly reported as timed_out=True — this is the "
        "pipe-buffer-deadlock risk ADR-007 flagged, not a real hang. Report it; "
        "do not adjust this assertion to hide it."
    )
    out = result["values"]["big"]
    assert len(out) == n_rows
    assert list(out.columns) == ["a", "b"]
    assert out["a"].iloc[-1] == n_rows - 1


# ---------------------------------------------------------------------------
# SAFE_BUILTINS restriction — still holds post-refactor, and analyst.py/
# science.py's actual call shape (extra_builtins=None) still blocks setattr.
# ---------------------------------------------------------------------------


def test_safe_builtins_excludes_dangerous_symbols() -> None:
    for name in ("open", "__import__", "eval", "exec", "compile", "setattr", "vars"):
        assert name not in SAFE_BUILTINS, f"{name!r} must not be in SAFE_BUILTINS"


@pytest.mark.parametrize(
    "dangerous_call",
    [
        "open('whatever')",
        "__import__('os')",
        "eval('1')",
        "exec('1')",
        "compile('1', '<s>', 'eval')",
    ],
)
def test_dangerous_builtins_raise_inside_sandbox(dangerous_call: str) -> None:
    code = f"result = {dangerous_call}"
    result = execute_in_sandbox(code, {}, mode="script", result_vars=["result"])

    assert result["error"] is not None
    assert "not defined" in result["error"]


def test_setattr_unavailable_for_analyst_science_extra_builtins_config() -> None:
    """ADR-007's Question 1 tightening: analyst.py/science.py pass no extra
    builtins beyond the shared SAFE_BUILTINS base (`setattr`/`vars`/`iter`/`next`/
    `repr`/`format`/`slice` dropped as unused). This mirrors that exact call
    shape (extra_globals present, like Plotly's px/go; extra_builtins=None) and
    confirms a script attempting `setattr` fails inside the sandbox rather than
    silently succeeding."""
    code = "result = setattr"
    result = execute_in_sandbox(
        code,
        {},
        mode="script",
        result_vars=["result"],
        extra_globals={"marker": "px-and-go-stand-in"},
        extra_builtins=None,
    )

    assert result["error"] is not None
    assert "setattr" in result["error"]
    assert "not defined" in result["error"]


# ---------------------------------------------------------------------------
# Env-var exposure — the child inherits the parent's full `os.environ` under
# `spawn` (spawn does not clear it), and this project keeps real secrets
# there (APP_DATABASE_URL, POSTGRES_URL, CLERK_JWKS_URL/CLERK_ISSUER,
# OPENAI_API_KEY-style vars). `_sandbox_worker()` now clears `os.environ` as
# its first line, before user code runs, specifically so that even the
# documented introspection escape (`().__class__.__mro__[1].__subclasses__()`
# reaching an already-imported `os` module's globals) finds nothing there.
#
# These tests don't exercise that exact mro/subclasses escape path — which
# module happens to be reachable that way is a fragile implementation detail
# of whatever's been imported by pandas/plotly/sklearn at the time, not a
# stable thing to assert on. Instead they inject the real `os` module via
# `extra_modules` (the same mechanism analyst.py/science.py use for
# `px`/`go`/`math` — modules are imported by dotted path inside the child,
# not pickled across the process boundary, since whole module objects can't
# be pickled at all) as a stable proxy: however sandboxed code ends up
# holding a reference to `os` — via the accepted escape or (as here) a
# legitimate extra_modules entry — `os.environ` must already be empty by the
# time it can use it, which is exactly the property `_sandbox_worker()`'s
# env-clear is supposed to guarantee.
# ---------------------------------------------------------------------------


def test_child_process_environment_is_cleared_before_user_code_runs() -> None:
    """Baseline: the child's `os.environ` is empty (or at least does not
    contain any of the parent's own vars), proving the clear happens
    before user code — and inside the child, not the parent, since this
    process's own `os.environ` (checked below) is untouched."""
    code = "env_len = len(os.environ)"
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["env_len"], extra_modules={"os": "os"}
    )

    assert result["error"] is None, result["error"]
    assert result["values"]["env_len"] == 0

    # The clear happened in the child's copy of os.environ, not the parent's
    # (this test process) — this process's own environment must be untouched.
    assert len(os.environ) > 0


def test_injected_parent_env_var_not_visible_in_sandboxed_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secret-shaped var set in the parent (this test process) right before
    the call — analogous to APP_DATABASE_URL/OPENAI_API_KEY being real,
    populated env vars in the running app — must not be visible inside the
    sandboxed child, even though `spawn` would otherwise inherit it."""
    monkeypatch.setenv("AI_ETL_TEST_SECRET_MARKER", "should-not-leak-into-sandbox")

    code = "leaked = os.environ.get('AI_ETL_TEST_SECRET_MARKER')"
    result = execute_in_sandbox(
        code, {}, mode="script", result_vars=["leaked"], extra_modules={"os": "os"}
    )

    assert result["error"] is None, result["error"]
    assert result["values"]["leaked"] is None
    # Confirm the marker really was set in the parent at call time — otherwise
    # this test would trivially pass for the wrong reason.
    assert os.environ.get("AI_ETL_TEST_SECRET_MARKER") == "should-not-leak-into-sandbox"


# ---------------------------------------------------------------------------
# scale_timeout_for_rows() (ADR-012, Sprint 12 scale profiling) — real
# profiling against a 200k-row benchmark confirmed the fixed per-call timeout
# doesn't scale with input size; see docs/adr/ADR-012-scale-strategy.md and
# docs/work/2026-08-19-sprint12-scale-profiling.md for the measurements.
# ---------------------------------------------------------------------------


def test_scale_timeout_unchanged_below_threshold() -> None:
    """Every existing case-study scenario (max 10k rows) must see identical
    behavior to before this sprint — no-op below the threshold."""
    assert scale_timeout_for_rows(30, 10_000) == 30
    assert scale_timeout_for_rows(15, LARGE_DATASET_ROW_THRESHOLD) == 15


def test_scale_timeout_doubles_above_threshold() -> None:
    result = scale_timeout_for_rows(20, LARGE_DATASET_ROW_THRESHOLD + 1)
    assert result == 20 * LARGE_DATASET_TIMEOUT_MULTIPLIER


def test_scale_timeout_at_benchmark_scale() -> None:
    """The real scale this sprint measured against (~204k rows)."""
    assert scale_timeout_for_rows(20, 204_000) == 40
    assert scale_timeout_for_rows(15, 204_000) == 30
    assert scale_timeout_for_rows(30, 204_000) == 60


def test_scale_timeout_zero_rows_unchanged() -> None:
    assert scale_timeout_for_rows(30, 0) == 30
