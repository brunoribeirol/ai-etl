# ADR-041: Circuit Breaker Around LLM Provider Calls

**Status:** Accepted
**Date:** 2026-08-27
**Sprint:** Wave 3 (post-audit strategic decisions, `docs/work/2026-08-26-strategic-decisions-execution-plan.md`)

## Context

An X-PRO.ai gap-analysis run against a real AI-ETL profile (criticality=10,
complexity=7, `privacy_law` flag — see `docs/CURRENT_STATE.md`'s 2026-08-27
entry) surfaced several resilience gaps, one of which the owner approved for
immediate, narrowly-scoped execution: **no circuit breaker or bulkhead around
this project's actual single point of external, third-party dependency on
every hot path** — the LLM provider call.

Every one of `core/llm.py`'s eight call sites (`agents/pipeline/orchestrator.py`,
`agents/pipeline/transformer.py`, `agents/analysis/planner.py`,
`agents/analysis/analyst.py`, `agents/analysis/science.py`,
`agents/analysis/advisor.py`, `agents/analysis/reviewer.py`) calls
`llm.invoke(prompt)` directly. Each of these already sits inside its own
node-local retry-and-repair loop (Transformer/Science: `MAX_ATTEMPTS` = 3,
feeding the error back into the next prompt; Orchestrator: 2 attempts on
invalid JSON) — but that loop has no memory across pipeline runs or across
concurrent tenants. If a provider has a regional incident or a rate-limit
storm, every node, every run, every tenant independently rediscovers that by
making its own slow, failing `.invoke()` call and burning through its own
retry budget — there is no shared, cheap "this provider is currently down,
don't bother" signal.

This is explicitly a narrow fix for that one gap, not the broader resilience
overhaul (bulkhead isolation across agent types, OpenTelemetry-driven SLO
alerting, contract tests) the same gap analysis also surfaced and the owner
deliberately deferred — see `docs/CURRENT_STATE.md`'s Wave 3 entry and
`docs/work/2026-08-26-strategic-decisions-execution-plan.md` for the full
triage.

## Decision

Add `invoke_llm(llm, prompt, provider)` to `core/llm.py` — a thin, per-provider
circuit breaker wrapping `BaseChatModel.invoke()` — and switch all seven agent
call sites (everywhere except `test_provider_connectivity`, see below) from
`llm.invoke(prompt)` to `invoke_llm(llm, prompt, <that call site's own already-
resolved provider variable>)`.

**States, in-process, per provider (not per model, per tenant, or per node):**

- **CLOSED** (default): calls pass straight through. A success resets the
  consecutive-failure counter to 0. A failure increments it; once it reaches
  `AI_ETL_LLM_CIRCUIT_FAILURE_THRESHOLD` (default 5), the circuit opens.
- **OPEN**: every call fails immediately with `LLMCircuitOpenError` — no
  network call is made — until `AI_ETL_LLM_CIRCUIT_COOLDOWN_SECONDS` (default
  60) has elapsed since it opened.
- **Half-open probe**: once the cooldown elapses, the next call is let through
  as a real `.invoke()`. Success closes the circuit (counter reset). Failure
  re-opens it and restarts the cooldown.

**Why per-provider, not per-model or per-tenant.** A provider outage is a
provider-level event (OpenAI down affects every OpenAI model/tenant
simultaneously) — a finer key would fragment the signal without adding real
protection, and a coarser one (one global circuit) would incorrectly trip a
healthy provider's calls just because a different provider (e.g. a tenant's
Ollama override) is failing. This matches the project's existing multi-
provider posture (ADR-014/ADR-031): providers are already independent,
per-request-selectable units everywhere else in this codebase.

**Why in-process, in-memory state — not Redis-backed, not a library
(`pybreaker`, `circuitbreaker`).** Every existing call site already runs
inside one of two processes (the FastAPI web process for synchronous request
paths, the Celery worker process for async pipeline runs) — no cross-process
sharing was in scope for this narrow fix, and Redis is already a dependency
(`ADR-008`, Celery broker + rate limiting) but wiring per-provider circuit
state through it is exactly the kind of generalization this Wave 3 triage
deferred. A dependency was considered and rejected: the entire mechanism is
~90 lines of `threading.Lock` + a per-provider mutable struct, well under the
bar this project's own `ADR-033` Decision 1 set for "a dependency buys
nothing over a small hand-written class here." Revisit a shared/distributed
breaker only if/when multiple worker processes need to observe the same
provider's health, which isn't the case today (Railway currently runs one
`ai-etl` API service instance and one `tranquil-appreciation` worker service).

**Why `LLMCircuitOpenError` is a `RuntimeError` subclass, not a new exception
family.** So every existing `except Exception`/broad retry-catch in
Transformer/Orchestrator/Analyst/Planner/Advisor/Reviewer/Science keeps
working completely unmodified — those loops still catch it, still record
`last_error`, still exhaust their own attempt budget exactly as before. This
ADR changes *how fast* a doomed call fails, never *what* callers see or how
they react to failure.

**`test_provider_connectivity()` deliberately bypasses the breaker.** That
function exists specifically so a tenant can diagnose/fix a broken provider
configuration (`POST /llm/test-connectivity`, ADR-031) — always making a real
call, never failing fast on stale circuit state, is the entire point of that
endpoint. Already documented as "deliberately independent of `get_llm()`" in
its own docstring; this ADR keeps that independence.

**Configuration:** two env vars, both optional with safe defaults —
`AI_ETL_LLM_CIRCUIT_FAILURE_THRESHOLD` (default 5) and
`AI_ETL_LLM_CIRCUIT_COOLDOWN_SECONDS` (default 60) — same "env var override,
sane default, zero required config" convention every other tunable in this
module already follows (`AI_ETL_LLM_PROVIDER`, `AI_ETL_LLM_MODEL`).

## Consequences

- A provider outage now degrades gracefully across the whole deployment: once
  5 consecutive calls to a provider fail, every subsequent call (any node, any
  tenant, any concurrent pipeline run) fails in microseconds instead of
  waiting out a real network timeout, for up to 60s at a time, until the
  provider recovers.
- Zero behavior change for the common case (provider healthy): `invoke_llm()`
  passes through to the exact same `.invoke(prompt)` call every existing test
  already mocks — confirmed by running the full unit+integration suite
  unmodified except for the new circuit-breaker test class itself (1043
  passed, 6 skipped, no other test touched).
- New module-level global state in `core/llm.py` (`_circuit_state`, guarded by
  `_circuit_lock`) — test isolation required a new autouse fixture
  (`tests/conftest.py::_reset_llm_circuit_breakers`) resetting it between every
  test, the same reason `test_llm.py`'s `_clean_llm_env` fixture already
  resets provider-related env vars.
- **Explicitly out of scope, not silently assumed:** cross-process/distributed
  circuit state (see rationale above); a per-tenant or per-model breaker
  granularity; any change to the existing node-local retry/repair loops
  themselves; metrics/alerting on circuit state transitions (a real follow-up
  once `ADR-033`'s Sentry integration has a live DSN to report to).
