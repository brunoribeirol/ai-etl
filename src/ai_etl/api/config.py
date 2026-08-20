"""Shared paths for the API — same `./runs/` layout `app.py` already uses,
so both processes agree on where uploads/audit artifacts land (relevant only
for the `local` StorageBackend; unused once `STORAGE_BACKEND=s3`, ADR-009).

`RUNS_DIR` itself now lives in `core/paths.py` (Sprint 13) so
`services/scheduler.py` can share the exact same constant without importing
from `api/` — re-exported here so existing `from ai_etl.api.config import
RUNS_DIR` call sites (e.g. `routers/runs.py`) don't need to change.
"""

from ai_etl.core.paths import RUNS_DIR

UPLOADS_DIR = RUNS_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

__all__ = ["RUNS_DIR", "UPLOADS_DIR"]
