# AI-ETL — Frontend

Next.js (App Router) + Clerk, backed by the FastAPI API in `../src/ai_etl/api/`
(Sprint 6, ADR-011 — `../docs/adr/ADR-011-nextjs-frontend-fastapi-clerk-middleware.md`).

Replaces the Streamlit UI (`../app.py`), retired once this app covers the same
surface (see the Sprint 6 plan: `../docs/work/2026-08-17-sprint6-frontend-nextjs-clerk-fastapi.md`).

## Local dev

```bash
cp .env.example .env.local   # fill in NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
npm install
npm run dev
```

Run the API alongside it (from the repo root): `make api`.

## Auth

`src/proxy.ts` (Next.js 16's renamed `middleware.ts` convention) gates every
route behind Clerk via `clerkMiddleware()` — no local login form; Clerk's own
hosted sign-in flow handles it. `src/components/auth-header.tsx` uses
`useUser()` for the signed-in/signed-out header state (`<SignedIn>`/`<SignedOut>`
were removed in `@clerk/nextjs` v7 "Core 3").

## Checks

```bash
npm run lint
npx tsc --noEmit
npm run build
```
