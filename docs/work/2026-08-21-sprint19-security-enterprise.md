# Sprint 19 — Segurança/produção enterprise

## Objective
Deliver the technical prerequisites for the Enterprise tier: org-level SSO
via Clerk, RBAC inside a tenant (editor vs viewer), and real per-tenant
secrets management for external source credentials (replacing shared
process-wide env vars).

## Non-goals
- SOC2/LGPD/GDPR certification work (Sprint 20).
- Wiring tenant secrets into the LangGraph execution path (extractor/REST
  source) — blocked by ADR-002's node-signature contract (`(state) ->
  state`, tenant_id deliberately kept out of `PipelineState`). Storage/API
  only this sprint; execution wiring is a follow-up decision.
- Migrating Postgres/MySQL/MongoDB source connectors off shared env vars —
  documented as known limitation.
- Any DB-level RLS (still out per ADR-006).

## Investigation findings (confirmed, not assumed)
1. `tenant_id` today == individual Clerk user id (ADR-006). One tenant = one
   human. RBAC ("two users, same tenant, different roles") is structurally
   impossible until tenancy can represent more than one user — Clerk
   Organizations is the prerequisite for both SSO *and* RBAC, not just SSO.
2. Source credentials: `postgres_source.py` reads one process-wide
   `POSTGRES_URL` env var (shared by literally every tenant on the
   deployment). `rest_source.py` already avoids embedding literal secrets
   in `pipeline_plan` — `auth` carries an *env var name*, resolved via
   `os.environ` at call time — but that env var is still process-wide, not
   tenant-scoped. Confirms the roadmap's hypothesis: no real multi-tenant
   self-serve secret isolation exists today.
3. Clerk session JWT v2 nests org claims under `o`: `o.id` (org id),
   `o.rol` (role), `o.slg` (slug); legacy v1 flat claims are `org_id`/
   `org_role`. A personal (non-org) session omits `o` entirely. Verified via
   Clerk docs (2026-08-21).

## Decisions (→ ADR-021)
- Tenant resolution: prefer `o.id`/`org_id` when present (org-scoped
  session) else fall back to `sub` (solo tenant, zero behavior change for
  every existing account).
- Role resolution: derived live from the JWT's org role claim each request
  (no new `role` column/table) — `"admin"`/`"org:admin"`-shaped role →
  `editor`, anything else → `viewer`; no active org → `editor` (sole owner,
  matches today's implicit unrestricted behavior).
- RBAC enforced via a new `require_role()` FastAPI dependency; mutating
  endpoints (`POST/PATCH /pipelines`, `POST /runs`, `PATCH /budget`,
  `POST/DELETE /secrets`) require `editor`; all `GET` endpoints stay
  `viewer`-accessible.
- Secrets: new `tenant_secrets` table + `services/secrets_service.py`,
  Fernet-encrypted at rest (`AI_ETL_SECRETS_ENCRYPTION_KEY`), values never
  logged, never returned by the API (names only on list).

## Smallest file set
`src/ai_etl/services/auth_service.py`, `src/ai_etl/api/deps.py`,
`src/ai_etl/audit/models.py`, new `alembic/versions/0011_*.py`, new
`src/ai_etl/services/secrets_service.py`, new
`src/ai_etl/api/routers/secrets.py`, `src/ai_etl/api/routers/{pipelines,runs,budget}.py`
(add `require_role`), `src/ai_etl/api/main.py`, tests under `tests/unit/`.

## Validation
`make check` (ruff, mypy, pytest, bandit/pip-audit). Manual: two fake org
JWTs (admin vs member role) hitting a mutating endpoint.
