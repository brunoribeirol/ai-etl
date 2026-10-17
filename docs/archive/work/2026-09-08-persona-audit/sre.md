# SRE — Operational Readiness

**Date:** 2026-09-09. Mode: live read-only curls + code reads. No writes to prod.

Production is live: `https://ai-etl.vercel.app` (200), Railway API `/health` (200).

## Findings

### SRE-1 — `/health` is a static stub; no dependency checks (MEDIUM)
`api/main.py:66-68`: `def health(): return {"status": "ok"}`. Confirmed live:
`curl .../health -> {"status":"ok"}`. If Postgres, Redis, or the LLM provider is down but
the FastAPI process is up, `/health` still returns 200 and Railway keeps routing traffic to
a broken instance. There is no readiness/deep check (`/health/ready` that pings DB/Redis).
Operational blind spot.

### SRE-2 — Backup workflow: no failure alerting, no retention (MEDIUM) [also found by CTO/Founder]
`.github/workflows/db-backup.yml` has a daily `cron: "0 3 * * *"` + `workflow_dispatch`, and
verifies the S3 object is >1024 bytes. But:
- **No `if: failure()` notification** — a failed nightly backup is silent. ADR-042's verified
  "RPO ≤ 24h" holds only while the schedule keeps succeeding, unobserved.
- **No S3 lifecycle/retention** on `_system/db-backups/` — dumps accumulate forever (also a
  right-to-erasure gap: erased tenants persist in old dumps — see CTO-7).
- **GitHub disables `schedule` triggers after 60 days of repo inactivity** — for a capstone
  that goes dormant after December, RPO silently returns to infinite with no signal.

### SRE-3 — Circuit breaker (ADR-041): PASS
`core/llm.py::invoke_llm()` is a real per-provider circuit breaker (`_circuit_state`,
`_circuit_lock`, `LLMCircuitOpenError(RuntimeError)`, configurable threshold). Wired as the
invoke path. Dependency-down behavior for the LLM provider is handled.

### SRE-4 — Root container, single image, no restart policy note (LOW/MEDIUM) [see Red Team, CTO]
`Dockerfile` has no `USER` → API, Celery worker, and celery-beat all run as root from one
image. Amplifies the sandbox file-read finding (RT-1). `railway.json` minimal.

## Verdict
The hard resilience piece (LLM circuit breaker) is real and the deploy is live and healthy.
The gaps are **observability of failure**: a health check that can't detect a dead
dependency, and a backup mechanism that can fail, expire, or accumulate PII silently.
