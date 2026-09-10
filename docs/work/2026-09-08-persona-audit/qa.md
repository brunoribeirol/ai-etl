# QA — Test Suite Honesty

**Date:** 2026-09-09. Ground truth: `make check` EXIT=0 — unit+integration 1199 passed /
14 skipped / 95.11% cov; e2e 5 passed in 15.86s; bandit clean; pip-audit clean.

## Findings

### QA-1 — e2e mocks the LLM; "real stack" claim is partial (MEDIUM)
`tests/e2e/conftest.py:12`: *"**Only the LLM calls are mocked**"*. The 5 scenarios run real
Postgres/Redis/sandbox/auth but a `MagicMock` LLM — hence 15.86s for 5 full pipelines. This
is a reasonable CI tradeoff, but README's "runs all 4 scenarios against the real stack" omits
that the LLM — the core of a "multi-agent LLM framework" — is faked. **Consequence:** green
e2e proves plumbing, not agent-output correctness. Combined with ADR-029 (manual-only prompt
regression), **there is no automated test anywhere of real LLM output quality.** Agent
correctness is only ever verified by hand (as this audit's live run did — see
llm-prompt-engineer.md, which found the Advisor's secondary figures wrong).

### QA-2 — The two headline security controls' tests don't run in required CI (HIGH) [also CTO-5]
The only required checks on `main` are the two `Python 3.11/3.12` `check` jobs. That job runs
`pytest tests/integration/` but declares only `mysql-test`/`mongodb-test` services — no
Postgres:5435, no Redis, no sandbox image. So `test_tenant_isolation_rls.py` (RLS/ADR-040),
`test_sandbox_docker.py` (ADR-038 containment), `test_audit_persistence.py`,
`test_alembic_migration.py`, `test_sandbox_vercel.py` all **self-skip green** (~27 tests).
The `e2e` job has Postgres/Redis but runs only `tests/e2e/` and is **not a required check**.
Net: the RLS and sandbox-containment controls pass only on the maintainer's laptop; a
regression in `tenant_scope()` or migration `0021` policies merges green.

### QA-3 — Skips are legitimate; the "1 warning" is third-party (LOW)
The 14 skips are environment-gated (app-postgres ×3, mysql ×1, mongo ×1, seccomp-Linux-only
×1, Vercel Sandbox creds ×7). Not hiding broken tests. The 1 unit/integration warning is
Starlette deprecating `httpx`→`httpx2` (third-party); the 85 e2e warnings are Celery's
`setex` deprecation (third-party). No project-code warnings. (The odd `reason="support
ticket"/"looks wrong"/...` strings in the grep are admin-log test *fixtures*, not skips.)

### QA-4 — Coverage is real but tilted to happy paths (LOW/MEDIUM)
95.11% line coverage is genuine, but line coverage ≠ scenario coverage: the highest-risk
branches (sandbox timeout/kill error paths, auth expired/invalid-JWT, LLM auto-repair
exhaustion) are the ones the deliberately-untested `sandbox_vercel.py`/`sandbox_docker.py`
error paths and the LLM-mocked e2e leave thin. `__main__.py` (CLI) is excluded from coverage
entirely (`pyproject.toml:171`) and has no tests — the headline % is "95.11% of everything
except the CLI."

## Verdict
The suite is honest about *what it is* in the conftest comments, but the *public* claims
(README "real stack", SECURITY "verified see test_tenant_isolation_rls.py") overstate it. The
real gaps: no automated LLM-quality test, and the two security controls' tests don't gate CI.
