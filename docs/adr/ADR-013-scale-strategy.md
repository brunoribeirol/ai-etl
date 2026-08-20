# ADR-013 — Scale strategy for large sources (100k–500k rows, 100–300+ columns)

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Bruno Ribeiro

---

## Context

Sprint 12 (Vault: `artefact/product-roadmap-post-tcc.md`, "Robustez em escala") was opened
on an explicit owner request — "quero algo robusto e confiável" — because every case-study
scenario so far runs in the low thousands of rows (`sales.csv`: 5,000 rows x 8 cols;
`orders.csv`: 10,000 rows x 7 cols). Two concrete points were already identified in the
code, not hypothesized, before this sprint started:

1. `agents/extractor.py::_extract_schema` sends `df.head(3).to_dict(orient="records")` raw
   into `source_schemas` for every source — with 300 columns that is ~900 values per source
   fed into the Orchestrator/Transformer prompts. With multiple heterogeneous sources at once
   (the product's own stated differentiator), prompt size scales linearly with column count.
2. `core/sandbox.py`'s `execute_in_sandbox()` timeout is a fixed constant per call site
   (`ANALYST_TIMEOUT_SECONDS = 15`, `SCIENCE_TIMEOUT_SECONDS = 20`, Transformer's default
   `timeout_seconds=30`) — it does not scale with the size of the DataFrame being processed.

This ADR was not allowed to assume either point is a real bottleneck ahead of measurement.
The mandate was: build a real benchmark dataset at the stated scale, profile each stage for
real, and only fix what the numbers confirm.

### Benchmark dataset

`case_study/data/generate_benchmark.py` (new) generates a synthetic dataset reusing the
seed/null-injection/outlier-injection/duplicate-injection patterns already established by
`generate_sales.py`/`generate_orders.py`, extended to a configurable row/column count.
Default: **200,000 rows x 300 columns**, heterogeneous dtypes (~40% float with 5% nulls +
1% IQR outliers, ~25% int, ~20% low-cardinality categorical, ~10% free text, ~5% datetime),
plus a 2% exact-duplicate injection. The generated CSV (~190 MB at default scale) is
**not committed** — `case_study/data/*.csv` is already gitignored project-wide (large
case-study fixtures policy predates this ADR); only the generator script is versioned,
consistent with how `sales.csv`/`orders.csv` are also generated, not committed, artifacts.

### Profiling methodology and a real constraint: no LLM credentials in this environment

`case_study/data/profile_scale.py` (new) measures:

- **Extractor** (`load_csv` + `_extract_schema`): real I/O and real schema-serialization
  size against the full 200k x 300 dataset.
- **Sandbox execution** (point 2): real wall-clock time of `execute_in_sandbox()` running
  representative code shaped like what the Transformer/Analyst/Science prompts instruct the
  LLM to produce (clean+dedupe, groupby+aggregate+plot, sklearn `RandomForestRegressor` fit)
  — authored directly for this profiling run rather than LLM-generated, against the full
  200k x 300 dataset.
- **Quality** (`quality_node`): fully deterministic, no LLM — run for real at full scale.

**What was deliberately *not* run against the full dataset: any real LLM call.** This
worktree has no `OPENAI_API_KEY` configured (confirmed: neither the shell environment nor
`.env` sets it), so no agent that calls `get_llm()` (Orchestrator, Transformer's code
generation, Planner, Analyst, Science, Advisor) can be exercised end-to-end here at all,
independent of the sprint's own cost-consciousness guidance. This does not block measuring
either of the two confirmed points, though: point 1 is a pure string-size question (no LLM
needed to measure a JSON payload's size), and point 2 is about the *sandbox execution*
mechanics, which is exactly what `execute_in_sandbox()` runs regardless of whether the code
inside it was written by an LLM or by hand — the timeout enforcement and process-boundary
cost do not care about code provenance. Real end-to-end LLM-driven runs against this dataset
are a follow-up once a key is available in an environment authorized for that spend;
flagged as an explicit open item below, not silently skipped.

## Real measurements

Real `profile_scale.py` run against `generate_benchmark.py`'s default output — **204,000
rows x 300 columns** (200,000 base rows + 2% duplicate injection), ~567 MB CSV. Machine:
8-core / 8 GB RAM, macOS (not production hardware — see caveat in the profiling report).
Full raw JSON: `docs/work/2026-08-19-sprint12-scale-profiling.md`.

| Stage | Real measurement | Verdict |
|---|---|---|
| Extractor I/O (`load_csv`) | 12.7s, 1.48 GB peak | Not gated by any timeout; noted, not a bottleneck for this sprint's two points |
| Extractor schema size (naive, pre-fix, 1 source) | 38,837 chars / ~9,709 tokens | — |
| Extractor schema size (fixed, 1 source) | 23,828 chars / ~5,957 tokens | **38.6% reduction** |
| Extractor schema size (naive, 3 sources) | ~29,128 tokens | — |
| Extractor schema size (fixed, 3 sources) | ~17,871 tokens | Real, measured reduction — not estimated |
| Sandbox — Transformer-style (clean+dedupe) | 32.5s wall / 30s budget | **Within budget** (`timed_out: false`), but wall time exceeded the nominal 30s once process-boundary overhead is counted — no margin left |
| Sandbox — Analyst-style (groupby+agg+plot) | 13.5s wall / 15s budget | **Within budget**, but ~90% of it used on a single sub-task — no headroom for a slower attempt or a heavier aggregation |
| Sandbox — Science-style (real `RandomForestRegressor` fit) | 20.7s wall / 20s budget | **TIMED OUT** (`timed_out: true`) — this is the confirmed, reproduced failure |
| Quality (`quality_node`, deterministic) | 12.2s, 947 MB peak | Not gated by any timeout; noted, not a blocker |

## Decision

Addressed below per point, gated strictly on what the measurement showed.

### Point 1 — extractor schema size: CONFIRMED, fixed

See profiling report for exact numbers. `df.head(3).to_dict(orient="records")` over all 300
columns is the dominant contributor to `source_schemas`' serialized size, and it scales
**linearly with column count** with no cap — a source with 1,000 columns would send 1,000+
raw values into the prompt for a sample that conveys the same qualitative information a
much smaller sample would. This is confirmed disproportionate to its value: the sample's
job is to let the LLM infer formats/units, not to reproduce every column's literal value.

**Fix implemented**: `_extract_schema()` now caps the raw per-row sample to the first
`MAX_SAMPLE_COLUMNS` columns (20, chosen as enough columns for the LLM to infer format
conventions from without paying full-width cost), and adds `null_ratio` (proportional, not
just an absolute `null_counts`) so a 300-column source's quality signal doesn't require the
LLM to divide by `shape` itself. `dtypes`, `null_counts`, and `columns` remain full-width —
those are already O(n_cols) small scalars per column (a type string and an int), not
O(n_cols) *sampled values*, so they were never the problem the profiling identified. A
`sample_truncated: bool` flag is added so a consuming prompt/agent can tell when the sample
is partial, rather than silently assuming full coverage.

This is a **backward-compatible, additive schema change** to `source_schemas` — every
existing key (`columns`, `dtypes`, `shape`, `sample`, `null_counts`) keeps its exact shape
and meaning; `null_ratio` and `sample_truncated` are new keys, and `sample` shrinks in width
only for sources wider than 20 columns (every case-study source today is far narrower, so
this is a no-op for all 3 existing scenarios and their tests).

### Point 2 — fixed sandbox timeout: CONFIRMED, fixed

Real profiling reproduced the failure directly: representative Science-style code — a real
`RandomForestRegressor` fit, the exact operation Science's own prompt template instructs
the LLM to write for a regression task — **timed out** against the 204k-row benchmark at
the fixed `SCIENCE_TIMEOUT_SECONDS = 20` budget. This is not a hypothetical; it is
`execute_in_sandbox()`'s own `timed_out: true` on a real run. Representative Analyst-style
code (a single `groupby().agg()` + Plotly bar chart) did not time out, but used ~90% of its
15s budget on a *single* sub-task — with Analyst's existing 3x retry loop (ADR-007) and
`pipeline_service.py`'s multi-sub-task fan-out, that margin does not hold up under realistic
compounding. Representative Transformer-style code (median-fill + dedupe + filter) also
did not internally time out, but its measured wall time (32.5s) exceeded its own nominal
30s budget once process-spawn/pickling overhead is counted — effectively zero margin.

**Fix implemented**: `core/sandbox.py` adds `scale_timeout_for_rows(base_timeout_seconds,
n_rows)` — every existing case-study scenario (max 10,000 rows) is unaffected (no-op); above
`LARGE_DATASET_ROW_THRESHOLD = 50,000` rows, the effective timeout doubles
(`LARGE_DATASET_TIMEOUT_MULTIPLIER = 2`). `transformer.py`/`analyst.py`/`science.py` each
call it once per `run_*()` call (not per retry attempt — retries execute different LLM-generated
code, but the input size is unchanged across them) using the real row count of the DataFrame(s)
they are about to hand to the sandbox: Transformer uses the largest single extracted source
(it receives every raw source at once, unlike Analyst/Science's single merged Silver input);
Analyst/Science use `len(df)` of the Silver DataFrame they were called with.

**Why a flat step function (threshold + multiplier) instead of a continuous
row-count-proportional formula**: only one large-scale data point was measured (204k
rows) — this sprint's own cost-consciousness guidance and the absence of any
`OPENAI_API_KEY` in this environment ruled out running the *full* case-study pipeline
repeatedly at intermediate scales (50k/100k/150k rows) just to fit a curve. Fitting a
precise proportional formula to a single point would be false precision — a linear or
sqrt scaling law both pass through that one point equally well with no way to distinguish
them from this data alone. A flat 2x multiplier above a threshold set safely above every
existing scenario is simple, auditable, and demonstrably sufficient against the one
real large-scale measurement taken (Science: 20s -> 40s effective budget, comfortably
above the ~21s `RandomForestRegressor` actually needed). Refining this into a continuous
formula is a legitimate follow-up once more scale points exist — explicitly flagged, not
silently deferred.

**What was deliberately NOT changed**: the retry-loop shape (3 attempts, ADR-007), the
choice to keep timeout enforcement at the `execute_in_sandbox()` process-boundary level
(still the only mechanism proven to actually stop a hung/runaway child, per ADR-007's own
Question 2 analysis — nothing here challenges that), and the separation between
"code generation over an LLM-visible sample/stats summary" and "execution over the real
DataFrame" — that separation already exists implicitly (Analyst/Science prompts show
column stats/a small sample, but `execute_in_sandbox()` always runs the generated code
against the real, full `df`) and needed no new formalization; the sprint's own phrasing
treated this as one *candidate* fix, and the measurement pointed at timeout scaling as the
concrete, sufficient one instead.

## Consequences

- **Positive**: the extractor fix removes an unbounded prompt-size dependency on source
  width — a source with 1,000+ columns no longer sends a proportionally larger raw sample,
  capping the cost of the "amostra bruta" contribution regardless of how wide a future
  source is.
- **Positive**: the sandbox timeout fix directly closes a reproduced failure — Science-style
  code at 200k-row scale no longer times out against the same operation that failed in
  profiling, verified by the same `execute_in_sandbox()` mechanism the real agents use, with
  no signature/contract change to `execute_in_sandbox()` itself (`scale_timeout_for_rows()`
  is a new, separate, optional helper call sites opt into).
- **Positive**: this ADR's profiling harness (`case_study/data/profile_scale.py`) is reusable
  for future scale work (Sprint 15's reliability hardening, Sprint 22's "robustez a dados
  sujos do mundo real") without regenerating the measurement methodology from scratch.
- **Negative**: worst-case sandbox time at large scale increases proportionally — Science's
  3-attempt retry ceiling goes from 3 x 20s = 60s to 3 x 40s = 120s above the threshold. This
  project's execution is already asynchronous (Celery, ADR-008), so a longer worst-case wall
  time no longer blocks an HTTP request the way it would have pre-Sprint-3, but it does mean
  a pathological large-scale run occupies a Celery worker longer. Not mitigated further here —
  flagged for Sprint 15 (production reliability: explicit retry policy, worker timeout limits).
- **Negative / open item**: only one large-scale data point (204k rows) informs the
  threshold/multiplier choice — see the "why a flat step function" rationale above. Revisit
  with intermediate measurements before refining into a continuous formula.
- **Negative / open item**: no real LLM-driven run (Orchestrator → Transformer code
  generation → Analyst/Science code generation) has been exercised against the 200k x 300
  benchmark in this environment, for the credential reason stated above. The two confirmed
  code-level points are fixed/documented on real evidence, but *prompt-following quality* at
  this scale (does the LLM actually produce working code for a 300-column schema, even the
  now-compacted one?) remains unverified until a follow-up run happens with real credentials.
- **Negative**: `case_study/data/generate_benchmark.py`'s dataset is synthetic and
  structurally uniform (columns are procedurally named `metric_N`/`category_N`/...) — it
  exercises *scale* faithfully but not the *semantic messiness* of a real 300-column
  customer export (inconsistent naming, mixed encodings, genuinely dirty free text). Sprint
  22 in the roadmap already flags this exact gap ("Benchmark sintético da Sprint 12 é
  limpo; dado de cliente real não é") — not re-solved here, just acknowledged as the
  documented boundary of this ADR's scope.

## Related

- `docs/adr/ADR-007-unified-sandbox-policy.md` — the fixed-timeout design this ADR
  reassesses at scale; ADR-007 §"Interaction with the Analyst/Science retry loops" already
  flagged that the specific timeout numbers were chosen before compounding/scale were a
  consideration and asked for exactly this kind of follow-up measurement.
- `case_study/data/generate_benchmark.py`, `case_study/data/profile_scale.py` — this ADR's
  measurement tooling.
- `docs/work/2026-08-19-sprint12-scale-profiling.md` — full raw profiling report.
- `src/ai_etl/agents/extractor.py` — `_extract_schema()`, the point-1 fix.
- `src/ai_etl/core/sandbox.py`, `src/ai_etl/agents/analyst.py`, `src/ai_etl/agents/science.py`,
  `src/ai_etl/agents/transformer.py` — point-2 call sites.
