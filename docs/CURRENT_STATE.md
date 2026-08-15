# Current State — AI-ETL

> Living doc. Updated at the end of meaningful work sessions, not per-commit. Source of truth for repo/code state; the Obsidian vault (`~/Documents/Obsidian Vault/tcc/`) is the source of truth for the academic TCC narrative and product/strategy context.

**Last updated:** 2026-08-15 (migration 0004 applied to production)

## Confirmed state (branch `main` @ `c989729`, PR #23 merged 2026-08-15)

- 5-agent LangGraph "Silver" pipeline (Orchestrator → Extractor → Transformer → Quality → Loader) + 4-agent "Agentic BI" layer (Planner → Analyst/Gold, Science → Advisor) — both fully implemented and exercised by the case study (15 runs, 100% success) and by the Streamlit app (`app.py`).
- **Real authentication (Clerk) and account-based tenancy (Supabase Postgres) are live**, both in code and on a real Railway deployment (Sprint 1, PR #18, merged 2026-08-13; deploy debugged and confirmed working 2026-08-14). `runs`/`analysis_runs.tenant_id` are `NOT NULL` foreign keys to a new `users` table, keyed by Clerk `user_id` — the PR #16 session-UUID stopgap is fully retired.
- **The `exec()` sandbox is now unified** (Sprint 2, PR #23, ADR-007) — `core/sandbox.py` is the single call site for Transformer/Analyst/Science, running in a `multiprocessing.Process` (spawn context) with a real enforced timeout (30s/15s/20s respectively) and `os.environ.clear()` in the child before user code runs. `SECURITY.md`/ADR-003 are now stale on this point (still describe 3 separate sites) — worth a follow-up doc pass. The introspection-escape limitation (`().__class__.__mro__[1].__subclasses__()`) remains open, unchanged, still accepted for TCC scope.
- Per-stage latency instrumentation live: `stage_durations` on `PipelineState`, persisted to a new `stage_latencies` table (migration `0004`) via `save_stage_latencies()` — feeds the evaluation-metrics framework (`artefact/evaluation-metrics.md` in the Vault).
- Dependencies: `gitpython` bumped to 3.1.59 (cleared 15 CVEs), `pandas`/`pandas-stubs` bumped to `<4.0.0` (Dependabot #13/#14), `pyjwt[crypto]>=2.13.0` added (Sprint 1) — all merged.

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

- **`alembic upgrade head`'s exact root-cause hang is still not fully diagnosed, and recurred a second time (2026-08-15) applying migration `0004`.** `uv run --env-file .env alembic upgrade head` hung indefinitely (near-zero CPU) with the same signature as migration `0003`'s hang — worked around the same way: applied the equivalent schema via direct SQL and manually synced `alembic_version` (`0003` → `0004`), confirmed via `information_schema.columns`/`pg_indexes` against the live database. Root cause is still unconfirmed (suspected heavier schema-introspection query than a trivial `SELECT`); this workaround will likely be needed again for migration `0005`+ unless diagnosed. See Vault: `bugs-solved/mypy-pytest-hang-agent-sandbox.md` for the broader sandbox-hang pattern this may share (HTTP/2-related hangs in the same environment were fixed by forcing HTTP/1.1 — untested for Alembic/psycopg2, worth trying next time before falling back to direct SQL).
- **`SECURITY.md`/`ADR-003` are now stale** — still describe 3 separate `exec()` sites; ADR-007 supersedes this but the older docs weren't rewritten (only cross-referenced). Low priority, but a reader landing on `SECURITY.md` first would get a wrong picture.
- **Two unreconciled ICP framings** across the project's own docs (`artefact/saas-potential.md`: data engineers; `writing/drafts/draft-visao-produto.md` + owner's stated framing: SMB entrepreneurs) — not yet resolved, flagged for the owner to decide, not a code task.
- **`.claude/specs/sr-standard.md` §8 SaaS Roadmap table** — the project's own pre-existing plan for exactly this transition; the current multi-sprint plan follows its sequencing logic but reorders items where the SaaS-readiness audit found reason to.

## Next steps

8-sprint plan (Vault: `artefact/sprint-roadmap.md`): A [done] → 1 [done — auth/tenancy/deploy] → 2 [done — sandbox unification + latency instrumentation] → 3 [in progress — async execution + metering + cost] → 4 [storage/config] → 5 [PDF/DOCX + e2e] → 6 [model comparison + stability] → 7 [human validation study] → 8 [multi-cloud]. Migration `0004` applied to the live Supabase database (2026-08-15) — `stage_latencies` is live in production.

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
