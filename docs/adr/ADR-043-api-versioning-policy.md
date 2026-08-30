# ADR-043: API Versioning Policy

**Status:** Accepted
**Date:** 2026-08-27
**Sprint:** Wave 3 (post-audit strategic decisions, `docs/work/2026-08-26-strategic-decisions-execution-plan.md`)

## Context

The X-PRO.ai gap analysis (`docs/CURRENT_STATE.md`, 2026-08-27 entry)
flagged the absence of an API versioning policy — one of 3 small items the
owner approved for immediate execution.

**Current state, confirmed against the actual code, not assumed:**
`src/ai_etl/api/main.py:36` sets `FastAPI(title="AI-ETL API",
version="1.0.0")` — a version string that exists only for OpenAPI docs
metadata, not a routing concern. No router carries a `/v1`-style path prefix;
every router mounts directly at its own resource path (`/runs`, `/pipelines`,
`/budget`, `/secrets`, `/tenant`, `/onboarding`, `/admin`, `/llm`, plus
unversioned `/health`/`/config`). No versioning scheme (path, header, or
media-type) exists today. This is a greenfield decision, not a migration of
an already-versioned API.

The frontend calls through exactly one chokepoint per request pattern —
`frontend/src/lib/api.ts` / `authed-fetch.ts`, both building
`` `${NEXT_PUBLIC_API_URL}${path}` `` — so whatever versioning scheme is
adopted only has to change in one or two places on the client side, not at
every call site.

## Decision

**URI path versioning, adopted prospectively — not applied retroactively.**
Every route that exists today (`/runs`, `/pipelines`, `/budget`, `/secrets`,
`/tenant`, `/onboarding`, `/admin`, `/llm`, `/health`, `/config`) is treated
as the implicit, frozen `v1` contract as of this ADR's date, **left
unprefixed** — no code change ships with this ADR. A version prefix
(`/v2/...`) is added only the first time a genuinely breaking change is
needed, and only to the router(s) that change; unaffected routers stay
unprefixed indefinitely.

**Why URI path versioning over header-based (`Accept:
application/vnd.ai-etl.v2+json`) or query-param versioning.** Path versioning
is visible in every log line, every browser network tab, and every FastAPI
OpenAPI docs page without extra tooling — this project's existing debugging
posture (structured JSON logs, `ADR-033`) already benefits from the version
being in the URL itself rather than hidden in a header a log line might not
capture. It is also the simplest to implement in FastAPI (`APIRouter(prefix="/v2/pipelines")`
is a one-line change, no custom content-negotiation middleware), matching
this project's repeated "the simplest mechanism that solves the actual
problem" precedent (`ADR-033`'s hand-written `JsonFormatter` over a logging
library; this same Wave 3's `ADR-041` circuit breaker over a `pybreaker`
dependency).

**Why "unprefixed = frozen v1" instead of retroactively renaming every route
to `/v1/...` now.** Retroactively adding `/v1` to routes with real frontend
consumers today would be a breaking change for zero present benefit — no
`/v2` exists yet, so there is nothing to disambiguate from. This mirrors
`ADR-030` Decision 1's own principle for this project's frontend routing:
don't move a URL without a concrete reason, and "we might version someday" is
not yet a concrete reason. The first real `/v2` router is what triggers
retroactively documenting (not renaming) the unprefixed routes as "v1" in
the OpenAPI title/description.

**What counts as a breaking change (requires a new version prefix) vs. not
(ships in place, no prefix change):**

| Change | Breaking? |
|---|---|
| Adding a new endpoint | No |
| Adding an optional request field / new response field | No |
| Removing or renaming a response field an existing client reads | **Yes** |
| Changing a field's type or semantics (e.g. `cost_usd` cents → dollars) | **Yes** |
| Removing an endpoint or changing its path | **Yes** |
| Changing required-auth semantics (e.g. a route that gains a new mandatory scope) | **Yes** |
| Changing default behavior a client silently relies on (e.g. default sort order) | **Yes** |
| Tightening validation that rejects previously-accepted input | **Yes** |

This table exists so a future PR reviewer (human or Claude Code, given this
project's own `CLAUDE.md` "SR Big Tech standard applied automatically"
convention) has a concrete checklist rather than a judgment call each time —
same spirit as `ALLOWED_MODELS_BY_PROVIDER`'s explicit-allowlist-over-
heuristic pattern in `core/llm.py`.

**Deprecation window once a `/v2` exists: minimum 90 days of the old
unprefixed route staying live and functional, announced in
`docs/CURRENT_STATE.md` and (once real tenants exist) via the notification
channels `ADR-034` already built.** 90 days, not a shorter or longer figure,
because this project currently has no automated API-consumer registry or
usage telemetry to know who's still on the old version (a real gap this ADR
does not attempt to close) — a conservative window compensates for that blind
spot until `ADR-033`'s observability work (or a future API-analytics
follow-up) makes a data-driven shorter window defensible. **Old-version
routes are never silently removed** — the router module stays in the
codebase, marked deprecated in its own docstring and in FastAPI's
`deprecated=True` route flag (renders in the auto-generated OpenAPI docs),
until the window elapses and removal ships as its own explicit, documented
PR.

**Frontend impact, by construction, is minimal.** `NEXT_PUBLIC_API_URL` stays
the deployment-wide base; only the small number of paths built inside
`api.ts`/`authed-fetch.ts` for a specifically-versioned resource would gain a
`/v2` segment — never a global find/replace across every frontend call site.

## Consequences

- Zero code change ships with this ADR — every existing route keeps working
  exactly as it does today. This is a policy decision, establishing what
  happens the *next* time a breaking API change is needed, not a migration.
- The next genuinely breaking backend change has a concrete, pre-agreed
  mechanism (path-prefix the new router, keep the old one live 90 days,
  document in `docs/CURRENT_STATE.md`) instead of an ad hoc decision made
  under time pressure when the need first arises.
- `FastAPI(version=...)`'s OpenAPI metadata string should be bumped
  (`1.0.0` → `2.0.0`) the first time a `/v2` router actually ships, as a
  documentation signal — not before.
- **Explicitly out of scope:** a client-facing API changelog page, automated
  contract tests pinning response shapes per version (the same "contract
  tests" gap this same Wave 3 triage already deferred, per
  `docs/CURRENT_STATE.md`'s 2026-08-27 entry), and any usage-telemetry
  system that would let a future deprecation window be data-driven instead of
  the conservative fixed 90 days chosen here.
