# Product / PO — Backend/Frontend Parity

**Date:** 2026-09-09. Mode: cross-referenced every API route against every frontend fetch;
walked the live UI.

## Method
Route inventory from `src/ai_etl/api/routers/*` + `api/main.py` (39 routes). Frontend calls
via `apiFetch`/`authedFetch`/raw `fetch(${apiUrl}...)` across `frontend/src`.

## Findings

### PP-1 — `POST /runs/estimate` is backend-complete with ZERO UI (MEDIUM)
`cost_estimation.py` (Sprint 35 FinOps) implements pre-run cost estimation, and its own module
docstring says *"the frontend consumes this next … render `estimated_cost_usd` … before the
confirm-execution control."* Reality: **no `estimate` reference anywhere in `frontend/src`.**
The Run page's "Analyze data" button has no pre-run cost preview; the model cards show only
static per-run cost ranges from the case study, not a dynamic estimate for the uploaded
dataset. A complete, working, tested endpoint with no consumer — the exact "route without a
frontend caller" pattern the 2026-09-08 walkthrough was hunting.

### PP-2 — `POST /admin/tenants/{id}/pipelines/{id}/retry` has no UI (LOW — known)
The ADR-047 admin write endpoint has zero frontend caller (admin UI is read-only per ADR-032
Decision 2). Already flagged in CURRENT_STATE as "built and unit-tested, never live-verified."
Confirmed still UI-less.

### PP-3 — Everything else maps to a consumer (PASS)
`/config`, `/runs*`, `/pipelines*` (+llm-config/notification-config), `/budget`, `/secrets*`,
`/tenant*` (export/locale/retention/delete), `/onboarding/status`, `/admin/*` reads,
`/llm/allowed-models`, `/llm/test-connectivity` (wired PR #223 — **live-verified working this
session**, "Working (1921ms)"), `/runs/excel-sheets` (multi-sheet picker — live-verified) all
have UI consumers. `drift_threshold_pct` (Wave-2 fix) is in the pipeline form. No other
orphan endpoints.

### PP-4 — Multi-provider model picker is real (PASS)
4 model cards across 2 providers (OpenAI GPT-4o mini/GPT-4o, Anthropic Claude Haiku 4.5/Sonnet
5), each with pricing, quality score, and a working Test-connection button. Backend
`ALLOWED_MODELS_BY_PROVIDER` matches.

## Verdict
Parity is strong — after the recent walkthrough fixes, only the **cost-estimate endpoint** is a
genuinely backend-complete/zero-UI feature. Shipping the estimate UI (the endpoint's own
docstring already specifies the UX) is a small, high-signal win, especially given PP-relevant
CTO finding CTO-4 (no default budget cap for self-serve tenants) — a visible pre-run cost would
partly mitigate uncapped spend anxiety.
