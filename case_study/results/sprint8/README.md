# Sprint 8 — Model Comparison + Stability

**Date:** 2026-08-19
**Script:** `case_study/scripts/model_comparison.py` (reproducible, see "How to reproduce" below)
**Scope:** CLI/headless only — no `api/` or `frontend/` code touched, per the sprint brief.

---

## Honest headline: this session has no real model-comparison data

Before anything else, the two hard constraints that shaped every number below:

- **No `OPENAI_API_KEY` in this sandbox** (`env | grep -i openai` returns nothing).
- **No `ollama` binary in this sandbox** (`which ollama` fails).
- **No Docker daemon** (`docker ps` fails with "Cannot connect to the Docker
  daemon") — so neither the test Postgres nor Redis used by
  `tests/e2e/conftest.py` is reachable either.

Per the sprint brief ("se Ollama não estiver disponível... documente isso
explicitamente e simule/mocke com clareza, não finja que rodou"), every run
in `comparison_runs.csv` is tagged with a `data_source` column:

| `data_source` | Meaning |
|---|---|
| `real` | Ran against a real provider with the given model. **Not used this session — no key/binary available.** |
| `mock` | An OpenAI-shaped model (`gpt-4o-mini`, `gpt-4o`), no key available — deterministic mocked LLM responses, `cost_usd = 0.0` (real pricing formula, zero real tokens), near-zero LLM latency. |
| `simulated` | The `ollama:llama3.1` slot — this script does **not** invent latency/quality numbers for a model it never ran. It still executes the same mocked pipeline (proving the harness generalizes to a 3rd provider) but reports `cost_usd = None` (self-hosted models have no per-token API price — that's a real fact, not a fabricated number) and every metric is illustrative-only. |
| `skipped_no_infra` | Scenarios 2-4, which need a reachable Postgres for their join/REST sources — unavailable in this sandbox. |

**What this session actually validates**: the full comparison/stability
harness — real Silver LangGraph execution, real sandboxed transform, real
Quality Agent, real `stage_durations` latency instrumentation, real
`core/pricing.py` cost formula, and an objective quality-scoring function —
runs correctly end to end and produces internally-consistent, reproducible
output. **It does not tell you which model is better, faster, or cheaper** —
that requires a real `OPENAI_API_KEY` (and a running Ollama with a pulled
model) and a rerun with zero code changes (`data_source` flips to `real`
automatically once credentials/binaries are present).

---

## What was run

**Scenario 1 only** (CSV-only, `case_study/data/sales.csv`, 5000 rows with
injected nulls/duplicates/outliers — the same dataset
`case_study/data/generate_sales.py` produces for the original case study).
Scenarios 2-4 additionally require a reachable Postgres
(`postgres-test:5433`) for their join/REST sources, matching
`tests/e2e/conftest.py::requires_full_stack` — unreachable here (no Docker).
The script attempts them and records one `skipped_no_infra` row per
(model, scenario) rather than silently omitting them.

**3 model slots** (`--models`, default): `gpt-4o-mini`, `gpt-4o`,
`ollama:llama3.1`.

**N = 5 runs per model** (`--n`, default). Rationale: matches this project's
own established convention (`case_study/results/tabela-resultados.md`, 5
runs/scenario for the original 3-scenario case study) rather than inventing a
new N; large enough to compute a usable sample standard deviation (n-1 = 4
degrees of freedom) without, in a real-key rerun, burning a disproportionate
amount of API cost for a TCC-scale experiment. Increase `--n` for a tighter
confidence interval once real billing is in play and the owner is comfortable
with the added cost.

**Method**: calls `ai_etl.services.pipeline_service.run_full_analysis`
directly (the same function `services/execution_queue.py`'s
`run_full_analysis_task` wraps for Celery) instead of going through
`enqueue_analysis`/Celery/Redis, and monkeypatches `save_run`/`save_analysis`/
`save_stage_latencies` to no-ops (both need a reachable
`APP_DATABASE_URL` Postgres with `ON CONFLICT` upserts that don't work
against SQLite — unavailable here). Everything upstream of persistence — the
real Silver LangGraph, real sandboxed transform, real Quality Agent, latency,
cost, quality score — runs for real. Pass `--persist-audit` once Postgres is
reachable to let the audit trail write too.

---

## Quality metric — how "quality" is defined here

`score_quality()` (`case_study/scripts/model_comparison.py`) is a
deterministic, 0-100 score computed only from signals the *real* (non-mocked)
Silver pipeline stages produce — so it stays meaningful even though the LLM's
plan/code content is currently mocked:

| Component | Points | Rule |
|---|---|---|
| Pipeline completed | 40 | Hard gate — `status != "completed"` scores 0 total. |
| Quality Agent severity | 30 | `ok`=30, `warning`=18, `error`=0. |
| Completeness | 20 | `rows_loaded / reference_rows` (capped at 1.0) if a reference count is given; flat 20 if `rows_loaded > 0` and no reference. |
| Transformer efficiency | 10 | 1 attempt=10, 2=5, ≥3=0 (mirrors the real retry cap in `agents/transformer.py`). |

Weights are chosen for interpretability, not statistically tuned (no labeled
dataset exists at this project's scale to tune against) — documented in the
function's own docstring for anyone who wants to re-weight them. Unit-tested
in `tests/unit/test_model_comparison.py::TestScoreQuality` (9 cases: gate
failure, each severity, zero/partial/over-100%-reference completeness, each
attempt-count band, missing-field defaults).

---

## Results (this session — mocked/simulated, see caveat above)

All 15 Scenario-1 runs (3 model slots × 5 runs) completed successfully.
Full per-run data: `comparison_runs.csv`. Full stability breakdown:
`stability_summary.json`.

| Model | data_source | rows_loaded (all runs) | quality_score (all runs) | elapsed_ms mean | elapsed_ms stdev | elapsed_ms CV | cost_usd |
|---|---|---|---|---|---|---|---|
| gpt-4o-mini | mock | 3649 (stdev 0) | 88.0 (stdev 0) | 15001 | 6662 | 0.44 | $0.00 (mock) |
| gpt-4o | mock | 3649 (stdev 0) | 88.0 (stdev 0) | 11282 | 724 | 0.06 | $0.00 (mock) |
| ollama:llama3.1 | simulated | 3649 (stdev 0) | 88.0 (stdev 0) | 12093 | 1032 | 0.09 | n/a (self-hosted) |

### Reading these numbers correctly

- **`rows_loaded` = 3649 for every single run, across every model, exactly
  matching the original case study's Scenario 1 result**
  (`tabela-resultados.md`: "3 649" rows loaded, all 5 runs). This is a real,
  useful cross-check — it confirms the harness reproduces the established
  Scenario 1 behavior — but it is a property of the *dataset and
  transformation logic* (deterministic, seed=42), not of the LLM, so it says
  nothing about model comparison.
- **`quality_score` = 88.0 with zero variance** for the same reason: the
  Quality Agent's severity and the Loader's row count are both deterministic
  given the fixed mocked transform code, independent of which model name is
  in the `data_source=mock`/`simulated` rows.
- **`cost_usd` = $0.00 for the OpenAI-shaped rows is not "cheap" — it's zero
  real tokens.** `core/pricing.py`'s real formula was exercised (proving the
  cost-computation code path works), but with `input_tokens=output_tokens=0`
  it can only ever return `$0.00`. `ollama:llama3.1`'s `cost_usd = None`
  reflects the real absence of a per-token API price for a self-hosted model
  (a legitimate data point) rather than "free to run" (compute/hosting cost
  is real and simply out of scope for this pricing formula).
- **`elapsed_ms` variance (stdev 724-6662ms, CV 0.06-0.44) is real wall-clock
  noise from this specific sandboxed environment** — `stage_transformer_s` in
  the raw CSV ranges roughly 4.7s-14.3s across runs of the *same* model,
  almost certainly `multiprocessing.Process` spawn overhead in
  `core/sandbox.py` (ADR-007) under this session's CPU contention, not LLM
  inference latency (the mocked LLM call itself resolves in well under a
  millisecond). **Do not read this as "gpt-4o-mini is slower than gpt-4o"** —
  with the LLM mocked, both models run the identical code path; the observed
  spread is sandbox/OS scheduling noise, and a real run's total latency would
  be dominated by actual LLM round-trip time instead.

---

## Stability experiment

Variance across the 5 identical (same model, same scenario, same mocked
inputs) runs per model — `compute_stability()`, unit-tested in
`TestComputeStability` (6 cases, including single-run and all-failed edge
cases). Under mocking, `rows_loaded` and `quality_score` show **zero**
variance for all three models, exactly as expected: the mocked LLM response
is fixed, so the deterministic parts of the pipeline (sandbox transform,
Quality Agent, Loader) produce identical output every time. This is the
correct null result for a mocked run, not evidence of real-world stability —
**real stability only has meaning once LLM output can actually vary run to
run**, i.e. with a real API key. `elapsed_ms` shows non-zero variance driven
by sandbox process-spawn noise (see above), which is a real (if
model-comparison-irrelevant) observation about this environment.

**What a real rerun would additionally measure**: real token-count variance
(same prompt, different completions across runs — GPT is not deterministic at
temperature 0.0 in practice, per OpenAI's own docs), real
`transformation_attempts` variance (does the model need a different number of
sandbox retries run to run?), and real `quality_score` variance (does a
different generated transform occasionally trip the Quality Agent's
severity?). None of that is observable without real model calls.

---

## Scenarios 2-4 — not executed this session

`skipped_no_infra` rows in `comparison_runs.csv` for both `gpt-4o-mini` and
`gpt-4o` (9 rows total: 3 models × 3 scenarios). Scenario 2 (CSV+Postgres),
3 (+REST), and 4 (+PDF/DOCX) all need `postgres-test` reachable for their
join/lookup sources, exactly like `tests/e2e/test_scenario{2,3,4}_*.py`'s
`@requires_full_stack` marker — this sandbox has no Docker daemon to start
it. The script supports all 4 scenarios' plumbing (see
`_postgres_reachable()`); only Scenario 1 needed no external infra to run
here.

---

## How to reproduce (with real data)

```bash
# 1. Generate the Scenario 1 dataset (fixed seed=42, reproducible):
uv run python case_study/data/generate_sales.py

# 2. With a real key, for real cost/latency/quality numbers:
export OPENAI_API_KEY=sk-...
uv run python case_study/scripts/model_comparison.py \
  --models gpt-4o-mini,gpt-4o \
  --n 5

# 3. For a real Ollama comparison point, additionally:
#    - install/start Ollama, `ollama pull llama3.1`
#    - re-run with --models gpt-4o-mini,gpt-4o,ollama:llama3.1
#      (note: this script's `ollama:` support is currently limited to the
#      "simulated" slot documented above -- see "Known limitation" below)

# 4. For Scenarios 2-4, first bring up the e2e test infra:
docker compose up -d postgres-test redis app-postgres-test
uv run python case_study/scripts/model_comparison.py --n 5   # picks up Postgres automatically
```

Output regenerates `comparison_runs.csv` and `stability_summary.json` in this
directory. Delete `case_study/results/sprint8/_runs/` afterward (per-run
scratch CSVs, not committed) or leave it — it is gitignored.

### Known limitation: no real Ollama client wired up yet

This script's `ollama:*` model slot always runs the mocked pipeline — there
is **no `langchain_ollama.ChatOllama` (or equivalent) call site** in
`core/llm.py` today. `get_llm()` only ever constructs a `ChatOpenAI`.
Wiring in a real local-model provider is a genuine architecture decision
(new provider abstraction, a `AI_ETL_LLM_PROVIDER` env var or similar,
`get_llm()`'s contract changing for every one of its ~6 call sites) — it was
**deliberately not implemented untested** in this session, since there is no
Ollama binary here to verify it against, and the sprint brief said to
document the gap rather than fake it. If/when Ollama comparison is wanted for
real: that's an ADR-worthy decision (see `docs/adr/`, next number after
ADR-011) for a future session with Ollama actually available to test against.

---

## Verification run in this session

- `uv run ruff check` — clean on `src/`, `case_study/scripts/`,
  `tests/unit/test_model_comparison.py` (21 pre-existing findings in
  `case_study/baselines/*.ipynb` notebooks, untouched by this work).
- `uv run ruff format --check` — clean on all files touched this session.
- `uv run mypy --strict src/ case_study/scripts/model_comparison.py
  tests/unit/test_model_comparison.py` — 0 errors (this session's local mypy
  ran without the previously-documented sandbox hang — see
  `docs/CURRENT_STATE.md`'s "Known risks" for that recurring issue; CI
  remains the authoritative gate regardless).
- `uv run pytest tests/unit` — **292 passed**, including 16 new tests in
  `tests/unit/test_model_comparison.py` (`score_quality`, `compute_stability`,
  `_provider_and_access` — the script's pure/testable logic; the I/O-heavy
  orchestration (`run_scenario1`, `main`) was exercised manually, twice,
  producing this directory's committed output).
- `uv run bandit -r src/ case_study/scripts/` — 2 findings, both the
  project's pre-existing, documented `exec()` sites in `core/sandbox.py`
  (ADR-007); nothing new.
- `uv run pip-audit` — no known vulnerabilities.
- `tests/integration` and `tests/e2e` were **not run** this session — both
  need a reachable Postgres/Redis (no Docker daemon in this sandbox); CI
  covers them on every push.
