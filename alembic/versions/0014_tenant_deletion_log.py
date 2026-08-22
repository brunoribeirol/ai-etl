"""tenant_deletion_log table (ADR-025, Sprint 24)

New, standalone table — no changes to any existing table. Records evidence
that a tenant data-erasure request (LGPD/GDPR, `DELETE /tenant`) was
fulfilled: per-table row counts deleted, storage keys deleted, and outcome.
Deliberately has no FK to `users.id` — the row it describes is gone by the
time this log entry is written (ADR-025 Decision 2). Purely additive.

RLS is enabled in this same migration per this project's own standing rule
(`SECURITY.md` — "Every new migration that adds a table must enable RLS on
it in the same migration"); this app's connection role has
`rolbypassrls = true`, so this is a safe no-op for application behavior and
only closes the anon/authenticated-default-CRUD hole Supabase opens on every
new table by default.

Revision ID: 0014
Revises: 0012
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_deletion_log",
        sa.Column("id", sa.String(), primary_key=True),
        # Not a FK to users.id — the users row this describes is deleted by
        # the same operation that writes this log row (ADR-025 Decision 2).
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runs_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analysis_runs_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage_latencies_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saved_pipelines_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("secrets_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_keys_deleted", sa.Integer(), nullable=False, server_default="0"),
        # "completed" | "failed" — see ADR-025 Decision 3: a partial storage
        # cleanup failure still leaves status "completed" with `error` set,
        # since the DB-side erasure (the higher-confidence signal) succeeded.
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_tenant_deletion_log_tenant", "tenant_deletion_log", ["tenant_id"])
    op.execute("ALTER TABLE tenant_deletion_log ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_tenant_deletion_log_tenant", table_name="tenant_deletion_log")
    op.drop_table("tenant_deletion_log")
