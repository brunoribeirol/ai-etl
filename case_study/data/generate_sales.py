"""Generate case_study/data/sales.csv for Scenario 1.

5000 rows with injected quality issues:
  - 5% nulls in amt and customer_id
  - 2% duplicate rows (100 rows repeated)
  - 1% outliers in amt (values 10x above IQR)

Fixed seed (42) for reproducibility.
"""

import random

import numpy as np
import pandas as pd

SEED = 42
N_BASE = 4900
N_DUPLICATES = 100
rng = np.random.default_rng(SEED)
random.seed(SEED)

PRODUCTS = ["Widget A", "Widget B", "Gadget X", "Gadget Y", "Tool Z"]
REGIONS = ["Norte", "Nordeste", "Sul", "Sudeste", "Centro-Oeste"]
STATUSES = ["active", "cancelled"]

order_ids = list(range(1001, 1001 + N_BASE))
customer_ids = rng.integers(100, 500, size=N_BASE).tolist()
dates = pd.date_range("2024-01-01", periods=N_BASE, freq="2h").strftime("%Y-%m-%d").tolist()
products = rng.choice(PRODUCTS, size=N_BASE).tolist()
amounts = rng.uniform(10.0, 500.0, size=N_BASE).round(2).tolist()
quantities = rng.integers(1, 20, size=N_BASE).tolist()
statuses = rng.choice(STATUSES, size=N_BASE, p=[0.75, 0.25]).tolist()
regions = rng.choice(REGIONS, size=N_BASE).tolist()

df = pd.DataFrame({
    "order_id": order_ids,
    "customer_id": customer_ids,
    "dt": dates,
    "product": products,
    "amt": amounts,
    "qty": quantities,
    "status": statuses,
    "region": regions,
})

# Inject 5% nulls in amt and customer_id
null_idx_amt = rng.choice(N_BASE, size=int(N_BASE * 0.05), replace=False)
null_idx_cid = rng.choice(N_BASE, size=int(N_BASE * 0.05), replace=False)
df.loc[null_idx_amt, "amt"] = np.nan
df.loc[null_idx_cid, "customer_id"] = np.nan

# Inject 1% outliers in amt (10x the max normal value)
outlier_idx = rng.choice(N_BASE, size=int(N_BASE * 0.01), replace=False)
df.loc[outlier_idx, "amt"] = rng.uniform(5000.0, 10000.0, size=len(outlier_idx)).round(2)

# Inject 2% duplicates (repeat 100 random rows)
dup_rows = df.sample(n=N_DUPLICATES, random_state=SEED)
df = pd.concat([df, dup_rows], ignore_index=True)

# Shuffle to distribute duplicates naturally
df = df.sample(frac=1, random_state=SEED).reset_index(drop=True)

output_path = "case_study/data/sales.csv"
df.to_csv(output_path, index=False)

print(f"Generated {len(df)} rows → {output_path}")
print(f"  Columns: {list(df.columns)}")
print(f"  Nulls in amt: {df['amt'].isnull().sum()}")
print(f"  Nulls in customer_id: {df['customer_id'].isnull().sum()}")
print(f"  Duplicates: {df.duplicated().sum()}")
print(f"  Status counts: {df['status'].value_counts().to_dict()}")
