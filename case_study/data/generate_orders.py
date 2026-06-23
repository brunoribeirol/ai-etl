"""Generate case_study/data/orders.csv for Scenarios 2 and 3.

10 000 rows with injected quality issues:
  - 5% nulls in customer_id
  - 2% duplicate rows (200 rows repeated)
  - 25% cancelled orders (to test active filter)

customer_id range: 100–499 (matches public.customers seeded by seed_postgres.py)
Fixed seed (42) for reproducibility.
"""

import numpy as np
import pandas as pd

SEED = 42
N_BASE = 9800
N_DUPLICATES = 200
rng = np.random.default_rng(SEED)

PRODUCTS = ["Widget A", "Widget B", "Gadget X", "Gadget Y", "Tool Z", "Device Pro", "Kit Basic"]
STATUSES = ["active", "cancelled"]

order_ids = list(range(5001, 5001 + N_BASE))
customer_ids = rng.integers(100, 500, size=N_BASE).tolist()
dates = pd.date_range("2024-01-01", periods=N_BASE, freq="1h").strftime("%Y-%m-%d").tolist()
products = rng.choice(PRODUCTS, size=N_BASE).tolist()
amounts = rng.uniform(15.0, 800.0, size=N_BASE).round(2).tolist()
quantities = rng.integers(1, 30, size=N_BASE).tolist()
statuses = rng.choice(STATUSES, size=N_BASE, p=[0.75, 0.25]).tolist()

df = pd.DataFrame({
    "order_id": order_ids,
    "customer_id": customer_ids,
    "order_dt": dates,
    "product": products,
    "amount": amounts,
    "qty": quantities,
    "status": statuses,
})

# Inject 5% nulls in customer_id
null_idx = rng.choice(N_BASE, size=int(N_BASE * 0.05), replace=False)
df.loc[null_idx, "customer_id"] = np.nan

# Inject 2% duplicates
dup_rows = df.sample(n=N_DUPLICATES, random_state=SEED)
df = pd.concat([df, dup_rows], ignore_index=True)
df = df.sample(frac=1, random_state=SEED).reset_index(drop=True)

output_path = "case_study/data/orders.csv"
df.to_csv(output_path, index=False)

print(f"Generated {len(df)} rows → {output_path}")
print(f"  Columns: {list(df.columns)}")
print(f"  Nulls in customer_id: {df['customer_id'].isnull().sum()}")
print(f"  Duplicates: {df.duplicated().sum()}")
print(f"  Status counts: {df['status'].value_counts().to_dict()}")
