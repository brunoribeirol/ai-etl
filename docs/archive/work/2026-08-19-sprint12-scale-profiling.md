# Sprint 12 — scale profiling report (real measurements)

**Date:** 2026-08-19
**Branch:** `feat/sprint12-scale-robustness`
**Companion ADR:** `docs/adr/ADR-012-scale-strategy.md`

## Environment

- Machine: local dev machine (macOS, Darwin 25.6.0, 8 CPU cores, 8 GB RAM) — not Railway
  production hardware. Numbers here are directional: relative comparisons and pass/fail
  against the fixed timeouts are the load-bearing findings; absolute production latency on
  Railway's containers will differ.
- Python 3.12, `pandas`/`numpy`/`scikit-learn` versions pinned by `uv.lock`.
- **No `OPENAI_API_KEY` configured in this environment** — no real LLM call was made
  anywhere in this profiling run. See ADR-012's "Profiling methodology" section for why
  this does not block measuring either of the two confirmed Sprint 12 points.

## Benchmark dataset

`case_study/data/generate_benchmark.py --rows 200000 --cols 300` (default args).
Heterogeneous column mix: ~40% float (5% nulls, 1% IQR outliers), ~25% int (3% nulls),
~20% categorical (2% nulls), ~10% free text, ~5% datetime (2% nulls), 1 id column,
2% exact-duplicate rows. Actual generated size: **204,000 rows x 300 columns**, ~567 MB CSV
(200,000 base rows + 4,000 duplicate-injected rows = 2%, matching the generator's spec).
Not committed to git (`case_study/data/*.csv` is gitignored, predating this sprint) —
regenerate locally with the command above. Generation itself took ~13 minutes on this
machine (dominated by the ~30 free-text columns' per-row Python-level random word
sampling) — a known, accepted one-time cost of building the fixture, not a pipeline-latency
finding.

## Method

```
uv run python case_study/data/profile_scale.py --csv case_study/data/benchmark_200k_300c.csv
```

## Results (real, full run)

```json
{
  "csv": "case_study/data/benchmark_200k_300c.csv",
  "measurements": [
    {
      "name": "extractor_load_csv",
      "seconds": 12.749,
      "peak_mb": 1483.7,
      "n_rows": 204000,
      "n_cols": 300
    },
    {
      "name": "extractor_schema_size",
      "seconds": 0.0,
      "peak_mb": 0.0,
      "n_cols": 300,
      "current_schema_chars": 38837,
      "current_schema_tokens_est": 9709,
      "compact_schema_chars": 23828,
      "compact_schema_tokens_est": 5957,
      "reduction_pct": 38.6,
      "multi_source_current_tokens_est_x3": 29128,
      "multi_source_compact_tokens_est_x3": 17871
    },
    {
      "name": "sandbox_transformer_style",
      "seconds": 32.464,
      "peak_mb": 0.0,
      "n_rows": 204000,
      "timed_out": false,
      "error": null
    },
    {
      "name": "sandbox_analyst_style",
      "seconds": 13.487,
      "peak_mb": 0.0,
      "n_rows": 204000,
      "timed_out": false,
      "error": null
    },
    {
      "name": "sandbox_science_style",
      "seconds": 20.702,
      "peak_mb": 0.0,
      "n_rows": 204000,
      "timed_out": true,
      "error": "Execution exceeded 20s — simplify the computation"
    },
    {
      "name": "quality_node",
      "seconds": 12.229,
      "peak_mb": 946.7,
      "n_rows": 204000
    }
  ]
}
```

(`peak_mb: 0.0` on the sandbox measurements is expected, not a bug — sandboxed code runs in
a separate child process, per ADR-007, so the parent's `tracemalloc` never sees its memory;
peak memory for those stages would need to be measured with an in-child instrumentation hook,
not attempted here since it isn't needed to answer either of Sprint 12's two questions.)

## Interpretation

### Point 1 — extractor schema size: CONFIRMED

A single 300-column source's naive (pre-fix) schema serializes to 38,837 characters
(~9,709 tokens estimated at ~4 chars/token). The fix (`MAX_SAMPLE_COLUMNS`-capped sample,
`agents/extractor.py`) brings that to 23,828 characters (~5,957 tokens) — a **38.6%
reduction** on a single source. With 3 heterogeneous sources in one pipeline plan (the
product's own stated differentiator), the naive total is ~29,128 estimated tokens vs.
~17,871 fixed — real, measured savings, not projected. See ADR-012 for the fix and why
`dtypes`/`null_counts`/`columns` were deliberately left full-width (they were never the
part of the payload that scaled the way the raw sample did).

### Point 2 — sandbox timeout vs. real execution time: CONFIRMED

This is the headline finding. Representative **Science-style code — a real
`RandomForestRegressor` fit** (the exact operation type Science's prompt instructs the LLM
to write for a regression task) **timed out** at the fixed `SCIENCE_TIMEOUT_SECONDS = 20`
budget against the 204k-row benchmark (`timed_out: true`, real `execute_in_sandbox()`
result, not inferred). This is a direct, reproduced confirmation of the concern named in the
Sprint 12 roadmap item — "uma transformação legítima em 200k linhas pode simplesmente não
caber no orçamento de tempo hoje, sem ser um bug de código gerado."

Representative **Analyst-style code** (`groupby().agg()` + Plotly bar) did not time out but
used 13.5 of its 15s budget (~90%) on a single sub-task — with Analyst's existing 3x retry
loop and `pipeline_service.py`'s ability to fan a single business question out into multiple
Gold sub-tasks, this margin does not hold up under realistic compounding, even though this
one profiling run didn't hit it directly.

Representative **Transformer-style code** (median-fill + drop_duplicates + filter) reported
`timed_out: false` internally, but the wrapper's external wall-clock measurement (32.5s)
exceeded the nominal 30s budget once process-spawn/pickling overhead outside
`execute_in_sandbox()`'s own internal deadline accounting is counted — effectively zero
margin at this scale.

**Fix**: `core/sandbox.py::scale_timeout_for_rows()`, applied at all three call sites
(`transformer.py`, `analyst.py`, `science.py`), doubles the effective timeout above 50,000
rows. See ADR-012 for the full rationale, including why a flat step function was chosen
over a continuous formula (only one large-scale data point was measured).

### Quality stage

12.2s, fully deterministic, no LLM, not gated by any timeout — noted for completeness, not
a bottleneck either of Sprint 12's two points was about. Not further optimized in this
sprint (no confirmed problem to fix).

### Extractor I/O

`load_csv()` (plain `pd.read_csv`) took 12.7s and peaked at 1.48 GB RSS for the 567 MB CSV
— unremarkable pandas behavior at this scale, not a Sprint 12 finding, noted for context.

## LLM cost incurred

**$0.00** — no LLM call was made during this profiling session (no `OPENAI_API_KEY`
configured in this environment; see ADR-012). Real end-to-end LLM-driven validation
against this benchmark (Orchestrator/Transformer/Analyst/Science code generation quality
at 300-column scale) remains an explicit open item, not silently skipped — flagged in
ADR-012's Consequences section.
