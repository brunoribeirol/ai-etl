"""Generate a synthetic wide/large benchmark dataset for Sprint 12 scale profiling.

Not one of the 3 case-study scenarios — this is a standalone load-testing fixture used
by `case_study/data/profile_scale.py` to measure real Extractor/Transformer/Quality/
Analyst/Science behavior against data far larger than the existing scenario datasets
(sales.csv: 5k rows x 8 cols, orders.csv: 10k rows x 7 cols).

Default: 200,000 rows x 300 columns, matching the scale named in the Sprint 12 roadmap
item (Vault: artefact/product-roadmap-post-tcc.md). Column mix is deliberately
heterogeneous (not just floats) because the two confirmed Sprint 12 pain points are
both column-count-sensitive, not just row-count-sensitive:
  - extractor.py::_extract_schema's raw sample scales with column count directly
    (df.head(3).to_dict(orient="records") is 3 x n_cols values).
  - Sandbox timeout budget needs to reflect realistic mixed-dtype cleaning/aggregation
    work, not just numeric-only operations.

Column layout (out of `--cols`, default 300):
  - 1 row-id integer column (no nulls, no dupes — the natural primary key)
  - ~40% numeric (float, 5% nulls, 1% IQR outliers — mirrors generate_sales.py's
    existing outlier-injection pattern)
  - ~25% integer (small-range categorical-ish counts, 3% nulls)
  - ~20% low-cardinality string/categorical (2% nulls)
  - ~10% free-text-ish string (longer, higher entropy — worst case for prompt-size
    concerns since these produce the largest per-cell sample values)
  - ~5% datetime (ISO strings, 2% nulls)
Plus a fixed 2% exact-duplicate-row injection, matching the existing generators.

Usage:
    python case_study/data/generate_benchmark.py
    python case_study/data/generate_benchmark.py --rows 5000 --cols 300 --output case_study/data/benchmark_small.csv

Fixed seed (42) for reproducibility, consistent with generate_sales.py/generate_orders.py.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

SEED = 42

CATEGORY_POOL = [
    "Norte",
    "Nordeste",
    "Sul",
    "Sudeste",
    "Centro-Oeste",
    "Widget A",
    "Widget B",
    "Gadget X",
    "Gadget Y",
    "Tool Z",
    "active",
    "cancelled",
    "pending",
    "returned",
]


def _build_columns(n_cols: int, rng: np.random.Generator) -> list[tuple[str, str]]:
    """Return [(col_name, col_kind)] with the heterogeneous mix described above."""
    n_numeric = int(n_cols * 0.40)
    n_int = int(n_cols * 0.25)
    n_cat = int(n_cols * 0.20)
    n_text = int(n_cols * 0.10)
    n_dt = max(n_cols - n_numeric - n_int - n_cat - n_text, 0)

    layout: list[tuple[str, str]] = [("row_id", "id")]
    for i in range(n_numeric):
        layout.append((f"metric_{i}", "float"))
    for i in range(n_int):
        layout.append((f"count_{i}", "int"))
    for i in range(n_cat):
        layout.append((f"category_{i}", "cat"))
    for i in range(n_text):
        layout.append((f"note_{i}", "text"))
    for i in range(n_dt):
        layout.append((f"event_dt_{i}", "datetime"))
    return layout[:n_cols] if len(layout) > n_cols else layout


def _random_text(
    rng: np.random.Generator, n: int, min_words: int = 3, max_words: int = 8
) -> list[str]:
    words = [
        "order",
        "customer",
        "delay",
        "region",
        "review",
        "issue",
        "priority",
        "note",
        "batch",
        "run",
    ]
    out = []
    for _ in range(n):
        n_words = rng.integers(min_words, max_words + 1)
        out.append(" ".join(rng.choice(words, size=n_words)))
    return out


def generate(n_rows: int, n_cols: int, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    columns = _build_columns(n_cols, rng)

    data: dict[str, object] = {}
    for name, kind in columns:
        if kind == "id":
            data[name] = np.arange(1, n_rows + 1)
        elif kind == "float":
            vals = rng.uniform(10.0, 500.0, size=n_rows).round(2)
            null_idx = rng.choice(n_rows, size=int(n_rows * 0.05), replace=False)
            vals = vals.astype(object)
            vals[null_idx] = np.nan
            outlier_idx = rng.choice(n_rows, size=max(int(n_rows * 0.01), 1), replace=False)
            for idx in outlier_idx:
                if idx not in null_idx:
                    vals[idx] = round(float(rng.uniform(5000.0, 10000.0)), 2)
            data[name] = vals
        elif kind == "int":
            vals = rng.integers(0, 1000, size=n_rows).astype(object)
            null_idx = rng.choice(n_rows, size=int(n_rows * 0.03), replace=False)
            vals[null_idx] = np.nan
            data[name] = vals
        elif kind == "cat":
            vals = rng.choice(CATEGORY_POOL, size=n_rows).astype(object)
            null_idx = rng.choice(n_rows, size=int(n_rows * 0.02), replace=False)
            vals[null_idx] = np.nan
            data[name] = vals
        elif kind == "text":
            vals = np.array(_random_text(rng, n_rows), dtype=object)
            data[name] = vals
        elif kind == "datetime":
            base = pd.Timestamp("2023-01-01")
            offsets = rng.integers(0, 700, size=n_rows)
            vals = pd.to_datetime(base) + pd.to_timedelta(offsets, unit="D")
            vals = vals.strftime("%Y-%m-%d").to_numpy(dtype=object)
            null_idx = rng.choice(n_rows, size=int(n_rows * 0.02), replace=False)
            vals[null_idx] = np.nan
            data[name] = vals

    df = pd.DataFrame(data)

    # 2% exact-duplicate rows, same pattern as generate_sales.py/generate_orders.py.
    n_dup = max(int(n_rows * 0.02), 1)
    dup_rows = df.sample(n=n_dup, random_state=seed)
    df = pd.concat([df, dup_rows], ignore_index=True)
    df = df.sample(frac=1, random_state=seed).reset_index(drop=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--cols", type=int, default=300)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", type=str, default="case_study/data/benchmark_200k_300c.csv")
    args = parser.parse_args()

    start = time.monotonic()
    df = generate(args.rows, args.cols, args.seed)
    gen_seconds = time.monotonic() - start

    start = time.monotonic()
    df.to_csv(args.output, index=False)
    write_seconds = time.monotonic() - start

    print(f"Generated {len(df)} rows x {len(df.columns)} cols -> {args.output}")
    print(f"  Generation time: {gen_seconds:.2f}s | CSV write time: {write_seconds:.2f}s")
    print(f"  In-memory size: {df.memory_usage(deep=True).sum() / 1e6:.1f} MB")
    print(f"  Duplicates: {df.duplicated().sum()}")


if __name__ == "__main__":
    main()
