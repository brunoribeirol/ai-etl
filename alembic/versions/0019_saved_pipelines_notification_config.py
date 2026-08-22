"""saved_pipelines.notification_channel / notification_target_ciphertext /
notification_active (ADR-034, Sprint 37)

Per-`saved_pipeline` notification destination, replacing the
deployment-global `RESEND_API_KEY`/`SLACK_WEBHOOK_URL`/`TEAMS_WEBHOOK_URL`/
`GOOGLE_CHAT_WEBHOOK_URL` env vars as the *only* delivery target
(`services/notifications.py` keeps those as the fallback when a pipeline has
no override — zero behavior change until a tenant explicitly configures one
via `PUT /pipelines/{id}/notifications`).

`notification_channel`/`notification_target_ciphertext` are both nullable
with no server default: `NULL`/`NULL` (the state of every existing row after
this migration) means "no override, use this deployment's global channel(s)".
`notification_target_ciphertext` is a Fernet token encrypted with the same
key/mechanism as `services/secrets_service.py`
(`AI_ETL_SECRETS_ENCRYPTION_KEY`) — never plaintext at rest.

Revision ID: 0019
Revises: 0017
Create Date: 2026-08-22

Note: pre-assigned as 0019 to avoid a collision with Sprint 36, which was
running in a parallel worktree at the same time and may have already
claimed 0018 by the time this branch merges into `main`. If `0018` exists on
`main` when this PR is opened, its own `down_revision` should already point
at `0017` (same reconciliation pattern as Sprints 14/17/29/31/36) — this
migration's `down_revision` stays `0017` unmodified; whichever of 0018/0019
lands on `main` second is the one that needs a `down_revision` bump to chain
onto the other, resolved at merge time, not by editing this file speculatively.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "saved_pipelines",
        sa.Column("notification_channel", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "saved_pipelines",
        sa.Column("notification_target_ciphertext", sa.Text(), nullable=True),
    )
    op.add_column(
        "saved_pipelines",
        sa.Column(
            "notification_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("saved_pipelines", "notification_active")
    op.drop_column("saved_pipelines", "notification_target_ciphertext")
    op.drop_column("saved_pipelines", "notification_channel")
