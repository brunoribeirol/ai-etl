# ADR-033: Structured Logging, Error Tracking, and a Basic SLO + Load Test

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** 34 (Phase 2 roadmap, "Observabilidade e confiabilidade de produção")

## Context

Even after the 2026-08-22 fix that added structured logging to the four
Agentic BI agents (Planner/Analyst/Science/Advisor, previously silent —
`agents/analysis/planner.py`, `services/pipeline_service.py`), this project
had:

1. No JSON/structured log output in production — every process (the FastAPI
   web app, the Celery worker) still writes plain-text `logging` lines,
   which are hard to query/aggregate in any log aggregation tool.
2. No APM/error-tracking integration at all — a production error is only
   visible via `saved_pipelines.consecutive_failures`/`last_status`
   (pipeline-level health, Sprint 15) or by reading raw logs, never with a
   stack trace, breadcrumbs, or request context in one place. The project's
   own SOC2 self-assessment already named this gap explicitly
   (`docs/compliance/soc2-readiness-assessment.md`: "no APM/error-tracking
   tool wired — e.g. no Sentry").
3. No defined SLO and no load test that exercises *concurrent multi-tenant*
   traffic — Sprint 12's benchmark (ADR-013) profiles one large dataset
   sequentially; it says nothing about what happens when several tenants run
   pipelines at once, which is the more realistic production failure mode
   for a multi-tenant SaaS.

This ADR covers all three, bundled under one number for the same reason
ADR-032 bundled four security decisions: one sprint, one theme
("observability"), and each decision below is independently readable.

---

## Decision 1 — Structured JSON logging via a custom `Formatter`, no new logging library

**Decision:** `src/ai_etl/core/logging_config.py` defines a ~30-line
`logging.Formatter` subclass (`JsonFormatter`) and a `configure_logging()`
function that attaches it to the root logger's `StreamHandler`. Called once
at bootstrap in both `api/main.py` (web process) and `core/celery_app.py`
(worker/beat process, since both import that module at startup).

**Why not `python-json-logger` (or another dependency)?** The format this
project actually needs — `timestamp`, `level`, `logger`, `message`, plus
whatever a call site passes via `extra={...}` — is small enough that a
dependency buys nothing over a plain `Formatter` subclass, and keeps this
sprint's `pyproject.toml` diff to one new dependency (Sentry, Decision 2)
instead of two. `JsonFormatter` folds every `LogRecord` attribute not on the
stdlib's own fixed set into the JSON payload, so any existing or future
`logger.warning("...", extra={"tenant_id": ...})` call gets structured
output automatically, no call-site change required.

**No existing call site changed.** Every `logger.warning(...)`/
`logger.error(...)`/`ai_etl.audit.logger.log_action(...)` call in the
codebase (including the 2026-08-22 Agentic BI fix) is untouched — this
sprint only swaps the handler/formatter attached to the root logger, so the
change is a pure formatting swap, not a re-instrumentation.

**Redaction stays with `audit/logger.py::_sanitize`.** `JsonFormatter` does
not redact — `log_action()`'s existing `key`/`token`/`secret`/`password`/
`credential` redaction is the mechanism for anything that ends up in the
audit log; plain `logging` calls must keep following the existing
non-negotiable rule (never `logger.warning(f"... {api_key}")`-shaped calls).

---

## Decision 2 — Sentry, conditional on `SENTRY_DSN`, not verified against a real account

**Decision:** `src/ai_etl/core/observability.py::init_sentry(component)`
initializes `sentry_sdk` (with `FastApiIntegration`, `CeleryIntegration`, and
a `LoggingIntegration` that auto-forwards every `logging.ERROR`+ record as an
event) only if the `SENTRY_DSN` env var is set. Unset (the default in every
environment except a deployed one with a real Sentry project configured)
makes it a documented no-op — same "optional, never breaks if absent"
convention `services/notifications.py` already established for Resend/
Slack/Teams/Google Chat, and `services/secrets_service.py` for
`AI_ETL_SECRETS_ENCRYPTION_KEY`.

`sentry-sdk[celery,fastapi]` was added as a **base dependency** (not an
extra) — both the API process and the Celery worker need it importable
unconditionally at bootstrap, same rationale already applied to
`langchain-anthropic`/`-google-genai`/`-ollama` in `pyproject.toml`.

`component` (`"api"` | `"worker"`) is set as a Sentry tag so errors from the
two processes stay distinguishable in one Sentry project rather than needing
two.

**Honest limitation, stated explicitly per this project's own precedent for
missing credentials (Sprint 8's `OPENAI_API_KEY`/Ollama constraint):** no
Sentry account or DSN is available in this development environment.
`init_sentry()`'s conditional-init contract (DSN unset -> no-op; DSN set ->
`sentry_sdk.init` called with the right kwargs) is verified by
`tests/unit/test_observability.py` via mocked `sentry_sdk` calls — **an
actual error event reaching a real Sentry dashboard has NOT been confirmed**
and must not be read as verified by this ADR. The "definition of pronto" in
the roadmap entry ("um erro em produção aparece numa ferramenta de
observabilidade") is code-complete but operationally unverified until a real
`SENTRY_DSN` is configured in a deployed environment and an error is
observed there.

**Sampling.** `traces_sample_rate` defaults to `0.1` (10%) via
`SENTRY_TRACES_SAMPLE_RATE` — every error is still captured (that's not
sampled), only performance traces are, to stay inside a free-tier event cap
at low traffic.

---

## Decision 3 — SLO definition and a real (mocked-LLM) concurrent load test

### SLO

Defined from this project's own existing measured baselines — not invented —
specifically Sprint 8's `stage_durations` instrumentation
(`case_study/results/sprint8/comparison_runs.csv`) and Sprint 28's
`baseline_metrics.json` (`elapsed_ms` per scenario, mocked LLM). Sprint 8's
per-stage columns (`stage_orchestrator_s`, `stage_extractor_s`,
`stage_transformer_s`, `stage_quality_s`, `stage_loader_s`) show the
Transformer's sandboxed `multiprocessing.Process` spawn (ADR-007) dominating
total latency (4.7s-14.3s across runs of the *same* model, same mocked
input) while every other stage stays sub-second. The SLO below reflects that
shape rather than a single flat number:

| Stage | p95 latency SLO | Rationale |
|---|---|---|
| orchestrator | ≤ 1.0s | LLM call, mocked here; real-LLM p95 depends on provider round-trip, out of scope for this sprint's numbers (flagged pending below) |
| extractor | ≤ 1.0s | Deterministic I/O + schema inspection, no LLM call |
| transformer | ≤ 20.0s | Sandbox `multiprocessing.Process` spawn is the project's known dominant cost (Sprint 8's 4.7s-14.3s range) — set generously above the observed max to absorb concurrency-driven contention, not to hide a regression |
| quality | ≤ 1.0s | Deterministic checks (nulls/duplicates/outliers), no LLM call |
| loader | ≤ 1.0s | Deterministic CSV/Postgres write |

**Error rate SLO: ≤ 1%** — chosen as a standard, conservative first target
for a pre-revenue TCC-stage product (no existing production error-rate
baseline to derive a tighter number from); revisit once real production
traffic accumulates.

These thresholds live in `case_study/scripts/load_test.py::
SLO_P95_STAGE_SECONDS`/`SLO_ERROR_RATE_MAX`, not just this document, so a
load-test run is graded against the same numbers this ADR states.

### Load test — why a custom script over Locust

**Decision:** a custom script (`case_study/scripts/load_test.py`), not
Locust/k6, reusing this project's own established pattern (Sprint 8's
`model_comparison.py`, Sprint 28's `regression_harness.py`) of calling
`pipeline_service.run_full_analysis` directly with mocked LLM responses and
no-op'd audit persistence, bypassing Celery/Redis/Postgres — this sandbox
has neither reachable (no Docker daemon, same constraint every prior
sprint's scripts document).

**Trade-off considered:** Locust/k6 are the standard-of-market tools for
this and would exercise a real HTTP layer end-to-end — but this project's
API layer has no unauthenticated load-testable endpoint that triggers a full
pipeline run without a real Clerk JWT and a real Celery/Redis/Postgres stack
none of which are reachable here. A custom script calling the orchestration
function directly (same function the Celery task and the API route both
wrap) measures the actual pipeline execution contention this SLO cares about
without inventing auth/infra plumbing this sandbox can't run anyway. Revisit
Locust/k6 once a real staging deployment exists to point them at.

**Concurrency model:** `ThreadPoolExecutor`, N simulated tenants running
concurrently (default 10), each issuing several sequential requests — real
Python-level thread concurrency (GIL contention, real sandbox process
spawning) against the real Silver LangGraph, not simulated numbers.

**A real bug found while building it:** the first version patched the mocked
LLM (`unittest.mock.patch`) per request, inside each worker thread —
`unittest.mock.patch` mutates the target module's attribute process-wide,
not thread-locally, so concurrent threads' patch/unpatch cycles raced each
other, intermittently handing a request the real (uncredentialed) `get_llm`
and failing with `OpenAIError: Missing credentials` — reproduced at 2/10
requests failing in an early run. Fixed by patching once, process-wide,
before the thread pool starts, with the orchestrator's mocked response built
dynamically per call from that call's own prompt text (so concurrent
tenants' distinct output paths still don't collide despite one shared
patch). See `load_test.py`'s module docstring and
`case_study/results/sprint34/README.md` for the full writeup — this is a
genuine (if small) concurrency-safety finding, not just script plumbing.

### Result (this session — mocked LLM, see full caveats in `case_study/results/sprint34/README.md`)

10 concurrent tenants x 3 requests each (30 total), mocked LLM (no
`OPENAI_API_KEY` in this environment): **0/30 errors (0.0%)**, every stage's
p95 within its SLO (extractor 0.092s, transformer 2.916s, quality 0.046s,
loader 0.033s, orchestrator 0.001s — all well under their thresholds above).
Full data: `case_study/results/sprint34/load_test_summary.json` /
`load_test_requests.json`.

**What this confirms for real:** the deterministic, non-LLM stages
(extractor I/O, sandboxed transform execution, Quality Agent, Loader I/O)
handle 10-way concurrency inside this sandbox with zero errors and
comfortable SLO headroom, and the concurrency-safety bug above is fixed.

**What remains explicitly pending, not claimed as done:**
- **Real LLM latency under concurrent load** — provider-side rate limiting,
  queueing, and round-trip time at 10+ simultaneous requests is unmeasured;
  requires a real `OPENAI_API_KEY` and a rerun (script supports it once
  mocking is made conditional the way `model_comparison.py` already is —
  currently `load_test.py` always mocks, a follow-up if a real-LLM load test
  is prioritized).
- **The Celery/Redis/Postgres hop itself under concurrent load** — this
  script bypasses it entirely (see trade-off above); a real deployed
  environment's queue depth/worker pool behavior under concurrent enqueue is
  unmeasured.
- **Production-scale concurrency** (dozens-hundreds of simultaneous tenants,
  Railway's actual container CPU/memory limits) — 10 concurrent threads in a
  local sandbox is a first data point, not a production capacity ceiling.
- **An actual Sentry event confirmed reaching a real dashboard** (Decision 2).

---

## Consequences

- Every log line in the API and worker processes is now a single JSON
  object — ready for any log aggregation/query tool without further code
  change, with zero call-site changes to existing `logger.*`/`log_action()`
  calls.
- Error tracking is one `SENTRY_DSN` env var away from being live in any
  deployed environment; today (no DSN configured anywhere) it remains a
  documented no-op, matching every other "optional integration" in this
  project.
- A documented, numeric SLO exists for the first time, derived from this
  project's own real measured baselines (Sprint 8/28) rather than invented,
  with a reproducible script to grade future changes against it.
- The load test's own finding (unsafe per-thread `unittest.mock.patch`) is a
  small but real reminder that this project's existing benchmark scripts
  (`model_comparison.py`, `regression_harness.py`) are safe *because* they
  run sequentially — any future script that adds concurrency to them needs
  the same fix applied here.
