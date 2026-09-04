# ADR-045: REST Source Tenant-Scoped Credentials (`secret_ref`)

**Status:** Accepted
**Date:** 2026-09-03
**Sprint:** post-TCC hardening (owner-requested, following the platform audit)

## Context

ADR-022 (Sprint 19) designed `rest_source.py`'s `auth` shape to eventually
accept a `secret_ref` field alongside its existing `env_var` fields, as the
natural next integration point for tenant-scoped secrets — but explicitly
left it unwired. ADR-044 (2026-08-31) wired Postgres/MySQL/MongoDB, and
again explicitly deferred REST as a separate follow-up. The platform audit
(2026-09-03) flagged this as the one remaining gap between what ADR-022
designed and what actually reads a tenant's stored secret.

## Decision

Every `auth` field that currently reads an env var now has a `*_secret_ref`
counterpart, tried first:

| Auth type | env var field(s) | secret ref field(s) |
|---|---|---|
| `api_key` | `env_var` | `secret_ref` |
| `bearer` | `env_var` | `secret_ref` |
| `basic` | `username_env_var`, `password_env_var` | `username_secret_ref`, `password_secret_ref` |
| `oauth2_client_credentials` | `client_id_env_var`, `client_secret_env_var` | `client_id_secret_ref`, `client_secret_ref` |

**Resolution is lazy, unlike ADR-044's DB overrides.** Postgres/MySQL/
MongoDB each have exactly one fixed secret name per source type, resolvable
up front in `pipeline_service.py` before the graph runs. A REST source's
credential has no such fixed name — the Orchestrator node's LLM-produced
`pipeline_plan` names whichever secret a given `auth` block should use, and
that plan doesn't exist until *during* the graph run. So `core/
tenant_context.py` gained a second, complementary mechanism: the raw
`tenant_id` itself is put in a `ContextVar` (alongside the pre-resolved DB
overrides dict), and a new `get_rest_secret(secret_ref)` resolves an
arbitrary name against `secrets_service.get_secret(tenant_id, secret_ref)`
at call time, inside `rest_source.py`. Same non-negotiable as ADR-044: the
decrypted value only ever exists in this `ContextVar` and the connector's
local stack, never in `PipelineState`/`pipeline_plan`/a run's JSON snapshot.

A `secret_ref` that doesn't resolve (no active tenant context, or the
tenant never saved a secret under that name) falls back to the paired
`env_var` field if the plan also set one — same "not configured yet, fall
back" posture ADR-044 established — or raises a clear, actionable error if
there's no fallback to use, rather than a bare `KeyError`.

## Alternatives considered

- **Pre-resolve every REST secret the same way DB overrides are, before
  the graph runs.** Rejected: impossible without either running the
  Orchestrator twice (once to get `pipeline_plan`, once for real) or
  guessing which secret names a plan might reference before it exists —
  neither is defensible.
- **Have the Orchestrator resolve `secret_ref` itself and embed the literal
  value into `pipeline_plan`.** Rejected outright, same reasoning ADR-044
  already established in depth: `pipeline_plan` is part of `PipelineState`,
  which gets serialized wholesale into every run's JSON snapshot.

## Consequences

- **Positive**: closes the last gap between ADR-022's original design and
  actual wiring — every source type this project has now supports a
  tenant's own credential.
- **Positive**: no new API surface — a tenant saves a REST secret the same
  way as a DB one, via the existing `POST /secrets` endpoint, naming it
  whatever `secret_ref` their pipeline spec asks for.
- **Negative — accepted**: unlike the DB case's 3 fixed names, a REST
  secret's name is plan-dependent, so there's no single documented
  convention to put in the `/secrets` page's hint text the way ADR-044's
  three names are — a tenant has to know what name their own spec (or the
  Orchestrator's plan) will reference. Acceptable for now; revisit if REST
  tenant credentials see real use and the naming friction turns out to
  matter.

## Related

- [ADR-022](ADR-022-rbac-org-sso-tenant-secrets.md) — original design of
  `secret_ref` alongside `env_var`.
- [ADR-044](ADR-044-tenant-scoped-db-source-credentials.md) — the DB-source
  sibling this ADR completes, and the `core/tenant_context.py` module both
  share.
