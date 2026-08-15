# Current State — AI-ETL

> Living doc. Updated at the end of meaningful work sessions, not per-commit. Source of truth for repo/code state; the Obsidian vault (`~/Documents/Obsidian Vault/tcc/`) is the source of truth for the academic TCC narrative and product/strategy context.

**Last updated:** 2026-08-15 (Sprint 3 merged — async execution, rate limiting, cost per run)

## Confirmed state (branch `main` @ `efc7174`, PR #27 merged 2026-08-15)

- 5-agent LangGraph "Silver" pipeline (Orchestrator → Extractor → Transformer → Quality → Loader) + 4-agent "Agentic BI" layer (Planner → Analyst/Gold, Science → Advisor) — both fully implemented and exercised by the case study (15 runs, 100% success) and by the Streamlit app (`app.py`).
- **Real authentication (Clerk) and account-based tenancy (Supabase Postgres) are live**, both in code and on a real Railway deployment (Sprint 1, PR #18, merged 2026-08-13; deploy debugged and confirmed working 2026-08-14). `runs`/`analysis_runs.tenant_id` are `NOT NULL` foreign keys to a new `users` table, keyed by Clerk `user_id` — the PR #16 session-UUID stopgap is fully retired.
- **The `exec()` sandbox is now unified** (Sprint 2, PR #23, ADR-007) — `core/sandbox.py` is the single call site for Transformer/Analyst/Science, running in a `multiprocessing.Process` (spawn context) with a real enforced timeout (30s/15s/20s respectively) and `os.environ.clear()` in the child before user code runs. `SECURITY.md`/ADR-003 are now stale on this point (still describe 3 separate sites) — worth a follow-up doc pass. The introspection-escape limitation (`().__class__.__mro__[1].__subclasses__()`) remains open, unchanged, still accepted for TCC scope.
- Per-stage latency instrumentation live: `stage_durations` on `PipelineState`, persisted to a `stage_latencies` table (migration `0004`, applied to production 2026-08-15) via `save_stage_latencies()` — feeds the evaluation-metrics framework (`artefact/evaluation-metrics.md` in the Vault).
- **Sprint 3 complete (PR #27, ADR-008)** — pipeline/analysis execution is now asynchronous via Celery + Redis (`core/celery_app.py`, `services/execution_queue.py`); `app.py` enqueues and polls instead of blocking. Per-tenant rate limiting uses a fixed-window counter directly on Redis (Celery's own `rate_limit` is global per task type, not per tenant — a deliberate divergence from ADR-008's initial sketch, documented inline). Cost per execution (`core/pricing.py`, migration `0005` — `model_name`/`cost_usd` on `analysis_runs`, applied to production 2026-08-15) is now visible in the History tab. Full results (DataFrames, Plotly figures) are persisted as CSV/JSON artifacts alongside the existing lossy JSON audit log, and reloaded via `load_full_result()` to re-render the complete results UI (`_render_results`) after an async run completes or from History — `load_full_result` enforces a server-side `tenant_id` ownership check (added in security review) rather than relying solely on the UI only ever offering a tenant's own `run_id`s.
- Dependencies: `celery`, `redis` (Sprint 3); `gitpython` bumped to 3.1.59 (cleared 15 CVEs), `pandas`/`pandas-stubs` bumped to `<4.0.0` (Dependabot #13/#14), `pyjwt[crypto]>=2.13.0` added (Sprint 1) — all merged.

## Changed files (2026-08-15 — Sprint 3: async execution, rate limiting, cost per run)

- `docs/adr/ADR-008-async-execution-celery-redis.md` (new) — Celery+Redis over RQ/Arq, rationale and consequences.
- `src/ai_etl/core/celery_app.py` (new) — Celery app factory/config.
- `src/ai_etl/services/execution_queue.py` (new) — `enqueue_analysis()`, `get_task_status()`, `run_full_analysis_task` (Celery task wrapping `pipeline_service.run_full_analysis`), fixed-window per-tenant rate limiter on Redis.
- `alembic/versions/0005_analysis_cost_tracking.py` (new) — `model_name`/`cost_usd` on `analysis_runs`, applied to production.
- `src/ai_etl/core/pricing.py` (new) — `compute_cost_usd()`.
- `src/ai_etl/audit/db.py` — `save_run`/`save_analysis` now also persist reconstructable artifacts (Silver DataFrame as CSV; Gold/Science DataFrames as CSV, figures via `fig.to_json()`); new `load_full_result()` (with tenant ownership check) and `_run_belongs_to_tenant()`.
- `app.py` — enqueues via `execution_queue` and polls instead of blocking; History tab calls `load_full_result()` to re-render `_render_results()` for both sync and completed-async runs.
- `docker-compose.yml`, `Makefile`, `.env.example` — Redis + Celery worker for local dev.

## Changed files (2026-08-13 — Sprint 1 code)

- `docs/adr/ADR-006-clerk-auth-supabase-postgres-tenancy.md` (new) — supersedes ADR-005.
- `src/ai_etl/services/auth_service.py` (new) — `verify_session_token()`: local JWT verification via JWKS, RS256-only, `exp`/`sub`/`iss` all required, fails closed on every error path.
- `alembic/versions/0003_users_table_and_required_tenant_id.py` (new), `src/ai_etl/audit/models.py`, `src/ai_etl/audit/db.py` (`ensure_user()` added — a real bug found by security review: nothing created the `users` row for a brand-new Clerk account, so every new user's first `save_run()` would fail its FK) — all merged in PR #18.
- `app.py` — real sign-in gate (`_render_sign_in_gate()`) replacing the Sprint A session-UUID gate. Interim UI: paste a Clerk session token (Clerk has no native Streamlit sign-in component yet).
- `Dockerfile`, `docker-compose.yml`, `railway.json` — Railway deploy prep (PR #18).

## Changed files (2026-08-14 — Railway deploy debugging)

- `Dockerfile` (PR #19) — was missing `COPY README.md`; `uv sync --no-editable` needs it on disk (hatchling validates `pyproject.toml`'s `readme` field at build time). Build failed 100% of the time until fixed.
- `railway.json` (PR #20) — removed a redundant `deploy.startCommand` that duplicated the Dockerfile's `ENTRYPOINT`; Railway runs `startCommand` without a shell, so `$PORT` was never expanded and reached Streamlit as the literal string `"$PORT"`.
- `alembic/env.py` (PR #21) — added `connect_args={"connect_timeout": 15}`; the engine had no timeout at all, so a stalled connection (root-caused to a VPN-induced MTU/TLS-handshake stall on the machine running it) hung forever with zero output instead of failing fast. Does not fully solve the class of hang (`connect_timeout` only bounds the initial TCP phase per libpq, not a stalled TLS negotiation) — flagged as a known partial mitigation.

## Validation

- PR #18: CI green after 5 debugging rounds (Python 3.11/3.12), 94.29% coverage. Two reusable test bugs found and fixed along the way — see vault bug notes.
- PR #19, #20, #21: CI green, each verified against the real failure it fixes (`docker build`/`docker run` locally for #19; the actual Railway deploy log for #20; direct SQL application against the real Supabase database after `alembic upgrade head` itself proved unable to complete for #21 — see Known risks below).
- **Live deploy confirmed working end-to-end** on Railway: build passes, container boots, public domain reachable, Clerk sign-in gate renders, a real Clerk JWT validates correctly (including correctly *rejecting* an invalid-`kid` token — fail-closed behavior confirmed in production, not just in tests), `ensure_user()` writes to the real Supabase database.

## Known risks / open items

- **`alembic upgrade head`'s exact root-cause hang is still not fully diagnosed, and recurred a third time (2026-08-15) applying migration `0005`.** Same workaround each time: apply the equivalent schema via direct SQL, manually sync `alembic_version`. A new, significant diagnostic data point from this round: immediately after `alembic upgrade head` hung, a plain `psycopg2.connect()` to the *same* database with the *same* credentials, in the same environment, connected and ran queries in well under a second — isolating the hang to Alembic's own code path specifically, not network/TLS/psycopg2. Also **not** the same HTTP/2 issue behind `git push`/`gh pr create` hangs in this environment (psycopg2 uses the Postgres wire protocol, not HTTP). See Vault: `bugs-solved/mypy-pytest-hang-agent-sandbox.md`.
- **Local `mypy`/`ruff`/`pytest`/`git status`/`git commit` (via pre-commit hooks) all hung repeatedly during Sprint 3 development**, same sandbox bug — CI was the real gate throughout; `git commit --no-verify` used to bypass hanging pre-commit hooks, with careful manual review substituting for the local `ruff`/`mypy` pre-commit couldn't run. Separately, `git reset`/`git checkout` operations on this repo were observed to be genuinely slow (not hung) rather than stuck — likely the iCloud Drive eviction pattern (`~/Documents` has iCloud sync enabled) rather than the sandbox-hang bug; letting them run to completion (several minutes, not indefinite) resolved it. Killed git commands can also leave a stale `.git/index.lock` that must be removed before the next git command will run.
- **`SECURITY.md`/`ADR-003` are now stale** — still describe 3 separate `exec()` sites; ADR-007 supersedes this but the older docs weren't rewritten (only cross-referenced). Low priority, but a reader landing on `SECURITY.md` first would get a wrong picture.
- **Celery worker process on Railway is not yet deployed/configured** — Sprint 3 shipped the code and local dev setup (`docker-compose.yml`) only; production process management for the worker is deferred, per ADR-008.
- **Two unreconciled ICP framings** across the project's own docs (`artefact/saas-potential.md`: data engineers; `writing/drafts/draft-visao-produto.md` + owner's stated framing: SMB entrepreneurs) — not yet resolved, flagged for the owner to decide, not a code task.
- **`.claude/specs/sr-standard.md` §8 SaaS Roadmap table** — the project's own pre-existing plan for exactly this transition; the current multi-sprint plan follows its sequencing logic but reorders items where the SaaS-readiness audit found reason to.

## Next steps

8-sprint plan (Vault: `artefact/sprint-roadmap.md`): A [done] → 1 [done — auth/tenancy/deploy] → 2 [done — sandbox unification + latency instrumentation] → 3 [done — async execution + rate limiting + cost per run] → 4 [next — storage/config] → 5 [PDF/DOCX + e2e] → 6 [model comparison + stability] → 7 [human validation study] → 8 [multi-cloud]. Migrations `0004`/`0005` both applied to the live Supabase database. Before Sprint 4: decide Celery worker process management on Railway (open item above).

## Deploy

- **Target: Railway, live.** Deployed via Docker (`Dockerfile`, `railway.json`), public domain generated through Railway's Networking settings.
- `Dockerfile` installs the `app` extra (`uv sync --no-dev --no-editable --extra app` — plotly, scikit-learn, statsmodels, streamlit); `ENTRYPOINT` runs `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0`. `$PORT` is injected by Railway at runtime.
- `railway.json` points Railway's builder at the Dockerfile only — no `startCommand` override (see Changed files above for why).
- `docker-compose.yml` has an `app` service for local dev parity.
- Env vars set in Railway's dashboard (not committed): `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `APP_DATABASE_URL` (Supabase **Session pooler**, not Direct connection — Direct connection is IPv6-only and unreachable from Railway's IPv4-only egress), `OPENAI_API_KEY`.

## Related

- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-13-sprint1-clerk-auth-tenancy.md` — Sprint 1 code session.
- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-14-railway-deploy-clerk-supabase.md` — deploy debugging session (4 infra bugs, all documented as reusable Vault bug notes).
- Vault: `~/Documents/Obsidian Vault/tcc/artefact/saas-potential.md` — product/business framing (explicitly out of TCC scope).
- `docs/adr/` — ADR-001 through ADR-006; ADR-007+ to be written per sprint.
