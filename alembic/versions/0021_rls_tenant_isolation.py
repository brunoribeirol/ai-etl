"""RLS defense-in-depth: restricted tenant role + real policies (ADR-040)

Supersedes ADR-032 Decision 1's "keep `rolbypassrls=true`, no production
change this sprint" — the owner decided to build Option B (a second,
non-bypass Postgres role plus real RLS policies) ahead of that decision's
own stated trigger. See `docs/adr/ADR-040-rls-tenant-isolation-defense-in-depth.md`
for the full design.

This migration does two things:

1. Creates `ai_etl_app_tenant` — a new, non-bypass (`NOBYPASSRLS`) Postgres
   role — if it doesn't already exist, and grants it exactly the privileges
   the restricted engine (`audit.connection.get_tenant_engine`) needs:
   `CONNECT` on the current database, `USAGE` on the `public` schema, and
   `SELECT`/`INSERT`/`UPDATE`/`DELETE` on the tenant-scoped tables this
   migration also enables RLS on.

   The password set here (`ai_etl_app_tenant`, matching the role name) is a
   **local-development-only default**, following this repo's own existing
   convention for `docker-compose.yml`'s local/test Postgres credentials
   (e.g. `ai_etl_app`/`ai_etl_app`, `ai_etl_app_test`/`ai_etl_app_test`) —
   none of these are real secrets, all are trivial, committed, and only ever
   reachable on a developer's own machine or an ephemeral CI container.
   **Any non-local deployment (Railway, Supabase) MUST rotate this password**
   via `ALTER ROLE ai_etl_app_tenant WITH PASSWORD '...'` from a real secret
   manager before `APP_DATABASE_URL_TENANT` is ever pointed at it — this
   migration only creates the role and its privileges, never a production
   credential. Flagged explicitly in ADR-040 as an operational follow-up,
   not silently assumed done.

2. Enables Row Level Security and a real `USING` policy — comparing each
   table's own tenant-identifying column against
   `current_setting('app.tenant_id', true)` (the `true` "missing_ok" flag
   means a connection with no GUC set at all reads back `NULL`, and
   `column = NULL` is never true in Postgres — so an unset GUC fails
   *closed*, denying every row, rather than raising or matching everything)
   — on every tenant-scoped table that does not already have RLS enabled
   from an earlier migration: `users`, `runs`, `stage_latencies`,
   `analysis_runs`, `saved_pipelines`, `tenant_secrets`.

   `tenant_deletion_log`, `admin_action_log`, and `retention_cleanup_log`
   already have `ENABLE ROW LEVEL SECURITY` (migrations `0014`/`0017`/`0018`)
   with no policy — left exactly as-is here. All three are written/read
   exclusively through the bypass engine (`admin_log.py`,
   `tenant_deletion_service.py`, `retention_service.py` — see ADR-040 §4 for
   why each is a legitimate bypass exemption), so they were never candidates
   for the restricted role's GRANTs or a tenant-matching policy in the first
   place; RLS-enabled-with-no-policy already denies the restricted role (or
   any non-bypass role) by default, which is the correct behavior for
   tables the restricted role should never touch at all.

Every statement here is `op.execute()` on a fixed, non-user-supplied Python
constant (role/table/column names hardcoded above) — never request-sourced
input, so this isn't the f-string-SQL-injection pattern CLAUDE.md's
non-negotiable rule forbids (that rule is about *values*, e.g. a `tenant_id`
coming from a request, which Postgres DDL can't bind as a parameter for
identifiers anyway). The one place a *value* would normally need binding —
the current database's name, for `GRANT CONNECT ON DATABASE` — is instead
resolved server-side via `current_database()` inside a `DO $$ ... $$` block
using `format('...', %I, ...)`, which safely quotes it as an identifier
without any string interpolation from this migration's own Python code.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RESTRICTED_ROLE = "ai_etl_app_tenant"

# (table, tenant-identifying column) — `users.id` doubles as the tenant id
# everywhere else calls `tenant_id` (see `audit/models.py`'s own comment).
_TENANT_TABLES = [
    ("users", "id"),
    ("runs", "tenant_id"),
    ("stage_latencies", "tenant_id"),
    ("analysis_runs", "tenant_id"),
    ("saved_pipelines", "tenant_id"),
    ("tenant_secrets", "tenant_id"),
]


def upgrade() -> None:
    # 1. Create the restricted role (idempotent — a second run of this
    # migration, e.g. after a failed partial apply, must not error).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_RESTRICTED_ROLE}') THEN
                CREATE ROLE {_RESTRICTED_ROLE} LOGIN PASSWORD '{_RESTRICTED_ROLE}' NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    # `current_database()` can't be passed as a bind parameter to `GRANT ... ON
    # DATABASE` (it's an identifier position, not a value) — `format(%I, ...)`
    # inside this DO block quotes it safely without string-interpolating
    # anything request-sourced (the whole statement is a fixed constant).
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {_RESTRICTED_ROLE}', current_database());
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_RESTRICTED_ROLE}")

    # 2. RLS + policy on every tenant-scoped table not already covered.
    for table, tenant_column in _TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_RESTRICTED_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING ({tenant_column} = current_setting('app.tenant_id', true))
            WITH CHECK ({tenant_column} = current_setting('app.tenant_id', true))
            """
        )


def downgrade() -> None:
    for table, _tenant_column in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM {_RESTRICTED_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_RESTRICTED_ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {_RESTRICTED_ROLE}', current_database());
        END
        $$;
        """
    )
    # Deliberately does not DROP ROLE: another session/connection may still be
    # using it at downgrade time (DROP ROLE fails if the role owns objects or
    # has an active session), and unlike the tables/policies above, a leftover
    # unprivileged role with no table grants is inert, not a security issue.
