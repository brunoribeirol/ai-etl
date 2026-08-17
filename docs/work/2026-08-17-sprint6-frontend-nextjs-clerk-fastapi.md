# Sprint 6 — Real frontend (Next.js + Clerk + FastAPI)

## Objective

Replace the pasted-Clerk-token Streamlit login with a real login flow (Next.js + `@clerk/nextjs` middleware), backed by a new FastAPI layer that exposes the pipeline services `app.py` currently calls in-process. Streamlit is retired once the Next.js app covers upload/execute, history, and run-detail rendering.

Decision already made (Vault: `decisions/nextjs-frontend-over-streamlit-js-bridge.md`); this plan covers **implementation**, not the strategic choice.

## Non-goals

- No change to `agents/`, `core/`, `sources/`, `destinations/`, `audit/`, `services/pipeline_service.py`, `services/execution_queue.py` — the API is a thin HTTP layer over what already exists and is already tested.
- No redesign of the pipeline/agent architecture.
- No new business features (this sprint is a presentation-layer swap, not new pipeline capability).
- Not fixing `tests/integration/`'s pre-existing bugs (tracked separately, `docs/CURRENT_STATE.md`).
- Not building a design system / polished UI — functional parity with the current Streamlit tabs is the bar, not a redesign.

## Assumptions

