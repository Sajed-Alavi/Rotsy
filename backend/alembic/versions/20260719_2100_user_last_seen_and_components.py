"""user last_seen_at column + components table (for retention/sync)

Revision ID: 20260719_2100
Revises: 20260718_2000
Create Date: 2026-07-19 21:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_2100"
down_revision: Union[str, None] = "20260718_2000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Track last activity per user for idle-timeout enforcement.
    op.add_column(
        "users",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Retention policies: rule-based deletion (keep last N tags / delete older than X days).
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        # 'keep_last_n' or 'delete_older_than_days' (both can be set together).
        sa.Column("keep_last_n", sa.Integer(), nullable=True),
        sa.Column("delete_older_than_days", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_retention_policies_repo", "retention_policies", ["repo"])


def downgrade() -> None:
    op.drop_index("ix_retention_policies_repo", table_name="retention_policies")
    op.drop_table("retention_policies")
    op.drop_column("users", "last_seen_at")
