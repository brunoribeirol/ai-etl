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

`src/middleware.ts` gates every route behind Clerk via `clerkMiddleware()` —
no local login form; Clerk's own hosted sign-in flow handles it.
`src/components/auth-header.tsx` uses `useUser()` for the signed-in/signed-out
header state (`<SignedIn>`/`<SignedOut>` were removed in `@clerk/nextjs` v7
"Core 3", independent of the Next.js version below).

## Next.js 16, and `proxy.ts` (history: the 15-vs-16 back-and-forth)

`create-next-app@latest` originally scaffolded this on Next.js 16.3.1, which
renamed `middleware.ts` to `proxy.ts` — that broke routing on Vercel at the
time (the deployed app 404'd on every request with zero runtime logs, the
platform's edge routing never finding a function to invoke for the new
"Proxy" convention at all, confirmed live on `ai-etl.vercel.app`, not a
local-only issue). Downgraded to `next@15.5.23` (`middleware.ts`) on
2026-08-17 rather than chase a bleeding-edge framework release.

**Re-upgraded to Next.js 16 on 2026-08-21 (PR #76, a routine Dependabot bump,
auto-merged without anyone re-reading this note against it)** — currently on
16.3.4, using `src/proxy.ts` (renamed from `middleware.ts` on 2026-09-03,
same file, same `clerkMiddleware()` contents). Re-verified live multiple
times since (most recently 2026-09-04): every route loads correctly on
`ai-etl.vercel.app`, no 404s, `proxy.ts` shows up correctly in `next build`'s
own output as `ƒ Proxy (Middleware)`. Whatever caused the original 404 was
specific to that first 16.3.1 release or an earlier Vercel platform gap —
both have since moved on. Left this section's history intact (rather than
deleting it) so a future upgrade doesn't rediscover the same dead end from
scratch, but the header no longer reflects current reality — don't downgrade
based on it.

## Checks

```bash
npm run lint
npm run build   # runs its own TypeScript check as part of the build
```

(`npx tsc --noEmit` also works, but only *after* a build has run at least
once — it needs Next.js's generated global types, e.g. `LayoutProps`, from
`.next/types/`.)
