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
        # Handle time-series dicts where a sub-key maps to parallel lists
        # (e.g. Open-Meteo: {"daily": {"time": [...], "temperature_2m_max": [...]}})
        for val in data.values():
            if isinstance(val, dict) and val and all(isinstance(v, list) for v in val.values()):
                lengths = {len(v) for v in val.values()}
                if len(lengths) == 1:
                    return pd.DataFrame(val)
        return pd.json_normalize([data])
    raise ValueError(f"Unexpected JSON structure from {url}: {type(data)}")
