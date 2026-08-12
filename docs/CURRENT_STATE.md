# Current State — AI-ETL

> Living doc. Updated at the end of meaningful work sessions, not per-commit. Source of truth for repo/code state; the Obsidian vault (`~/Documents/Obsidian Vault/tcc/`) is the source of truth for the academic TCC narrative and product/strategy context.

**Last updated:** 2026-08-11

## Confirmed state (branch `main` @ `60469a8`, PR #16 merged 2026-08-11)

- 5-agent LangGraph "Silver" pipeline (Orchestrator → Extractor → Transformer → Quality → Loader) + 4-agent "Agentic BI" layer (Planner → Analyst/Gold, Science → Advisor) — both fully implemented and exercised by the case study (15 runs, 100% success) and by the Streamlit app (`app.py`).
- Persistence: Postgres (SQLAlchemy Core + Alembic), `runs`/`analysis_runs` tables. As of PR #16: both tables carry a nullable `tenant_id` column, session-scoped (not account-scoped) via `app.py::_get_session_id()`.
- Security docs (`SECURITY.md`, `docs/adr/ADR-003-exec-sandbox.md`) accurately describe **three separate, unmerged `exec()` sandboxes** (`core/sandbox.py`, `agents/analyst.py`, `agents/science.py`) — corrected 2026-08-11 (PR #15), still unmerged/unenforced-timeout as of this writing (tracked for Sprint 2 of the SaaS plan, see below).
- No authentication anywhere. No real multi-tenancy (only the PR #16 session-scoping stopgap). Dockerfile does not run the Streamlit app (CLI only).
- Dependencies: `gitpython` bumped to 3.1.59 (cleared 15 CVEs), `pandas`/`pandas-stubs` bumped to `<4.0.0` (Dependabot #13/#14) — all merged.

## Changed files (this session, 2026-08-11)

- `docs/adr/ADR-003-exec-sandbox.md`, `SECURITY.md`, `README.md`, `src/ai_etl/core/sandbox.py` — corrected to document all 3 `exec()` sites (PR #15, merged).
- `uv.lock` — gitpython bump (part of PR #15).
- `alembic/versions/0002_add_session_scoping.py` (new), `src/ai_etl/audit/models.py`, `src/ai_etl/audit/db.py`, `src/ai_etl/services/pipeline_service.py`, `app.py`, `docs/adr/ADR-005-session-scoped-run-isolation.md` (new), `tests/unit/test_audit_db.py`, `tests/unit/test_pipeline_service.py` — session-scoped isolation stopgap for the Histórico-tab data leak (PR #16, merged — CI green, 186/186 unit tests, 93.72% coverage).

## Validation

- PR #15: CI green (Python 3.11/3.12 matrix), merged.
- PR #16: `ruff check` / `ruff format --check` passed locally (scoped to touched files). **`mypy`/`pytest` could not be run locally this session** — every invocation hung indefinitely regardless of scope/cache/plugin-autoload settings; root cause and workaround captured in the vault bug note (see Related below). Manual review substituted for automated checks and found/fixed one real regression (5 stale test monkeypatches) before pushing. CI (clean GitHub Actions runner) then caught one more thing manual review missed — a stale dict-equality assertion — fixed in a follow-up commit; final CI run green on both Python 3.11/3.12, 186/186 unit tests, 93.72% coverage. Merged.

## Known risks / open items

- **`exec()` sandbox not yet unified** — 3 independent whitelists, no enforced timeout. Deployment-blocking once real multi-tenant traffic exists (tracked as Sprint 2 of the SaaS plan).
- **No real authentication/accounts** — session-scoping (PR #16) is an explicit stopgap, not a fix. Tracked as Sprint 1 of the SaaS plan (Clerk + Supabase Postgres, decided 2026-08-11).
- **Dockerfile doesn't run the app** — CLI-only image. Tracked as part of Sprint 1.
- **Two unreconciled ICP framings** across the project's own docs (`artefact/saas-potential.md`: data engineers; `writing/drafts/draft-visao-produto.md` + owner's stated framing: SMB entrepreneurs) — not yet resolved, flagged for the owner to decide, not a code task.
- **`.claude/specs/sr-standard.md` §8 SaaS Roadmap table** — the project's own pre-existing plan for exactly this transition; the current multi-sprint plan (see below) follows its sequencing logic but reorders items where the SaaS-readiness audit found reason to (see plan file).

## Next steps

Multi-sprint plan approved 2026-08-11, plan file: `~/.claude/plans/magical-floating-teapot.md` (local to the session that created it — if unavailable, the sprint structure is: A [done, PR #16] → 1 [auth/tenancy/deploy] → 2 [sandbox unification] → 3 [async + metering] → 4 [storage/config] → 5 [e2e + thesis polish]). ADR-006 (Clerk + Supabase Postgres tenancy decision) is the next artifact to write, at the start of Sprint 1.

## Related

- Vault: `~/Documents/Obsidian Vault/tcc/sessions/2026-08-11-tech-lead-saas-review-e-sprint-a.md` — full session narrative.
- Vault: `~/Documents/Obsidian Vault/tcc/artefact/saas-potential.md` — product/business framing (explicitly out of TCC scope).
- `docs/adr/` — ADR-001 through ADR-005; ADR-006+ to be written per sprint.
