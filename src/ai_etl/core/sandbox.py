"""Unified code execution sandbox with restricted globals and an enforced timeout.

Executes LLM-generated Python code in an isolated, time-bounded child process
(``multiprocessing.Process``, ``spawn`` context). This is the single ``exec()``
call site for the whole project — Transformer (``mode="function"``) and
Analyst/Science (``mode="script"``) both route through ``execute_in_sandbox()``
instead of keeping their own copies of the restricted-globals whitelist and
their own bare ``exec()`` call. See docs/adr/ADR-007-unified-sandbox-policy.md
for the design rationale (why a child process instead of ``signal.alarm()`` or
``ProcessPoolExecutor``, and why Analyst/Science's extra builtins were dropped).

pandas and numpy are available to every site; file I/O and network access are
not. Analyst/Science layer additional globals (Plotly, sklearn, statsmodels) on
top of this shared base via ``extra_globals``.

Security note: exec() with restricted globals can be bypassed via
introspection (e.g., ().__class__.__mro__[1].__subclasses__()).
This is an accepted limitation for the TCC scope with controlled datasets.
Production use would require a proper sandboxing solution (e.g., Docker).
See SECURITY.md and docs/adr/ADR-003-exec-sandbox.md / ADR-007 for the full
risk analysis.
"""

import multiprocessing
import queue
import time
import traceback
from typing import Any, Literal, Optional, TypedDict

import numpy as np
import pandas as pd

SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "all": all,
    "any": any,
    "print": print,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "hasattr": hasattr,
    "getattr": getattr,
    "type": type,
    "pow": pow,
    "divmod": divmod,
    "None": None,
    "True": True,
    "False": False,
}

SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": SAFE_BUILTINS,
    "pd": pd,
    "np": np,
}

# How long the parent waits, after SIGTERM, for the child to exit before
# escalating to SIGKILL. Short on purpose — a child that ignores SIGTERM is
# already misbehaving; this just bounds how long that can delay the caller.
_TERMINATE_GRACE_SECONDS = 2


class SandboxResult(TypedDict):
    """Outcome of one `execute_in_sandbox()` call.

    `values` is always a plain, already-collected dict — nothing about the
    child process (its locals, its exec() environment) is visible to the
    caller once the call returns, since the child runs in a separate process.
    """

    values: dict[str, Any]  # named results extracted from the executed code
    error: Optional[str]  # None on success
    timed_out: bool
    duration_seconds: float


