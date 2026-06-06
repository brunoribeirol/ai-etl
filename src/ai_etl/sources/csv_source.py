"""CSV source connector."""

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    """Load a CSV or Excel file into a DataFrame."""
    if path.endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    return pd.read_csv(path)
