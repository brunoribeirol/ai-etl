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
introspection (e.g., ().__class__.__mro__[1].__subclasses__()). ADR-038 adds
a second, opt-in isolation backend (``core/sandbox_docker.py`` — a
network-disabled, read-only, resource-capped Docker container) specifically
to contain that bypass, superseding ADR-032 Decision 4's risk acceptance.
The `"process"` backend below remains the default (this project's Railway
deployment cannot run Docker-in-Docker — see ADR-038's "Railway feasibility"
section); `"docker"` is opt-in via `AI_ETL_SANDBOX_BACKEND=docker` or
`execute_in_sandbox(..., backend="docker")`, dev/local-verified today, with
production rollout tracked as explicit follow-up work.
See SECURITY.md and docs/adr/ADR-003-exec-sandbox.md, ADR-007, ADR-032
Decision 4, and ADR-038 for the full risk analysis and its history.
"""

import importlib
import multiprocessing
import os
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

# ADR-038: selects execute_in_sandbox()'s isolation backend when the
# `backend` argument isn't passed explicitly. "process" (default) is the
# multiprocessing.Process implementation below; "docker" routes through
# core/sandbox_docker.py instead. Left at "process" in every deployed
# environment today (Railway) — see ADR-038 for why "docker" isn't yet the
# production default.
_SANDBOX_BACKEND_ENV_VAR = "AI_ETL_SANDBOX_BACKEND"

# Sprint 12 (ADR-012): real profiling against a 200k-row x 300-col benchmark
# (docs/work/2026-08-19-sprint12-scale-profiling.md) confirmed the fixed
# per-call-site timeout (Transformer 30s default, Analyst 15s, Science 20s) does
# NOT scale with input size — at 204,000 rows, representative Science-style code
# (a real RandomForestRegressor fit, the exact operation Science's own prompt asks
# the LLM to write) hit the 20s ceiling and timed out; representative Analyst-style
# code (a single groupby+aggregate) used ~90% of its 15s budget. Every existing
# case-study scenario tops out at 10,000 rows, well under the threshold below, so
# this is a no-op for current scenarios — it only engages for inputs materially
# larger than anything exercised before this sprint.
LARGE_DATASET_ROW_THRESHOLD = 50_000
LARGE_DATASET_TIMEOUT_MULTIPLIER = 2

# Deliberately a simple step function, not a continuous formula fit to a curve —
# only one large-scale data point (204k rows) has been measured so far;
# extrapolating a precise row-count-proportional curve from a single point would
# be false precision. A flat 2x multiplier above the threshold gave real
# headroom against that one measured point (Science: 20s -> 40s effective budget,
# comfortably above the ~21s it actually needed) without guessing at the true
# shape of the timing curve between 10k and 204k rows. Revisit with intermediate
# measurements (50k/100k/150k rows) before refining further.


def scale_timeout_for_rows(base_timeout_seconds: int, n_rows: int) -> int:
    """Scale a sandbox `timeout_seconds` budget for large inputs.

    Called by Transformer/Analyst/Science with the row count of the DataFrame(s)
    actually being processed, before passing `timeout_seconds` to
    `execute_in_sandbox()`. See the module-level constants above for the
    real-measurement rationale behind the threshold and multiplier.
    """
    if n_rows > LARGE_DATASET_ROW_THRESHOLD:
        return base_timeout_seconds * LARGE_DATASET_TIMEOUT_MULTIPLIER
    return base_timeout_seconds


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
        exec(code, sandbox_globals, local_env)  # noqa: S102  # nosemgrep: python.lang.security.audit.exec-detected.exec-detected -- the one reviewed exec() call site (SECURITY.md, ADR-003, ADR-007); restricted globals, isolated child process, timeout-bounded
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
        exec(code, exec_env)  # noqa: S102  # nosemgrep: python.lang.security.audit.exec-detected.exec-detected -- same reviewed sandbox call site as above (script mode)
    except SyntaxError as e:
        return {}, f"SyntaxError: {e}"
    except Exception as e:
        return {}, f"{e}\n{traceback.format_exc()}"

    return {var: exec_env.get(var) for var in result_vars}, None


def _execute_code(
    code: str,
    dfs: dict[str, Any],
    mode: str,
    entry_point: str,
    result_vars: list[str],
    extra_globals: Optional[dict[str, Any]],
    extra_modules: Optional[dict[str, str]],
    extra_builtins: Optional[dict[str, Any]],
) -> tuple[dict[str, Any], Optional[str]]:
    """Build the restricted globals and run `code` in the current process.

    Pulled out of `_sandbox_worker` (ADR-038) so the exact same, single
    reviewed logic runs regardless of *which* isolation boundary calls it —
    the `multiprocessing` child below, or the Docker sandbox driver script
    (`docker/sandbox/run_sandboxed.py`), which imports this function directly
    rather than re-implementing the restricted-globals/mode dispatch a second
    time. This function assumes whatever isolation boundary the caller
    provides (process, container, ...) already exists; it does not create one
    itself.
    """
    builtins_ = {**SAFE_BUILTINS, **(extra_builtins or {})}
    imported = {
        name: importlib.import_module(dotted_path)
        for name, dotted_path in (extra_modules or {}).items()
    }
    sandbox_globals: dict[str, Any] = {
        "__builtins__": builtins_,
        "pd": pd,
        "np": np,
        **imported,
        **(extra_globals or {}),
    }

    if mode == "function":
        result_df, error = _run_function_mode(code, dfs, entry_point, sandbox_globals)
        values = {} if error is not None else {"result": result_df}
        return values, error
    else:  # mode == "script"
        return _run_script_mode(code, dfs, result_vars, sandbox_globals)


def _sandbox_worker(
    code: str,
    dfs: dict[str, Any],
    mode: str,
    entry_point: str,
    result_vars: list[str],
    extra_globals: Optional[dict[str, Any]],
    extra_modules: Optional[dict[str, str]],
    extra_builtins: Optional[dict[str, Any]],
    result_queue: "multiprocessing.Queue[dict[str, Any]]",
) -> None:
    """Entry point run inside the spawned child process.

    Always puts exactly one plain, picklable dict onto `result_queue` before
    returning — the parent's job (join/terminate/kill) is entirely about
    bounding *this* function's wall-clock time, not about handling exceptions
    that escape it, so nothing here is allowed to propagate uncaught.
    """
    # Clear the child's environment before anything else runs — the very
    # first line, before even building SAFE_GLOBALS. `spawn` does not clear
    # os.environ; the child inherits it verbatim from the parent (this
    # process), which holds real secrets (APP_DATABASE_URL, POSTGRES_URL,
    # CLERK_JWKS_URL/CLERK_ISSUER, OPENAI_API_KEY-style vars — see
    # audit/connection.py, sources/postgres_source.py, services/auth_service.py,
    # core/llm.py). None of those are ever placed in SAFE_GLOBALS/extra_globals,
    # but the sandbox's exec()-with-restricted-globals boundary is documented
    # (module docstring above) as bypassable via introspection
    # (`().__class__.__mro__[1].__subclasses__()`) reaching already-imported
    # modules' `__globals__` — which is enough to reach `os.environ` without
    # ever doing `import os` through the blocked builtins. Clearing here
    # removes that concrete target; it does not close the introspection
    # escape itself (still an accepted, documented, separate limitation).
    #
    # Safe to do this late (as the first line of the worker, not earlier):
    # pandas/numpy (imported at module load, above) and any extra_globals
    # classes (sklearn/statsmodels — pickled by reference and unpickled by
    # multiprocessing's bootstrap before this function is even called) have
    # already finished importing by this point, using the still-intact
    # environment. `extra_modules` (plotly, math, ...) import *after* this
    # clear, via importlib inside this function — checked and none of them
    # need an env var at import time. Checked for any *runtime* (not just
    # import-time) env-var dependency in this call path and found none that
    # matters here: pandas/numpy have none at the operations Analyst/Science/
    # Transformer code performs; sklearn's estimators here (RandomForest*,
    # KMeans, LinearRegression/Ridge/LogisticRegression) default to
    # `n_jobs=None` (no parallel worker subprocesses, so no joblib/loky
    # temp-dir env lookup); and even if LLM-generated code passed `n_jobs=-1`,
    # `tempfile.gettempdir()` (what joblib/loky falls back to without
    # `TMPDIR`/`TEMP`/`TMP`) has a hardcoded `/tmp` fallback on the Linux
    # containers this project deploys to. Nothing here needs an allowlist.
    os.environ.clear()

    try:
        values, error = _execute_code(
            code, dfs, mode, entry_point, result_vars, extra_globals, extra_modules, extra_builtins
        )
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
    extra_modules: Optional[dict[str, str]] = None,
    extra_builtins: Optional[dict[str, Any]] = None,
    timeout_seconds: int = 30,
    backend: Optional[Literal["process", "docker"]] = None,
) -> SandboxResult:
    """Execute LLM-generated Python code in an isolated, time-bounded sandbox.

    Two isolation backends exist (ADR-038): `"process"` (default, described
    below — a `multiprocessing.Process` child) and `"docker"` (a
    network-disabled, read-only, resource-capped container — see
    `core/sandbox_docker.py` and ADR-038 for what it does and does not close
    versus the process backend, and why it is NOT the production default on
    this project's current Railway deployment). Selecting `"docker"` requires
    a reachable Docker daemon and the pre-built sandbox image; it raises
    `DockerSandboxUnavailableError` rather than silently falling back to the
    weaker `"process"` backend if either is missing — an explicit opt-in
    fails closed, it does not degrade quietly.

    Backend selection is resolved in this order: the `backend` argument, then
    the `AI_ETL_SANDBOX_BACKEND` environment variable, then `"process"`. This
    keeps the function's signature backward-compatible for every existing
    caller (Transformer, Analyst, Science) — none of them pass `backend`, so
    none of them change behavior unless `AI_ETL_SANDBOX_BACKEND=docker` is set
    in their environment.

    `mode="function"` preserves the Transformer's existing transform(dfs) -> pd.DataFrame
    contract: `code` must define a function named `entry_point` (default "transform"),
    called with `dfs`. On success, `result["values"]["result"]` holds the returned
    DataFrame.

    `mode="script"` covers Analyst/Science: `code` assigns top-level variables
    (gold_df/fig/narrative, or predictions_df/fig/narrative/model_info) instead of
    defining a callable — `result_vars` names which ones to collect. `dfs`' keys
    (typically just `{"df": ...}`) are exposed directly as globals to the script,
    matching what the Analyst/Science prompts already assume (`df` in scope).

    `extra_globals` is for values that pickle *by reference* across the `spawn`
    boundary — classes and functions (e.g. sklearn's `RandomForestRegressor`),
    which pickle stores as a dotted qualified name and reconstructs by importing
    it in the child. **Whole module objects do not pickle at all** (`TypeError:
    cannot pickle 'module' object` — a real bug caught by this project's CI, not
    a theoretical concern; ADR-007's original assumption that modules pickle by
    reference was wrong). For a module (`plotly.express`, `math`, `os`, ...), use
    `extra_modules={"px": "plotly.express"}` instead — the child imports it
    itself via `importlib.import_module()`, so nothing module-shaped ever has to
    cross the process boundary through `multiprocessing.Process`'s own pickling
    of its `args`.

    Runs in a multiprocessing.Process (spawn context, pinned explicitly rather than
    relying on the platform default — see ADR-007 for why). The parent calls
    `process.join(timeout_seconds)`; if the child is still alive afterward, it calls
    `process.terminate()` (SIGTERM), waits a short grace period, and calls
    `process.kill()` (SIGKILL) if it still hasn't exited. On timeout, returns
    SandboxResult(timed_out=True, values={}, error="Execution exceeded {N}s — simplify
    the computation").
    """
    resolved_backend = backend or os.environ.get(_SANDBOX_BACKEND_ENV_VAR, "process")
    if resolved_backend == "docker":
        # Imported lazily so the `docker` CLI / subprocess dependency is only
        # ever touched by call sites that actually opt into it — every
        # existing caller (Transformer, Analyst, Science) still gets the
        # "process" backend below unless AI_ETL_SANDBOX_BACKEND=docker (or
        # backend="docker") is set explicitly.
        from ai_etl.core.sandbox_docker import execute_in_docker_sandbox

        return execute_in_docker_sandbox(
            code,
            dfs,
            mode=mode,
            entry_point=entry_point,
            result_vars=result_vars or [],
            extra_globals=extra_globals,
            extra_modules=extra_modules,
            extra_builtins=extra_builtins,
            timeout_seconds=timeout_seconds,
        )

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
            extra_modules,
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
