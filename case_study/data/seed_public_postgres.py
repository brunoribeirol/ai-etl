"""Load the NYC TLC taxi-zone lookup table into PostgreSQL for scenarios 2 and 3.

Creates (replacing if present) `public.taxi_zones` from
`case_study/data/taxi_zones.csv`, exactly as published — 265 rows, including the
two placeholder zones whose `Zone`/`service_zone` are empty. No row is altered.

Usage:
    make db-up                                              # start PostgreSQL
    uv run python case_study/data/fetch_public_data.py      # download the lookup table
    uv run python case_study/data/seed_public_postgres.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

ZONES_CSV = Path(__file__).resolve().parent / "taxi_zones.csv"
TABLE_NAME = "taxi_zones"


def main() -> int:
    url = os.getenv("POSTGRES_URL")
    if not url:
        print("POSTGRES_URL is not set.", file=sys.stderr)
        return 1
    if not ZONES_CSV.exists():
        print(f"{ZONES_CSV} not found — run fetch_public_data.py first.", file=sys.stderr)
        return 1

    zones = pd.read_csv(ZONES_CSV)
    engine = create_engine(url)
    try:
        zones.to_sql(TABLE_NAME, engine, schema="public", if_exists="replace", index=False)
    finally:
        engine.dispose()
    print(f"Loaded {len(zones)} rows into public.{TABLE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
