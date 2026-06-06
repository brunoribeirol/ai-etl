"""CSV destination connector."""

from pathlib import Path
from typing import Any

import pandas as pd


def save_csv(df: pd.DataFrame, path: str) -> dict[str, Any]:
    """Save DataFrame to CSV. Creates parent directories if needed."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return {"rows_loaded": len(df), "destination": path}
