"""Code execution sandbox with restricted globals.

Executes LLM-generated Python code with a limited set of safe builtins.
pandas and numpy are available; file I/O and network access are not.

Security note: exec() with restricted globals can be bypassed via
introspection (e.g., ().__class__.__mro__[1].__subclasses__()).
This is an accepted limitation for the TCC scope with controlled datasets.
Production use would require a proper sandboxing solution (e.g., Docker).
See SECURITY.md and docs/adr/ADR-003-exec-sandbox.md for the full risk analysis,
including the two other exec() sites in agents/analyst.py and agents/science.py
that this module's sandbox does not cover.
"""

import traceback
from typing import Any, Optional

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
    "None": None,
    "True": True,
    "False": False,
}

SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": SAFE_BUILTINS,
    "pd": pd,
    "np": np,
}


def execute_in_sandbox(
    code: str,
    dfs: dict[str, pd.DataFrame],
    timeout_seconds: int = 30,
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Execute LLM-generated transform() function in a restricted environment.

    Args:
        code: Python source containing a function `transform(dfs) -> pd.DataFrame`.
        dfs: Dictionary of DataFrames available to the transform function.
        timeout_seconds: Not enforced in this implementation (v0 limitation).

    Returns:
        Tuple of (result_dataframe, error_message).
        On success: (DataFrame, None).
        On failure: (None, error string with traceback).
    """
    local_env: dict[str, Any] = {"dfs": dfs}
    sandbox_globals = {**SAFE_GLOBALS}

    try:
        exec(code, sandbox_globals, local_env)  # noqa: S102
    except SyntaxError as e:
        return None, f"SyntaxError: {e}"
    except Exception as e:
        return None, f"Error defining transform function: {e}\n{traceback.format_exc()}"

    if "transform" not in local_env:
        return None, "No function named 'transform' found in generated code."

    try:
        result = local_env["transform"](dfs)
    except Exception as e:
        return None, f"Error executing transform(dfs): {e}\n{traceback.format_exc()}"

    if not isinstance(result, pd.DataFrame):
        return None, f"transform() must return a pd.DataFrame, got {type(result).__name__}."

    return result, None
