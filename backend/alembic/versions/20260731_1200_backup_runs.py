"""backup runs: persisted history for the byte-level archive feature

Revision ID: 20260731_1200
Revises: 20260730_0100
Create Date: 2026-07-31 12:00:00.000000

Adds ``backup_runs`` — one row per full/selective archive run, so a backup
that isn't tracked anywhere after its background job's 7-day Redis TTL
expires still has a durable record: which repos were included, where the
archive landed on disk, its totals, and whether it succeeded.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_1200"
down_revision: Union[str, None] = "20260730_0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("repos", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("output_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("asset_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_backup_runs_status", "backup_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_backup_runs_status", table_name="backup_runs")
    op.drop_table("backup_runs")
