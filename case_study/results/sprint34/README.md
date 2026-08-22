# Sprint 34 — Load Test vs. SLO (ADR-033)

**Date:** 2026-08-22
**Script:** `case_study/scripts/load_test.py` (reproducible, see "How to reproduce" below)
**SLO defined in:** `docs/adr/ADR-033-observability-slo.md`

---

## Honest headline: real concurrency, mocked LLM

- **No `OPENAI_API_KEY` in this sandbox** — same constraint every prior
  sprint's scripts in this directory document (`sprint8/README.md`,
  `sprint28/baseline_metrics.json`'s `data_source: "mock"`). Every LLM call
  is mocked with a deterministic, near-instant response.
- **Real concurrency, real code path** — `ThreadPoolExecutor` runs
  `pipeline_service.run_full_analysis` directly (bypassing Celery/Redis, same
  as `model_comparison.py` — no reachable broker/Postgres here), so the real
  Silver LangGraph, real sandboxed transform execution
  (`multiprocessing.Process`, ADR-007), real Quality Agent, and real
  `stage_durations` instrumentation all run under genuine multi-thread
  contention.
- **What this validates**: per-stage p50/p95/p99 latency and error rate
  *under N simultaneous tenants*, graded against ADR-033's SLO — a question
  Sprint 12's `profile_scale.py` (one dataset, sequential, single tenant)
  cannot answer.
- **What this does not validate**: real LLM latency/throughput under
  concurrent load (provider-side rate limits, queueing) — that needs a real
  `OPENAI_API_KEY` and is flagged pending in ADR-033.

## A real bug found while building this script

The first version patched each mocked LLM call site *inside* each worker
thread (`unittest.mock.patch`, same as `model_comparison.py`). Under real
concurrency this is unsafe — `patch` mutates the target module's attribute
process-wide, not thread-locally, so one thread's teardown can race another
thread's still-in-flight call. Reproduced at 5 tenants x 2 requests: 2/10
requests failed with `OpenAIError: Missing credentials` (a real,
unpatched `get_llm()` call slipped through mid-race). Fixed by patching once,
process-wide, before the thread pool starts — see `load_test.py`'s module
docstring for the full explanation. This is itself a small, real
concurrency-testing finding, not just infrastructure noise.

---

## Result — 10 concurrent tenants x 3 requests each (30 total)

Full data: `load_test_summary.json` (aggregates) / `load_test_requests.json`
(per-request raw).

| Stage | p50 | p95 | p99 | SLO (p95) | Within SLO |
|---|---|---|---|---|---|
| orchestrator | 0.000s | 0.001s | 0.002s | ≤ 1.0s | yes |
| extractor | 0.053s | 0.092s | 0.112s | ≤ 1.0s | yes |
| transformer | 2.626s | 2.916s | 2.990s | ≤ 20.0s | yes |
| quality | 0.011s | 0.046s | 0.055s | ≤ 1.0s | yes |
| loader | 0.010s | 0.033s | 0.050s | ≤ 1.0s | yes |

**Error rate: 0/30 (0.0%)**, within the ≤1% SLO.

Total per-request wall time: p50 5.80s, p95 6.71s, p99 6.77s (dominated by
the Transformer's sandbox `multiprocessing.Process` spawn — consistent with
Sprint 8's finding that this, not LLM latency, is the sandbox's main
overhead source; see `sprint8/README.md`).

### Reading this correctly

- Every LLM-touching stage (orchestrator, transformer's *code content*) is
  near-instant here because the LLM itself is mocked — this run stresses the
  *deterministic* stages (extractor I/O, sandbox execution, Quality Agent,
  Loader I/O) under contention, not LLM throughput.
- 0% error rate under 10-way concurrency, with the one race condition found
  during development (see above) already fixed, is a real, meaningful
  result for this sandbox's non-LLM code paths.
- All five stages ran comfortably inside ADR-033's SLO at this concurrency
  level in this environment. This does not prove the SLO holds at
  production-realistic concurrency (10s-100s of tenants), against a real
  LLM provider, or on Railway's actual container resources — see ADR-033
  for what's explicitly flagged pending.

---

## How to reproduce

```bash
uv run python case_study/data/generate_sales.py   # once, if case_study/data/sales.csv is missing
uv run python case_study/scripts/load_test.py --tenants 10 --requests-per-tenant 3
```

Add `OPENAI_API_KEY` (and drop the mocked-LLM path — would need a script
change, currently hardcoded to mock, see `load_test.py`'s docstring) for a
real-LLM rerun; add `--persist-audit` once `APP_DATABASE_URL` is reachable to
let the audit trail write for real during the load test too.
