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

## Why Next.js 15, not 16

`create-next-app@latest` scaffolded this on Next.js 16.3.1, which renamed
`middleware.ts` to `proxy.ts`. That broke routing on Vercel — the deployed
app 404'd on every request with zero runtime logs, meaning the platform's
edge routing never found a function to invoke for the new "Proxy" convention
at all (confirmed live on `ai-etl.vercel.app`, not a local-only issue).
Downgraded to `next@15.5.23` (`middleware.ts`, Vercel's well-established
convention) rather than chase bleeding-edge framework support. `@clerk/nextjs`
v7's peer range covers both, so no Clerk downgrade was needed —
`eslint-config-next` was pinned to match (15.5.23), which required switching
`eslint.config.mjs` back to the `FlatCompat` bridge (that version still
exports the legacy eslintrc shape, not a flat-config-native array). `postcss`/
`sharp` (transitive, via `next`) had known-CVE versions pinned to 15.5.23's
lockfile — pinned to patched versions via `package.json`'s `overrides` instead
of following `npm audit fix --force`'s suggestion to jump back to `next@16`.

## Checks

```bash
npm run lint
npm run build   # runs its own TypeScript check as part of the build
```

(`npx tsc --noEmit` also works, but only *after* a build has run at least
once — it needs Next.js's generated global types, e.g. `LayoutProps`, from
`.next/types/`.)