- Vercel account available (user's stated primary stack) for the Next.js deploy; Railway continues hosting the API + Celery worker + Postgres.
- Clerk's Next.js SDK (`@clerk/nextjs`) can be added to the *same* Clerk application/instance already used by the backend (`CLERK_PUBLISHABLE_KEY`/`CLERK_JWKS_URL`/`CLERK_ISSUER` stay valid) — no new Clerk project needed, just a frontend SDK key pair.
- CORS: Next.js (Vercel domain) calling FastAPI (Railway domain) is a cross-origin request — the API needs an explicit CORS allowlist for the Vercel domain(s).
- `run_dir` (local artifact staging before Sprint 4's S3 backend takes over, and the upload-staging path `_save_upload_to_temp` writes to) still needs a real filesystem path reachable by both the API process and the Celery worker process — same base assumption `execution_queue.py` already makes today, unchanged by this sprint.

## Affected contracts

- **New**: HTTP contract between Next.js and FastAPI (endpoints below) — this is the one genuinely new interface surface this sprint introduces, and the one most worth getting reviewed/stable early.
- **Unchanged**: `pipeline_service.run_full_analysis`/`run_silver_pipeline`, `execution_queue.enqueue_analysis`/`get_task_status`, `audit.db.load_history`/`load_full_result`, `services/auth_service.verify_session_token` — the API calls these exactly as `app.py` does today, same signatures.
- **Deploy contract change**: the Railway `ai-etl` web service's `ENTRYPOINT` changes from `streamlit run app.py` to a `uvicorn` command — this is a breaking change to that service's purpose, sequenced last (see PR 6) so Streamlit keeps serving traffic until the Next.js replacement is proven.

## Risks

- **CORS/auth misconfiguration silently breaking the frontend in production but not in local dev** (`localhost` origins tend to be permissive by default) — mitigate by testing against the real Vercel preview domain before considering any PR in this sprint done, not just `localhost`.
- **Token handling**: Clerk's Next.js SDK manages the session client-side; the API must verify the same JWT server-side via the *existing* `verify_session_token()` (JWKS, `iss` check) — the risk is accidentally trusting a Next.js-side claim without re-verifying server-side. Mitigation: the FastAPI auth dependency must call `verify_session_token()` on every request, no shortcuts, mirroring `_render_sign_in_gate()`'s behavior exactly.
- **Scope creep in `_render_results`' React reimplementation** (343 lines of Streamlit rendering logic today, includes Plotly figures, multiple sub-tabs) — mitigate by phasing it into its own PR (5) with a narrow "read `_fig.json`/CSV artifacts back, render with Plotly.js" scope, not a rewrite of the visual design.
- **Full-stack e2e coverage doesn't exist yet for the API layer** — `tests/e2e/`'s 4 scenarios call `pipeline_service`/`execution_queue` directly, not through HTTP. New API-level tests are needed (see Testing below), but the existing e2e scenarios stay valid unchanged (they test the layer below the API, which doesn't move).

## Acceptance criteria (Sprint 6 "definição de pronto")

- `make check` green (existing suite unaffected; new API tests added).
- A non-technical person can open the Vercel URL, sign in via Clerk's real hosted UI (Google/email, no token to copy), upload a CSV, ask a business question, see it complete, and see it again in history — with zero mention of "token" or "JWKS" anywhere in the UI.
- Streamlit (`app.py`, the Railway web service's old `ENTRYPOINT`) is removed only after the above is verified live — not before.

## Workstreams and sequencing

Six PRs, mostly sequential (API before frontend, since the frontend needs a real endpoint to call) with two safe parallel points flagged below.

1. **ADR-011 + FastAPI skeleton, read-only endpoints.** `docs/adr/ADR-011-nextjs-frontend-fastapi-clerk-middleware.md` (per the roadmap's "ADR before any SaaS-roadmap item" convention). `src/ai_etl/api/main.py` (FastAPI app, CORS middleware), `src/ai_etl/api/deps.py` (`get_current_tenant_id` dependency: reads `Authorization: Bearer <token>`, calls `verify_session_token`, calls `ensure_user`, raises `HTTPException(401)` on failure — mirrors `_render_sign_in_gate()`). Endpoints: `GET /health` (no auth), `GET /runs` (wraps `load_history`), `GET /runs/{run_id}` (wraps `load_full_result`, 404 on `None`). New dependency: `fastapi`, `uvicorn[standard]` (new `api` extra in `pyproject.toml`, since Streamlit's `app` extra stays until PR 6 retires it).
   - **Smallest initial file set**: `src/ai_etl/api/__init__.py`, `main.py`, `deps.py`, `routers/runs.py`; `pyproject.toml` (`api` extra); `tests/unit/test_api_deps.py`, `tests/unit/test_api_runs.py`.
   - **Validation**: `uv run uvicorn ai_etl.api.main:app --reload` locally, `curl -H "Authorization: Bearer <test-jwt>" localhost:8000/runs`; `uv run pytest tests/unit/test_api_*.py`.

2. **FastAPI write endpoints.** `POST /runs` (wraps `enqueue_analysis`, accepts multipart form for an optional file upload + JSON fields for spec/business_question — mirrors `app.py`'s `_save_upload_to_temp`/base64 handoff, unchanged underlying mechanism), `GET /runs/{task_id}/status` (wraps `get_task_status`). Handle `RateLimitExceededError` → `HTTPException(429)`.
   - **Validation**: same pattern as PR 1, plus a real enqueue against local Celery (eager or a real worker) proving the full round-trip.

3. **Next.js scaffold + Clerk auth, no business pages yet.** `frontend/` (Next.js App Router), `@clerk/nextjs` + `clerkMiddleware()`, `.env.local`/Vercel env vars (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_API_URL`). One page proving the full loop: sign in → call `GET /runs` with the Clerk session token attached → render the raw JSON. This PR's only job is proving auth works end-to-end against a real deployed API (Railway) from a real deployed frontend (Vercel preview) — **not** local-only.
   - **Can start in parallel with PR 2** (Clerk scaffold work doesn't depend on the write endpoints existing yet) — one integration owner should still merge PR 1 before PR 3 needs it, but PR 3's own build can begin once PR 1 is open.
   - **Validation**: manual — real sign-in on a Vercel preview URL, real 200 from the Railway API, screenshot/confirmation in the PR description (same verification discipline this project already uses for infra changes).

4. **"Executar" page.** Upload form (drag-drop or file input), business-question textarea, `POST /runs`, poll `GET /runs/{task_id}/status` client-side (same polling shape `app.py` already does, just client-side `setInterval`/React Query instead of `st.rerun()`).
   - **Validation**: real upload → real completed run, confirmed via the API directly (`GET /runs/{run_id}`) matching what the UI shows.

5. **"Histórico" + run-detail page.** List runs (`GET /runs`), click into a run (`GET /runs/{run_id}`), render Gold/Science/Advisor results with Plotly.js reading the same `_fig.json` shape `storage.py` (ADR-009) already persists — `fig.to_json()`/`pio.from_json()`'s Python-side schema is Plotly's own JSON figure spec, directly loadable by `Plotly.newPlot()` client-side with no transformation needed.
   - **Validation**: a run with a real Gold/Science figure renders identically (data-wise) to what Streamlit's `_render_results` showed for the same `run_id`.

6. **Cut over + retire Streamlit.** Railway `ai-etl` web service `ENTRYPOINT`/`Dockerfile` swapped to `uvicorn ai_etl.api.main:app --host 0.0.0.0 --port $PORT`; `app.py` removed; `docs/CURRENT_STATE.md` + `artefact/sprint-roadmap.md` (Vault) updated. **Gated on PRs 1–5 all verified live**, not just CI-green — this is the "no more Streamlit" point of no return, matching the acceptance criteria's ordering requirement.
   - **Correction (2026-08-17, prep work done ahead of PR 6 itself)**: the plan originally said "the `app` extra removed" — **wrong**, caught before it caused damage. `plotly`/`scikit-learn`/`statsmodels` were misclassified under the `app` (Streamlit-only) extra, but `agents/analyst.py`/`agents/science.py` actually import them *inside the sandbox* (`extra_modules`) to generate charts and fit models — a real pipeline runtime dependency the API exercises identically to Streamlit, not a UI-only concern. Dropping the whole `app` extra during cutover would have silently broken every Analyst/Science sandboxed execution in production (chart/model generation), not just the UI. **Fixed ahead of time**: `pyproject.toml` now has `plotly`/`scikit-learn`/`statsmodels` in base `dependencies`; `app` extra is `streamlit` only. This part is done, `uv lock` re-resolved, `ruff` clean — safe to build on tomorrow.
   - **Not executed yet, deliberately** (2026-08-17 session ended here): this step needs (a) the API deployed somewhere publicly reachable — today `NEXT_PUBLIC_API_URL` is still `http://localhost:8000`, no third Railway service or repurposed web service exists for the API yet — and (b) PRs 1-5 actually verified live (real upload → real completed run → visible in Histórico with a real chart), which hasn't happened. Flipping the Railway web service's `ENTRYPOINT` is an immediate, live production change (same auto-deploy-on-push behavior that made the Vercel troubleshooting this session so consequential) — not something to do unattended without the owner able to fix any Railway-dashboard-side fallout, the same category of manual step Vercel needed repeatedly this session. **Next session should start here**: deploy the API publicly first (new Railway service, mirroring how the Celery worker was added in Sprint 3 — a Custom Start Command service is the lowest-risk option, since it doesn't touch the working Streamlit service at all), verify PRs 1-5 live end-to-end against it, *then* do the Dockerfile/`ENTRYPOINT` swap as the final, deliberate step.

## Testing

- **API layer (new)**: `tests/unit/test_api_deps.py` (auth dependency — reuses the fake-JWKS pattern from `tests/unit/test_auth_service.py`), `tests/unit/test_api_runs.py` (endpoint behavior, mocking `pipeline_service`/`execution_queue`/`audit.db` at the same import-site-mocking convention `test_extractor.py` etc. already use). FastAPI's `TestClient` (`httpx`-based, already a transitive dependency via `fastapi`) covers request/response shape without a running server.
- **Frontend**: no unit-test framework decision made yet in this plan — deferred to PR 3's implementation (Next.js's own testing conventions, e.g. Playwright for the auth-flow smoke test, are a reasonable default but not mandated here).
- **Existing `tests/e2e/`**: unchanged, still valid (tests the pipeline/sandbox/async/auth layer below the new API, which this sprint doesn't touch).
- **Manual verification is the real acceptance gate** for PRs 3–6 (see Acceptance criteria) — this sprint is fundamentally a UX change that automated tests can't fully substitute for, same reasoning the project already applied to Sprint 1/3's live-deploy verification.

## Open questions for the next session (not blocking PR 1)

- Exact Next.js data-fetching approach (Server Components hitting the API directly vs. client-side fetch + React Query) — decide when PR 3 starts, doesn't affect the API contract.
- Whether `frontend/` lives in this same repo (monorepo) or a separate one — recommend same repo for now (simpler CI/PR flow, matches this project's existing single-repo discipline), revisit only if Vercel's build step becomes awkward sharing the repo with the Python backend.

## Deferred: physical `backend/` split

Owner's call (2026-08-17, after PR 3/PR 39 landed): move `src/ai_etl/`, `tests/`, `docs/`, `app.py`, `Dockerfile`, `alembic/`, `case_study/` etc. into a `backend/` directory, mirroring `frontend/`, for visual clarity. **Deferred until the frontend cutover (PR 6) is fully live** — doing it now would mean also reconfiguring Railway's deploy Root Directory mid-sprint, the same class of dashboard-config fragility PR 3's Vercel deploy just went through (see below). Root's `README.md` documents the current (unmoved) structure clearly in the meantime as the agreed-upon interim fix.

## PR 3 postscript — Vercel deployment troubleshooting (real, not build-related)

`ai-etl.vercel.app` 404'd for over an hour after PR #38/#39 merged, despite every build (local, CI, and Vercel's own) succeeding cleanly. Two distinct, unrelated problems stacked on top of each other:

1. **Next.js 16's `proxy.ts` rename broke Vercel's edge routing** (PR #39 fixes this — downgraded to `next@15.5.23`, `middleware.ts`). Confirmed via zero Runtime Logs for any request — the platform never resolved a function to invoke, not a code-level error.
2. **After the Next 15 fix, `ai-etl.vercel.app` specifically stayed 404 while the same deployment's raw hash URL (`ai-hp5z3drkx-...vercel.app`) worked correctly** — domain showed "Valid Configuration", removing/re-adding the domain didn't help, cache-busting didn't help, and even the project owner's own authenticated browser session got 404 where an anonymous session correctly hit Vercel's login prompt (backwards from expected — the owner should bypass Deployment Protection automatically). Root cause never conclusively identified from the dashboard/API alone (no Vercel support ticket was needed in the end) — **fixed by deleting the Vercel project entirely and recreating it from the same GitHub import**, this time setting Root Directory to `frontend` *before* anything else and adding all three Clerk-related env vars (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_API_URL`) up front. Whatever internal state was broken didn't survive the recreate.

**Reusable lesson**: if a Vercel project ever gets its very first deployment configured through the "New Project" screen with the wrong settings (framework/root directory misdetected, e.g. from importing a monorepo before pointing Root Directory at the right subfolder), don't assume fixing Project Settings afterward is equivalent to a clean project — some deployment/domain-level state can apparently persist in a broken form that dashboard-level fixes (removing/re-adding the domain, changing Framework Preset, redeploying) don't reach. Deleting and recreating the project, configuring Root Directory correctly from the very first screen, is a cheap, effective reset.

## Related

- Plan (strategic): `~/.claude/plans/adicionar-o-frontend-ir-silly-rabbit.md`
- Vault decision: `decisions/nextjs-frontend-over-streamlit-js-bridge.md`
- `docs/adr/ADR-006-clerk-auth-supabase-postgres-tenancy.md` — the auth verification this sprint reuses unchanged.
- `docs/adr/ADR-009-tenant-scoped-storage-and-config.md` — the `_fig.json` artifact shape PR 5 reads.