def _run_function_mode(
    code: str,
    dfs: dict[str, Any],
    entry_point: str,
    sandbox_globals: dict[str, Any],
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Execute function-mode code: defines `entry_point(dfs) -> pd.DataFrame`.

    Preserves the Transformer's original transform(dfs) contract exactly.
    """
    local_env: dict[str, Any] = {"dfs": dfs}

    try:
        exec(code, sandbox_globals, local_env)  # noqa: S102
    except SyntaxError as e:
        return None, f"SyntaxError: {e}"
    except Exception as e:
        return None, f"Error defining {entry_point} function: {e}\n{traceback.format_exc()}"

    if entry_point not in local_env:
        return None, f"No function named '{entry_point}' found in generated code."

    try:
        result = local_env[entry_point](dfs)
    except Exception as e:
        return None, f"Error executing {entry_point}(dfs): {e}\n{traceback.format_exc()}"

    if not isinstance(result, pd.DataFrame):
        return None, f"{entry_point}() must return a pd.DataFrame, got {type(result).__name__}."

    return result, None


def _run_script_mode(
    code: str,
    dfs: dict[str, Any],
    result_vars: list[str],
    sandbox_globals: dict[str, Any],
) -> tuple[dict[str, Any], Optional[str]]:
    """Execute script-mode code: top-level assignments, no callable contract.

    A single dict serves as both globals and locals so that nested `def`s the
    LLM writes (which close over globals, not a separate locals dict) can
    still see `df`/`dfs` — exec(code, g, l) with g is not l would otherwise
    raise NameError inside any such nested function.
    """
    exec_env: dict[str, Any] = {**sandbox_globals, **dfs}

    try:
        exec(code, exec_env)  # noqa: S102
    except SyntaxError as e:
        return {}, f"SyntaxError: {e}"
    except Exception as e:
        return {}, f"{e}\n{traceback.format_exc()}"

    return {var: exec_env.get(var) for var in result_vars}, None


def _sandbox_worker(
    code: str,
    dfs: dict[str, Any],
    mode: str,
    entry_point: str,
    result_vars: list[str],
    extra_globals: Optional[dict[str, Any]],
    extra_builtins: Optional[dict[str, Any]],
    result_queue: "multiprocessing.Queue[dict[str, Any]]",
) -> None:
    """Entry point run inside the spawned child process.

    Always puts exactly one plain, picklable dict onto `result_queue` before
    returning — the parent's job (join/terminate/kill) is entirely about
    bounding *this* function's wall-clock time, not about handling exceptions
    that escape it, so nothing here is allowed to propagate uncaught.
    """
    try:
        builtins_ = {**SAFE_BUILTINS, **(extra_builtins or {})}
        sandbox_globals: dict[str, Any] = {
            "__builtins__": builtins_,
            "pd": pd,
            "np": np,
            **(extra_globals or {}),
        }

        if mode == "function":
            result_df, error = _run_function_mode(code, dfs, entry_point, sandbox_globals)
            values = {} if error is not None else {"result": result_df}
            result_queue.put({"values": values, "error": error})
        else:  # mode == "script"
            values, error = _run_script_mode(code, dfs, result_vars, sandbox_globals)
            result_queue.put({"values": values, "error": error})
    except Exception as e:  # noqa: BLE001 — must never let the child crash silently
        result_queue.put(
            {"values": {}, "error": f"Unexpected sandbox error: {e}\n{traceback.format_exc()}"}
        )


def execute_in_sandbox(
    code: str,
    dfs: dict[str, pd.DataFrame],
    *,
    mode: Literal["function", "script"] = "function",
    entry_point: str = "transform",
    result_vars: Optional[list[str]] = None,
    extra_globals: Optional[dict[str, Any]] = None,
    extra_builtins: Optional[dict[str, Any]] = None,
    timeout_seconds: int = 30,
) -> SandboxResult:
    """Execute LLM-generated Python code in an isolated, time-bounded child process.

    `mode="function"` preserves the Transformer's existing transform(dfs) -> pd.DataFrame
    contract: `code` must define a function named `entry_point` (default "transform"),
    called with `dfs`. On success, `result["values"]["result"]` holds the returned
    DataFrame.

    `mode="script"` covers Analyst/Science: `code` assigns top-level variables
    (gold_df/fig/narrative, or predictions_df/fig/narrative/model_info) instead of
    defining a callable — `result_vars` names which ones to collect. `dfs`' keys
    (typically just `{"df": ...}`) are exposed directly as globals to the script,
    matching what the Analyst/Science prompts already assume (`df` in scope).

    Runs in a multiprocessing.Process (spawn context, pinned explicitly rather than
    relying on the platform default — see ADR-007 for why). The parent calls
    `process.join(timeout_seconds)`; if the child is still alive afterward, it calls
    `process.terminate()` (SIGTERM), waits a short grace period, and calls
    `process.kill()` (SIGKILL) if it still hasn't exited. On timeout, returns
    SandboxResult(timed_out=True, values={}, error="Execution exceeded {N}s — simplify
    the computation").
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue: "multiprocessing.Queue[dict[str, Any]]" = ctx.Queue()

    process = ctx.Process(
        target=_sandbox_worker,
        args=(
            code,
            dfs,
            mode,
            entry_point,
            result_vars or [],
            extra_globals,
            extra_builtins,
            result_queue,
        ),
    )

    start = time.monotonic()
    process.start()

    # Drain the queue *while* waiting for the child, not strictly after
    # `process.join()` returns. `multiprocessing.Queue` is backed by an OS
    # pipe with a bounded buffer (~64KB on Linux) fed by a background thread
    # inside the child. If nobody reads from the queue until the child has
    # fully exited, a result larger than that buffer makes the child block
    # forever on `queue.put()` — its feeder thread can't drain into a pipe
    # nobody is reading — so the child never exits, `process.join(timeout_seconds)`
    # runs out the clock, and a call that actually finished computing gets
    # reported as `timed_out=True`. Polling `queue.get(timeout=...)` in a loop
    # concurrently with waiting for the child avoids the deadlock: as soon as
    # the child manages to put data on the queue it is read immediately,
    # unblocking its feeder thread regardless of how long the child then takes
    # to finish exiting.
    deadline = start + timeout_seconds
    payload: Optional[dict[str, Any]] = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            payload = result_queue.get(timeout=min(remaining, 0.1))
            break
        except queue.Empty:
            if not process.is_alive():
                break

    if payload is None and process.is_alive():
        process.terminate()
        process.join(_TERMINATE_GRACE_SECONDS)
        if process.is_alive():
            process.kill()
            process.join()
        duration = time.monotonic() - start
        result_queue.close()
        return SandboxResult(
            values={},
            error=f"Execution exceeded {timeout_seconds}s — simplify the computation",
            timed_out=True,
            duration_seconds=duration,
        )

    # The result (if any) has already been read above, which unblocks a
    # child that was waiting on `queue.put()` — reaping it here is now fast
    # and bounded rather than at risk of the same deadlock.
    process.join(_TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        process.kill()
        process.join()

    duration = time.monotonic() - start
    result_queue.close()

    if payload is None:
        # Child exited without putting anything on the queue — a crash, an
        # OOM kill, a segfault in a C extension. Surface a generic error
        # instead of hanging on an empty queue.
        return SandboxResult(
            values={},
            error=f"Sandboxed process exited unexpectedly (exit code {process.exitcode}) without a result.",
            timed_out=False,
            duration_seconds=duration,
        )

    return SandboxResult(
        values=payload.get("values", {}),
        error=payload.get("error"),
        timed_out=False,
        duration_seconds=duration,
    )
