# ADR-008 — Asynchronous execution via Celery + Redis, shared with per-tenant rate limiting

**Status:** Accepted
**Date:** 2026-08-15
**Deciders:** Bruno Ribeiro

---

## Context

`src/ai_etl` is currently 100% synchronous — no `async`/`asyncio` anywhere in the codebase. The Streamlit app blocks the UI thread for the full duration of a pipeline or analysis run. Sprint 3's scope (Vault: `artefact/sprint-roadmap.md`) is:

1. **Asynchronous execution** — take the pipeline off Streamlit's blocking click handler.
2. **Per-tenant rate limiting** — bound how often a tenant can trigger runs.
3. **Cost per execution** (metric 3 of the evaluation framework) — multiply the existing `TokenUsage` by model pricing, persisted per run.

Deploy target is Railway, which has a managed Redis addon billed under the same project plan as everything else already deployed (no separate cost tier).

## Options considered

| Option | Advantages | Disadvantages |
|---|---|---|
| **RQ (Redis Queue)** | Minimal setup (~20 lines of config), fits a synchronous codebase directly with no restructuring, low learning curve, single worker process | Weaker native retry/rate-limiting primitives (would need custom Redis-backed logic for rate limiting anyway); less common in large production stacks |
| **Celery + Redis** (chosen) | Mature, de facto standard for Python production task queues; native per-task retry policies and rate limiting; Flower available for monitoring; native rate-limiting primitives directly reusable for the Sprint 3 rate-limiting requirement, not just async execution | More setup/config complexity than RQ (app factory, task routing, serializer config); a separate worker process needs its own deploy/process management on Railway |
| **Arq (asyncio-native)** | Modern, lightweight, asyncio-first API | The codebase is fully synchronous — adopting Arq would require restructuring the pipeline to `async`/`await` solely to use the queue, with no other benefit to this project |

## Decision

Use **Celery with Redis as the broker** for asynchronous pipeline/analysis execution. Reuse the same Redis instance as the backing store for per-tenant rate limiting, avoiding a second piece of infrastructure for two related Sprint 3 requirements.

## Rationale

This project is being built as both a TCC academic deliverable and a candidate foundation for a real SaaS (Vault: `artefact/saas-potential.md`, owner's entrepreneurial goals). Celery is the choice a technical reviewer, hiring manager, or investor recognizes as a production-grade decision for Python task queues, and its native retry/rate-limiting primitives are directly reusable for the rate-limiting deliverable in this same sprint — not just execution. RQ would close Sprint 3 faster with less setup, but Celery was chosen deliberately, weighing that trade-off explicitly, in favor of the piece that also serves the post-TCC SaaS direction.

## Consequences

- **Positive**: pipeline/analysis execution no longer blocks the Streamlit UI thread; the app can poll job status instead.
- **Positive**: Celery's native `rate_limit` and retry-policy primitives are reused directly for per-tenant rate limiting — no second bespoke implementation needed.
- **Positive**: Flower is available for queue/worker observability without extra bespoke tooling.
- **Negative**: new infra dependency (Redis) added to `docker-compose.yml` for local dev and to Railway (managed addon) for production.
- **Negative**: a Celery worker process needs its own deploy/process management on Railway, separate from the existing Streamlit web process — increases deploy surface versus the current single-process app.
- **Negative**: more setup/config complexity than RQ would have required, accepted deliberately for the reasons above.
- **Neutral**: `Celery` + `redis-py` added to `pyproject.toml`; no change to the existing sandbox/auth/tenancy architecture — purely additive at the execution layer.

## Review trigger

Revisit if Railway's Redis addon pricing changes materially, or if the operational cost of running a separate Celery worker process proves disproportionate to the project's actual load before Sprint 6 (model comparison / stability testing, which will exercise this queue under repeated load).

## Related

- [ADR-007](ADR-007-unified-sandbox-policy.md) — the sandboxed execution this queue now wraps asynchronously; no change to sandbox internals.
- [ADR-001](ADR-001-langgraph-orchestration.md) — the pipeline graph this ADR moves off the blocking call path, unchanged internally.
- Vault: `artefact/sprint-roadmap.md` — Sprint 3 scope this ADR implements.
- Vault: `decisions/async-execution-celery-redis.md` — linked decision note.
