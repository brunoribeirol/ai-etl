"""Shared local-disk path for `./runs/` artifacts (ADR-009's `local`
`StorageBackend`).

Lives in `core/`, not `api/`, so both `api/config.py` (the web process) and
`services/scheduler.py` (the beat process) can import the same constant
without `services/` depending on `api/` — this project's layering has
`agents/`/`services/` import from `core/`/`audit/`, never the other way
around; `api/` is the outermost layer. A previous version of
`services/scheduler.py` duplicated the literal `"./runs"` instead of
importing this, which a code review flagged as a future drift risk (Sprint
13 review) — fixed by giving both call sites one shared source of truth.
"""

from pathlib import Path

RUNS_DIR = Path("runs")
RUNS_DIR.mkdir(exist_ok=True)
