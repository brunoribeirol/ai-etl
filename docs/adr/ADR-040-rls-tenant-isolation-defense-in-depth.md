# ADR-040: RLS Tenant Isolation Defense-in-Depth (Two-Role Design)

**Status:** Accepted, code complete
**Date:** 2026-08-27
**Sprint:** ad hoc (ahead of schedule — see Trigger below)

**Supersedes [ADR-032](ADR-032-security-posture-admin-role-sast.md) Decision 1**,
which kept `rolbypassrls=true` and explicitly deferred this work to a stated
trigger. ADR-032 itself is updated with a superseded-by note pointing here
(both in its Status line and inline at the top of Decision 1's own section).

## Context

ADR-032 Decision 1 accepted, as a conscious risk, that this application's
Postgres connection role bypasses Row Level Security entirely
(`rolbypassrls=true`). RLS was only ever closing the
PostgREST/anon-key-reachable hole Supabase opens by default on every new
table — it provided **zero** defense-in-depth against a future
`WHERE tenant_id = :id` bug in this codebase's own query functions. That ADR
rejected building a real non-bypass role + RLS policies ("Option B") for
three concrete reasons, and set an explicit trigger for revisiting: "opening
the product beyond Bruno's own controlled testing to external paying
tenants."

**The project owner decided to build Option B now, ahead of that trigger.**
This ADR is that follow-up: a second, non-bypass Postgres role, per-request
GUC scoping, and real RLS policies on every tenant-scoped table.

## Decision — two roles, one new connection-scoping mechanism

### 1. Two Postgres roles, two SQLAlchemy engines

- **`ai_etl_app`** (existing, unchanged) — `rolbypassrls=true`. Kept for:
  - `audit/admin_log.py` and the platform-admin routes it backs
    (`api/routers/admin.py`, ADR-032 Decision 2) — these must read across
    every tenant by design.
  - Background/scheduler jobs that are not acting on behalf of any single
    tenant's request: `audit/db/pipelines.py::list_due_pipelines`,
    `claim_due_pipeline`, `release_pipeline_claim`, `record_pipeline_run`;
    `audit/db/health.py::record_pipeline_health`;
    `audit/db/retention.py::list_tenants_with_retention`;
    `audit/db/tenants.py::list_all_tenants`;
    `audit/db/budget.py::get_global_avg_run_cost_usd`.
  - `services/secrets_service.py`, `services/tenant_deletion_service.py`,
    `services/retention_service.py`'s own direct queries were **out of this
    ADR's original scope** — migrated 2026-08-30, see Residual risk below
    for what's closed and what's still deliberately bypass-only.
- **`ai_etl_app_tenant`** (new, migration `0021`) — `NOBYPASSRLS`, granted
  `SELECT`/`INSERT`/`UPDATE`/`DELETE` on exactly the tenant-scoped tables
  listed below, nothing else. Used by every per-tenant-request read/write in
  `audit/db/*.py` that isn't one of the exemptions above.

Rejected: migrating the existing role in place (removes the admin/background
bypass path entirely — Decision 2 and the scheduler both need it) and a
single engine with conditional GUC logic (exactly the "getting the boundary
wrong" risk ADR-032 flagged — a clean role/engine split makes the two paths
structurally distinguishable in code review, not just by convention).

### 2. Per-request GUC scoping — `tenant_scope()` (`audit/connection.py`)

```python
@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[Connection]:
    engine = get_tenant_engine()
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        yield conn
```

Every RLS-backed read/write opens its connection through this one function
(or `scoped_connection(tenant_id)`, a thin wrapper that falls back to the
bypass engine when `tenant_id` is `None` — see below) — a single choke point
for the GUC binding, rather than each of the ~30 call sites repeating it.

**Why `set_config(..., true)` and not `SET LOCAL app.tenant_id = ...`:**
`SET LOCAL` is session-config syntax, not a normal statement — it does not
accept a bound parameter, which would force interpolating `tenant_id`
directly into the SQL string. That is exactly the f-string-SQL pattern this
project's non-negotiable rules forbid, with no exception carved out for an
internally-sourced value (CLAUDE.md is explicit: bound parameters, always).
`set_config(name, value, is_local)` is a regular function call — `tenant_id`
is a normal bound parameter — and its third argument (`true`) gives it
identical transaction-local semantics to `SET LOCAL`: the setting reverts
automatically at `COMMIT`/`ROLLBACK`.

**How the pooled-connection-safety claim was verified, not just reasoned
about:** `tests/integration/test_tenant_isolation_rls.py::
test_set_local_guc_does_not_leak_across_pooled_connection_reuse` builds an
engine with `pool_size=1, max_overflow=0` against the real Postgres test
container, runs one transaction that sets `app.tenant_id`, ends it, then runs
a **second** transaction (necessarily reusing the same physical DBAPI
connection, since the pool holds exactly one) that never calls
`set_config` itself and reads `current_setting('app.tenant_id', true)` —
asserting it comes back empty, not the first transaction's value. This is
the literal scenario ADR-032 was worried about (two different tenants'
requests, one pooled connection) reproduced and proven safe, not assumed.

### 3. `scoped_connection(tenant_id: str | None)` — the backward-compat seam

A handful of `audit/db/*.py` functions accept `tenant_id: str | None = None`
for callers that predate per-tenant scoping (ADR-006) — real callers always
pass one today, and every tenant-scoped table's own `NOT NULL` FK to
`users.id` makes a genuine `None` write fail regardless of which engine
handles it. `scoped_connection` opens `tenant_scope(tenant_id)` when a value
is given, else a plain bypass-engine transaction — one `with` statement per
call site instead of an `if/else` repeated at each one.

### 4. New migration `0021` — restricted role + RLS policies

Creates `ai_etl_app_tenant` (idempotent `CREATE ROLE ... IF NOT EXISTS`
pattern via a `DO $$` block), grants it `CONNECT`/`USAGE`/table privileges,
then for every tenant-scoped table without RLS yet:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <table>
USING (<tenant_column> = current_setting('app.tenant_id', true))
WITH CHECK (<tenant_column> = current_setting('app.tenant_id', true));
```

`current_setting(..., true)` — the `true` "missing_ok" flag — means a
connection with **no** GUC set reads back `NULL`; `column = NULL` is never
true in Postgres, so an unset GUC fails **closed** (denies every row) rather
than raising or, worse, matching everything.

**Tables that got RLS + a real policy for the first time (this migration):**

| Table | Tenant column |
|---|---|
| `users` | `id` (doubles as tenant id — see `audit/models.py`'s own comment) |
| `runs` | `tenant_id` |
| `stage_latencies` | `tenant_id` |
| `analysis_runs` | `tenant_id` |
| `saved_pipelines` | `tenant_id` |
| `tenant_secrets` | `tenant_id` |

**Tables left untouched** (already `ENABLE ROW LEVEL SECURITY` with no
policy, from migrations `0014`/`0017`/`0018`): `tenant_deletion_log`,
`admin_action_log`, `retention_cleanup_log`. All three are written/read
exclusively through the bypass engine (§4 below explains why each is a
legitimate exemption) — RLS-enabled-with-no-policy already denies the
restricted role by default, which is the *correct* behavior for tables the
restricted role should never touch at all, so no policy was added.

**Password handling — an explicit, honest gap, not silently assumed done:**
migration `0021` sets the new role's password to the literal string
`ai_etl_app_tenant`, following this repo's own existing convention for local
Postgres credentials (`docker-compose.yml` already commits
`ai_etl_app`/`ai_etl_app`, `ai_etl_app_test`/`ai_etl_app_test` — none of
these are real secrets, all are trivial and only ever reachable on a
developer's machine or an ephemeral CI container). **Any non-local deployment
must rotate this password** via `ALTER ROLE ai_etl_app_tenant WITH PASSWORD
'...'` from a real secret manager (Railway config) before
`APP_DATABASE_URL_TENANT` is ever pointed at it — this is an operational
follow-up step, not something this migration or this ADR closes.

## Audit of every `audit/db/*.py` query function (Decision requirement 4)

| File | Function | Engine | Why |
|---|---|---|---|
| `runs.py` | `ensure_user` | tenant (`tenant_scope(user_id)`) | The row being created is its own tenant |
| `runs.py` | `save_run` / `_write_run_row` | tenant when `tenant_id` given, else bypass | `scoped_connection`; `tenant_id=None` predates ADR-006 |
| `runs.py` | `load_history` | tenant when `tenant_id` given, else bypass (unscoped, unchanged legacy behavior) | `scoped_connection` |
| `runs.py` | `save_analysis` / `_write_analysis_row` | tenant when `tenant_id` given, else bypass | `scoped_connection` |
| `runs.py` | `save_stage_latencies` | tenant when `tenant_id` given, else bypass | `scoped_connection` |
| `runs.py` | `_run_belongs_to_tenant` | tenant | required `tenant_id` param |
| `runs.py` | `load_full_result` / `_load_analysis_tokens` | tenant when `tenant_id` given, else bypass | ownership already checked by `_run_belongs_to_tenant` |
| `pipelines.py` | `create_saved_pipeline`, `list_saved_pipelines`, `get_saved_pipeline`, `update_saved_pipeline`, `mark_pipeline_approved`, `get_run_status_and_pipeline`, `list_pending_approvals`, `get/set_saved_pipeline_llm_config`, `get_saved_pipeline_notification_config/target`, `set_saved_pipeline_notification_config` | tenant | required `tenant_id` param, one caller's own data |
| `pipelines.py` | `list_due_pipelines`, `claim_due_pipeline`, `release_pipeline_claim`, `record_pipeline_run` | **bypass** | scheduler beat-tick sweep, no single tenant in scope at the call site |
| `health.py` | `get_pipeline_health`, `list_pipeline_run_history` | tenant | required `tenant_id` param |
| `health.py` | `record_pipeline_health` | **bypass** | scheduler-driven, keyed by `pipeline_id` only |
| `health.py` | `get_previous_completed_run` | tenant | **signature change**: added a required `tenant_id` param (the pipeline's own owner, already known to its one caller, `services/alerting.py`) so this read gets the same RLS backstop as every other per-tenant lookup in this module, instead of staying on bypass only because its original query happened not to filter by tenant |
| `budget.py` | `get_monthly_budget`, `set_monthly_budget`, `get_monthly_spend_usd`, `get_avg_run_cost_usd` | tenant | required `tenant_id` param |
| `budget.py` | `get_global_avg_run_cost_usd` | **bypass** | deliberately cross-tenant fallback average |
| `locale.py` | `get_locale`, `set_locale` | tenant | required `tenant_id` param |
| `onboarding.py` | `get_onboarding_status` | tenant | required `tenant_id` param |
| `retention.py` | `get_retention_days`, `set_retention_days` | tenant | required `tenant_id` param |
| `retention.py` | `list_tenants_with_retention` | **bypass** | beat-task cross-tenant sweep, same shape as `list_due_pipelines` |
| `tenants.py` | `list_all_tenants` | **bypass** | admin-panel tenant directory (ADR-032 Decision 2's cross-tenant read) |
| `admin_log.py` | `log_admin_action`, `list_admin_actions` | **bypass**, unchanged | the entire point of this module — Decision 2's audited cross-tenant path |

**Net: 26 functions now go through the restricted engine (RLS-backed) for
their per-tenant-request path; 11 stay on the bypass engine, each for a
documented, structural reason (scheduler/background sweep, deliberate
cross-tenant admin/aggregate read) — not because auditing them was skipped.**

## Alternatives considered

- **Migrate `ai_etl_app` in place to `NOBYPASSRLS`** — rejected: removes the
  legitimate admin/background-job bypass path Decision 2 and the scheduler
  both need; would force every one of those into an ad hoc "temporarily
  re-grant bypass" pattern, worse than a clean second role.
- **One engine, conditional GUC logic per call** — rejected per ADR-032's own
  concern: a single engine makes "did this call remember to scope itself"
  a runtime behavior rather than a structural, code-reviewable fact (which
  engine/import a function uses).
- **`SET LOCAL` with f-string interpolation** — rejected outright: violates
  this project's non-negotiable SQL rule with no exception for internal
  values.

## Residual risk — honestly not fully closed

- ~~`services/secrets_service.py`, `tenant_deletion_service.py`,
  `retention_service.py` still query the bypass engine directly~~ —
  **closed 2026-08-30**: all three now route their tenant-scoped
  reads/writes through `tenant_scope()`, the same restricted role as
  `audit/db/*.py`. `secrets_service.py` migrated in full. The other two
  keep one deliberate exception each: `tenant_deletion_log`/
  `retention_cleanup_log` writes stay on the bypass engine, since both
  tables have RLS enabled with no policy (migrations `0014`/`0018`) and
  would deny the restricted role entirely — not an oversight, the same
  exemption already documented above for those two tables.
  `tenant_run_storage_candidates()` (shared with
  `services/tenant_export_service.py`) changed from taking an `Engine` to
  a `Connection`, so callers control which role opens it —
  `tenant_export_service.py` itself was **not** migrated in this pass
  (still bypass engine, out of scope; a real next increment). Verified
  against a live local Postgres, not just unit-tested with mocks: a fresh
  `alembic upgrade head`, then `set_secret`/`get_secret`/`list_secret_names`/
  `delete_secret`, `cleanup_expired_retention_for_tenant`, and
  `delete_tenant_data` all called for real through the restricted role,
  each completing without a permission error.
- **Password rotation for `ai_etl_app_tenant` in any non-local deployment is
  a manual, undone step** — flagged explicitly above, not automated by this
  migration or this ADR. **Update 2026-08-30**: done for the current Railway
  production deployment — migration applied, password rotated, both
  `ai-etl` and `tranquil-appreciation` services configured with
  `APP_DATABASE_URL_TENANT`, verified live (no `EnvironmentError` in
  runtime logs post-deploy). Still a manual step for any *future*
  from-scratch deployment — this ADR doesn't automate it, the fix above was
  operational, not a code change.
- **`get_previous_completed_run`'s signature change** required updating its
  one call site (`services/alerting.py`) and every test calling it — done,
  but any *other*, not-yet-written caller of this function would need the
  same update; it is no longer callable with the old two-argument signature.

## Related

- [ADR-032](ADR-032-security-posture-admin-role-sast.md) — the decision this
  supersedes (Decision 1 only; Decisions 2-4 unchanged).
- [ADR-006](ADR-006-clerk-auth-supabase-postgres-tenancy.md) — tenant FK on
  `runs`/`analysis_runs`, referenced throughout `audit/db/*.py`'s docstrings
  as the "session-UUID stopgap" this superseded.
- `tests/integration/test_tenant_isolation_rls.py` — the real-Postgres proof
  this ADR's two central claims (cross-tenant read blocked, GUC doesn't leak
  across a pooled connection) actually hold.
