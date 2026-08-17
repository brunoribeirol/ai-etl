"""Shared paths for the API — same `./runs/` layout `app.py` already uses,
so both processes agree on where uploads/audit artifacts land (relevant only
for the `local` StorageBackend; unused once `STORAGE_BACKEND=s3`, ADR-009)."""

from pathlib import Path

RUNS_DIR = Path("runs")
RUNS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = RUNS_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
