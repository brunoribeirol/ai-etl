# ADR-030: Public Landing Page — Route Group Split, `/` Becomes Marketing

**Status:** Accepted
**Date:** 2026-08-22
**Sprint:** none — growth/marketing initiative, not part of the numbered 1-29 roadmap (see `docs/CURRENT_STATE.md`)

## Context

Every route in `frontend/` was Clerk-protected (`src/middleware.ts`'s `isPublicRoute` matcher only
excluded `/sign-in`/`/sign-up`) — a visitor with no session hit Clerk's hosted sign-in immediately,
with no public page explaining what the product is or why to sign up. The owner asked for a real,
public marketing home page (visual quality bar: "sensacional", "o melhor e mais moderno de
frontend"), plus a language toggle (Sprint 25, separate PR) that needs to work on both the
marketing page and the product.

## Decision 1 — `/` becomes the public landing page; the product moves to `/app`

**Chosen:** the landing page lives at `/` (root) — the conventional place a marketing page belongs
for SEO and first impression — and everything that used to be at `/` (the "Executar" upload form)
moves to `/app`. `/pipelines`, `/historico`, `/resumo`, `/comecar` keep their existing URLs
unchanged (no reason to move routes that weren't the home page).

**Alternative considered:** put the landing page at a secondary path (`/home`, `/landing`) and leave
`/` as the protected app. Rejected — a marketing page hidden behind a non-root path fights basic SEO
and looks unfinished to anyone who lands on the bare domain, which is exactly the first impression
this ADR exists to fix.

**Mechanism:** Next.js route groups — `(marketing)/page.tsx` for `/`, `(app)/...` for everything
that needs the product's authenticated chrome (header/nav/`AgentsInfo`, all previously in the root
`layout.tsx`, now in `(app)/layout.tsx`). Route groups don't affect the URL, only which `layout.tsx`
wraps a page — no URL changed for any existing authenticated route. The root `layout.tsx` shrinks to
what every route genuinely shares: `ClerkProvider`, fonts, the dark theme class, and `<Toaster>`.

`src/middleware.ts`'s `isPublicRoute` matcher gains a single exact entry, `"/"` — matched exactly
(not `"/(.*)"`), so `/app`, `/pipelines`, etc. stay behind `auth.protect()` unchanged.
`SignInButton`/`SignUpButton` now pass `forceRedirectUrl="/app"` explicitly (both the landing page's
and the marketing layout's), since Clerk's un-configured default would otherwise send a fresh
sign-in back to `/`, which is no longer the app.

## Decision 2 — landing page content: real facts only, no fabricated social proof

The landing page's differentiators section, dual-audience section, and "built for production" facts
strip are all sourced directly from `artefact/saas-potential.md` (ICP, the honest "why not just
ChatGPT" answer already researched there) and from real, shipped architecture (RLS-scoped
multi-tenancy, sandboxed execution, ADR-020's retry/health tracking, ADR-026's output validation,
the SOC2 self-assessment doc from Sprint 24) — no invented usage numbers, no fake customer logos or
testimonials, no "trusted by N companies." The product has no paying customers yet; claiming
otherwise on a public page would be false advertising, not just bad practice.

## Decision 3 — kept `src/middleware.ts`, did **not** rename to `proxy.ts`, despite Next 16's deprecation warning

Next.js 16.3.1 (already the pinned version, `frontend/package.json`) warns that the `middleware.ts`
convention is deprecated in favor of `proxy.ts`. **Investigated and reverted before merging, not
silently left as a warning**: `frontend/README.md`'s own "Why Next.js 15, not 16" section documents
a real, previously-live production incident — this exact rename (`middleware.ts` → `proxy.ts`)
broke routing on Vercel with zero runtime logs (a silent 404 on every request) the last time this
project was on a Next 16 scaffold, which is why the project deliberately downgraded to `next@15.5.23`
at the time.

This session already bumped back to `next@16.3.1` via a Dependabot PR (merged before this ADR, CI
green) while keeping the filename `middleware.ts` — confirmed still healthy in production
(`ai-etl.vercel.app` serves `/` correctly on the current deployment, checked live via browser before
writing this ADR). Renaming to `proxy.ts` now would reintroduce the exact configuration already
proven to break Vercel routing, to silence a cosmetic build warning — not a trade worth making.
**Consequence:** the deprecation warning will keep appearing in every `npm run build` until either
Vercel's platform-side support for the `proxy.ts` convention is confirmed fixed (untested since the
original incident) or Next.js removes the `middleware.ts` fallback entirely (forcing the issue). Not
addressed further in this ADR — flagged for whoever picks this up next to re-test in a low-stakes
way (a throwaway preview deployment, not a `main` merge) before ever renaming this file again.

## Consequences

- Every bookmark/link to the old `/` (the Executar form) now needs `/app` — no redirect was added
  from old `/` to `/app`, since `/` now serves real, different (marketing) content rather than a
  moved page; a visitor with an old bookmark sees the landing page, not a broken link, and can sign
  in from there.
- `frontend/README.md`'s auth section reference to `src/middleware.ts` stays accurate (unchanged).
- Sprint 25 (i18n toggle, separate PR) needs to cover this landing page's copy too, not just the
  authenticated app — flagged for that PR, not solved here.

## Related

- ADR-011 — original Next.js/Clerk/FastAPI middleware decision this ADR builds on, not replaces.
- `frontend/README.md`'s "Why Next.js 15, not 16" — the incident this ADR's Decision 3 avoids
  repeating.
- Vault: `artefact/saas-potential.md` §1-2 — source of the landing page's differentiators/ICP copy.
