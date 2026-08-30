"""Driver entrypoint for the ADR-038 Docker sandbox image.

Reads one pickled request dict from stdin, runs it through
``ai_etl.core.sandbox._execute_code`` (the exact same restricted-globals /
mode-dispatch logic the ``multiprocessing`` "process" backend uses — this
file does not reimplement it), and writes one pickled result dict to stdout.

Intentionally minimal: this process runs inside a container started with
``--network none --read-only --cap-drop ALL --user 65534:65534`` (see
``core/sandbox_docker.py``), so isolation is enforced by the container
boundary, not by anything in this file. The only thing this file is
responsible for is not leaking anything *extra* into the executed code's
reach and not crashing without producing a result the parent can read.

Not imported by any other module — invoked only as ``python
/app/run_sandboxed.py`` (the image's ENTRYPOINT).
"""

import os
import pickle
import sys
import traceback
from typing import Any

# Defense in depth, same discipline as `_sandbox_worker()`'s first line in
# core/sandbox.py: the container is started with no `-e`/`--env-file` (see
# core/sandbox_docker.py), so there is nothing secret to clear here in
# practice — but clearing first, before importing anything that might read
# env vars at import time, keeps this file honest to the same rule even if
# a future caller changes how the container is invoked.
os.environ.clear()

from ai_etl.core.sandbox import _execute_code  # noqa: E402


def main() -> None:
    request: dict[str, Any] = pickle.load(sys.stdin.buffer)  # nosec B301 — see note below

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
    except Exception as e:  # noqa: BLE001 — must always produce a result, never crash silently
        response = {
            "values": {},
            "error": f"Unexpected sandbox error: {e}\n{traceback.format_exc()}",
        }

    pickle.dump(response, sys.stdout.buffer)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()

# Note on `pickle.load` (S301/B301, "possible security issue"): this is not
# an arbitrary-network-input deserialization — the payload comes over a
# stdin pipe from `core/sandbox_docker.py`, a process on the *same trusted
# host* that spawned this exact container for this exact call, over a pipe
# no other process can write to. It carries the sandboxed DataFrames and
# extra_globals/extra_modules, not attacker-controlled bytes from outside
# this project's own trust boundary — the actual untrusted input is `code`,
# a string field *inside* the payload, which is never `eval`/`exec`'d by
# this file directly (it's handed to `_execute_code()`'s own restricted
# `exec()`, the same reviewed call site as the "process" backend).
