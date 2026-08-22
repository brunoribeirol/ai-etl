"""tenant_secrets table (ADR-022, Sprint 19)

New, standalone table — no changes to any existing table. Stores
Fernet-encrypted external-source credentials scoped by tenant, one row per
(tenant_id, name). Purely additive: no backfill needed, no existing table
touched.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_secrets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint(
        "uq_tenant_secrets_tenant_name", "tenant_secrets", ["tenant_id", "name"]
    )
    op.create_index("ix_tenant_secrets_tenant", "tenant_secrets", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_secrets_tenant", table_name="tenant_secrets")
    op.drop_constraint("uq_tenant_secrets_tenant_name", "tenant_secrets", type_="unique")
    op.drop_table("tenant_secrets")
