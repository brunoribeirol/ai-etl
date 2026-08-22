"""saved_pipelines.llm_provider / llm_model (ADR-031, Sprint 30)

Per-`saved_pipeline` LLM provider/model override. Both columns are nullable with
no server default: `NULL`/`NULL` (the state of every existing row after this
migration) means "no override, use this deployment's global
`AI_ETL_LLM_PROVIDER`/`AI_ETL_LLM_MODEL`" — zero behavior change until a tenant
explicitly sets both via `PUT /pipelines/{id}/llm-config`
(`api/routers/pipelines.py`). Always written or cleared together, never one `NULL`
and the other not — see `audit/db.py::set_saved_pipeline_llm_config`.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_pipelines",
        sa.Column("llm_provider", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "saved_pipelines",
        sa.Column("llm_model", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("saved_pipelines", "llm_model")
    op.drop_column("saved_pipelines", "llm_provider")
