"""Seed public.customers in PostgreSQL for Scenarios 2 and 3.

Creates and populates 400 customer rows (customer_id 100–499).
City distribution is Recife-heavy to match the Open-Meteo endpoint
used in Scenario 3 (lat=-8.05, lon=-34.88, Recife/PE).

Usage:
    make db-up          # start PostgreSQL via docker-compose
    uv run python case_study/data/seed_postgres.py
"""

import os

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

SEED = 42
rng = np.random.default_rng(SEED)

CUSTOMER_IDS = list(range(100, 500))  # 400 customers
N = len(CUSTOMER_IDS)

# Pernambuco cities — Recife-heavy so Scenario 3 enrichment has meaningful matches
CITIES = ["Recife", "Recife", "Recife", "Recife", "Recife",
          "Olinda", "Olinda", "Caruaru", "Petrolina", "Garanhuns"]
CITY_WEIGHTS = [0.5, 0.5, 0.5, 0.5, 0.5, 0.1, 0.1, 0.1, 0.05, 0.05]

# Normalise weights
total = sum(CITY_WEIGHTS)
city_probs = [w / total for w in CITY_WEIGHTS]

first_names = ["Ana", "Bruno", "Carlos", "Daniela", "Eduardo",
               "Fernanda", "Gustavo", "Helena", "Igor", "Julia"]
last_names = ["Silva", "Santos", "Oliveira", "Souza", "Lima",
              "Costa", "Ferreira", "Alves", "Pereira", "Carvalho"]

names = [
    f"{rng.choice(first_names)} {rng.choice(last_names)}"
    for _ in range(N)
]
cities = rng.choice(CITIES, size=N, p=city_probs).tolist()
emails = [
    f"customer{cid}@example.com"
    for cid in CUSTOMER_IDS
]

df = pd.DataFrame({
    "customer_id": CUSTOMER_IDS,
    "name": names,
    "city": cities,
    "email": emails,
})

url = os.getenv("POSTGRES_URL")
if not url:
    raise EnvironmentError("POSTGRES_URL is not set. Run 'make db-up' first.")

engine = create_engine(url)

with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS public.customers CASCADE"))
    conn.execute(text("""
        CREATE TABLE public.customers (
            customer_id INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            city        TEXT NOT NULL,
            email       TEXT NOT NULL
        )
    """))

df.to_sql("customers", engine, schema="public", if_exists="append", index=False)

print(f"Seeded {len(df)} rows → public.customers")
print(f"  City distribution:\n{df['city'].value_counts().to_string()}")
