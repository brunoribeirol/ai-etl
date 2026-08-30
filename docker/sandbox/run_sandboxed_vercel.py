"""Driver entrypoint for the ADR-039 Vercel Sandbox production image.

Reads one pickled request dict from a file (Vercel Sandbox's Python SDK
does not support piping data over a process's stdin — "Process standard
input isn't supported", per the Python SDK reference — so the payload has
to cross the boundary as a file instead of the pipe `docker/sandbox/
run_sandboxed.py` uses), runs it through
``ai_etl.core.sandbox._execute_code`` (the exact same restricted-globals /
mode-dispatch logic every other backend uses — this file does not
reimplement it), and writes one pickled result dict to a second file.

Invoked as ``python /app/run_sandboxed_vercel.py <input_path> <output_path>``
by ``core/sandbox_vercel.py`` via ``box.run_process(...)``. Not imported by
any other module.

Ships in the same image as ``run_sandboxed.py`` (see ``docker/sandbox/
Dockerfile``) — one image, two entrypoints, one shared dependency install.
Vercel Sandbox ignores a custom image's Docker ``ENTRYPOINT``/``CMD``
entirely ("Vercel Sandbox does not run Docker ENTRYPOINT or CMD for custom
images"), so this file is only ever reached by naming it explicitly on the
``run_process()`` command line, unlike ``run_sandboxed.py`` which the
Docker backend's ``docker run`` invokes as the container's entrypoint.
"""

import os
import pickle
import sys
import traceback
from typing import Any

# Same discipline as run_sandboxed.py's first line: clear before importing
# anything that might read env vars at import time. core/sandbox_vercel.py
# does not pass any host env vars into the sandbox's `env=` kwarg, so this
# is defense in depth (a future caller changing that should not silently
# undo this), not a mitigation for a concrete leak today.
os.environ.clear()

from ai_etl.core.sandbox import _execute_code  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print(  # noqa: T201 -- this process's own stderr, not application logging
            "usage: run_sandboxed_vercel.py <input_path> <output_path>",
            file=sys.stderr,
        )
        sys.exit(2)

    input_path, output_path = sys.argv[1], sys.argv[2]

    with open(input_path, "rb") as f:
        request: dict[str, Any] = pickle.load(f)  # nosec B301 -- see note below

    try:
        values, error = _execute_code(
            request["code"],
            request["dfs"],
            request["mode"],
            request["entry_point"],
            request["result_vars"],
            request.get("extra_globals"),
            request.get("extra_modules"),
            request.get("extra_builtins"),
        )
        response: dict[str, Any] = {"values": values, "error": error}
    except Exception as e:  # noqa: BLE001 -- must always produce a result, never crash silently
        response = {
            "values": {},
            "error": f"Unexpected sandbox error: {e}\n{traceback.format_exc()}",
        }

    with open(output_path, "wb") as f:
        pickle.dump(response, f)


if __name__ == "__main__":
    main()

# Note on `pickle.load` (S301/B301, "possible security issue"): the file
# read here was written moments earlier by `core/sandbox_vercel.py` via
# `box.fs.write_bytes()`, into this exact sandbox instance, for this exact
# call — not attacker-controlled network input. The untrusted part of this
# call (`code`) is a plain string field inside the payload, never itself
# `eval`/`exec`'d by this file; it only ever reaches Python's restricted
# `exec()` inside `_execute_code()`, the same reviewed call site every other
# backend uses.
