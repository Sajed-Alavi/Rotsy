"""metrics and alert rules

Revision ID: 20260718_2000
Revises: 20260717_2000
Create Date: 2026-07-18 20:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260718_2000"
down_revision: Union[str, None] = "20260717_2000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("metric_type", sa.String(length=32), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_metrics_timestamp", "metrics", ["timestamp"])
    op.create_index("ix_metrics_repo", "metrics", ["repo"])

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("condition", sa.String(length=2), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("repo_filter", sa.String(length=255), nullable=True),
        sa.Column("webhook_url", sa.String(length=512), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("alert_rules")
    op.drop_index("ix_metrics_repo", table_name="metrics")
    op.drop_index("ix_metrics_timestamp", table_name="metrics")
    op.drop_table("metrics")
