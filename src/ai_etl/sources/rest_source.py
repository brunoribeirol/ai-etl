"""REST API source connector."""

from typing import Any

import httpx
import pandas as pd


def load_rest(url: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """Fetch JSON from a REST endpoint and normalize into a DataFrame."""
    response = httpx.get(url, params=params or {}, timeout=30)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        return pd.json_normalize(data)
    if isinstance(data, dict):
        for key in ("data", "results", "items", "records"):
            if key in data and isinstance(data[key], list):
                return pd.json_normalize(data[key])
        return pd.json_normalize([data])
    raise ValueError(f"Unexpected JSON structure from {url}: {type(data)}")
