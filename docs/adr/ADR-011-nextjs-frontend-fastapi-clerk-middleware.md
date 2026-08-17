# ADR-011 — Next.js frontend with Clerk middleware, backed by a new FastAPI layer

**Status:** Accepted
**Date:** 2026-08-17
**Deciders:** Bruno Ribeiro

---

## Context

Sprint 1 (ADR-006) shipped Clerk authentication with an interim UI: paste a Clerk session token into a Streamlit text field. Streamlit has no native OAuth/redirect support and no hosted Clerk sign-in component, so this was always a stopgap. It failed live in production on 2026-08-16 (a pasted token expired mid-session, requiring a manual re-paste), and — more importantly — the Sprint 8 roadmap item (Validação humana: real, non-technical participants using the tool to measure time-saved/trust/usefulness for OE4) cannot produce valid results if participants have to copy a JWT to log in. This turns the login flow from UX debt into a blocker for a specific, planned deliverable.

The Vault decision note (`decisions/nextjs-frontend-over-streamlit-js-bridge.md`) already resolved *whether* to build a real frontend, weighing it against patching Streamlit with a JS bridge to Clerk's hosted `<SignIn/>` widget. This ADR records the *how*.

## Decision

Build a real frontend: **Next.js** (App Router) with **`@clerk/nextjs`**'s `clerkMiddleware()` handling login/session refresh natively — Clerk's first-class, best-supported integration, no custom bridge code. Deployed to **Vercel**.

The Next.js app cannot call `pipeline_service`/`execution_queue`/`audit.db` directly (those are Python, in-process today only from `app.py`/Streamlit) — a new **FastAPI** layer, `src/ai_etl/api/`, exposes them as HTTP endpoints:

```
GET  /health                    — no auth, liveness check
GET  /runs                      — wraps audit.db.load_history(tenant_id=...)
GET  /runs/{run_id}             — wraps audit.db.load_full_result(run_id, tenant_id=...)
POST /runs                      — wraps services.execution_queue.enqueue_analysis(...)
GET  /runs/{task_id}/status     — wraps services.execution_queue.get_task_status(task_id)
```

Auth: a FastAPI dependency (`api/deps.py::get_current_tenant_id`) reads the `Authorization: Bearer <token>` header and calls the **existing, unchanged** `services/auth_service.verify_session_token()` — the same JWKS/`iss` verification `_render_sign_in_gate()` already performs, just invoked from an HTTP dependency instead of a Streamlit widget. `ensure_user()` (ADR-006's FK-safety call) runs in the same dependency. No new auth mechanism, no trusting anything Clerk's frontend SDK claims client-side without server-side re-verification — this ADR reuses ADR-006's security property exactly, it doesn't relax it.

Deploy: Railway's existing `ai-etl` web service switches its `ENTRYPOINT` from `streamlit run app.py` to `uvicorn ai_etl.api.main:app`, once the Next.js frontend is verified live against it (not before — see the implementation plan's sequencing). The Celery worker service is unaffected. Streamlit (`app.py`) is deleted only at that point, not run in parallel indefinitely.

## Alternatives considered

See `decisions/nextjs-frontend-over-streamlit-js-bridge.md` (Vault) for the frontend-framework-level alternative (Streamlit + JS bridge) and its rejection rationale. Within "build a real frontend," no other backend-API framework was seriously considered — FastAPI is already this project's de facto Python web-framework choice by association (async-first, matches the project's existing async/Celery direction, first-class Pydantic validation for the request/response shapes this API needs) and introduces no new language/runtime beyond what's already in the stack.

## Consequences

- **Positive**: closes the Sprint 8 blocker with the same integration Clerk itself recommends, not a maintained-forever workaround.
- **Positive**: `pipeline_service`/`execution_queue`/`audit.db`/`services/auth_service` are untouched — every existing unit/integration/e2e test covering them stays valid; the API is a thin, separately-tested wrapper.
- **Positive**: `_fig.json` artifacts already persisted by `storage.py` (ADR-009) are directly consumable by Plotly.js client-side — no new serialization format needed for chart rendering.
- **Negative**: a genuinely new HTTP contract (endpoints above) is now a compatibility surface that didn't exist before — changes to it need the same care as any public API, unlike `app.py`'s previous direct Python calls.
- **Negative**: CORS must be configured explicitly (Vercel domain calling a Railway domain is cross-origin) — a new class of misconfiguration risk that didn't exist when everything ran in one Streamlit process.
- **Neutral**: no database schema change — the API reads/writes the same `runs`/`analysis_runs` tables via the same `audit.db` functions.

## Related

- Vault: `decisions/nextjs-frontend-over-streamlit-js-bridge.md` — the strategic decision this ADR implements.
- `docs/work/2026-08-17-sprint6-frontend-nextjs-clerk-fastapi.md` — implementation plan, PR sequencing.
- `docs/adr/ADR-006-clerk-auth-supabase-postgres-tenancy.md` — the auth verification this ADR reuses unchanged.
- `docs/adr/ADR-009-tenant-scoped-storage-and-config.md` — the `_fig.json` artifact shape the frontend's result pages read.
