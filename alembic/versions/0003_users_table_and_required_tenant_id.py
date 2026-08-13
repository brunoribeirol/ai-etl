"""users table and required tenant_id — Clerk auth / real tenancy (ADR-006)

Supersedes ADR-005's session-UUID stopgap. `tenant_id` on `runs`/
`analysis_runs` now maps to a real Clerk user account instead of a per-
browser-session UUID, so it becomes `NOT NULL` with a foreign key to the new
`users` table (`id` = Clerk `user_id`, a string — Clerk ids are not UUIDs).

Backfill decision (explicit, per ADR-006 and confirmed by Bruno): this is a
pre-launch project with no real user base yet. Rows written under the ADR-005
session-UUID scheme have no corresponding `users` row and cannot satisfy the
new `NOT NULL` FK — they are test data, not real user data, so `upgrade()`
deletes them outright rather than archiving them or inventing synthetic user
records. `downgrade()` cannot un-delete that data; it only reverses the
schema change (drop FK/NOT NULL, drop `users`), which is expected and fine
for a pre-launch project.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ADR-005 test data: no corresponding `users` row exists for any session-UUID
    # `tenant_id` written so far, and none can satisfy the NOT NULL FK below.
    # No real users exist yet — delete rather than archive/backfill (see module docstring).
    op.execute("DELETE FROM analysis_runs")
    op.execute("DELETE FROM runs")

    op.alter_column("runs", "tenant_id", existing_type=sa.String(), nullable=False)
    op.create_foreign_key(
        "fk_runs_tenant_id_users",
        "runs",
        "users",
        ["tenant_id"],
        ["id"],
    )

    op.alter_column("analysis_runs", "tenant_id", existing_type=sa.String(), nullable=False)
    op.create_foreign_key(
        "fk_analysis_runs_tenant_id_users",
        "analysis_runs",
        "users",
        ["tenant_id"],
        ["id"],
    )


def downgrade() -> None:
    # Data deleted in upgrade() cannot be un-deleted here — only the schema
    # change is reversed (expected and fine for this pre-launch project).
    op.drop_constraint("fk_analysis_runs_tenant_id_users", "analysis_runs", type_="foreignkey")
    op.alter_column("analysis_runs", "tenant_id", existing_type=sa.String(), nullable=True)

    op.drop_constraint("fk_runs_tenant_id_users", "runs", type_="foreignkey")
    op.alter_column("runs", "tenant_id", existing_type=sa.String(), nullable=True)

    op.drop_table("users")
