"""CSV destination connector."""

from pathlib import Path
from typing import Any

import pandas as pd


def save_csv(df: pd.DataFrame, path: str) -> dict[str, Any]:
    """Save DataFrame to CSV. Creates parent directories if needed."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return {"rows_loaded": len(df), "destination": path}


def preview_csv(df: pd.DataFrame, path: str) -> dict[str, Any]:
    """Sprint 27 (ADR-028) — what `save_csv` would do, without writing anything.

    Contains no write call at all (not a `dry_run` flag on `save_csv` a caller
    could get wrong) — the "never accidentally writes on preview" invariant is
    structural. `would_write_rows` uses the same `len(df)` `save_csv` reports
    as `rows_loaded`, so a later real write always matches this preview.
    """
    existing = Path(path)
    existing_info = {"existing_bytes": existing.stat().st_size} if existing.exists() else None
    return {
        "destination_type": "csv",
        "destination": path,
        "would_write_rows": len(df),
        "existing": existing_info,
    }
