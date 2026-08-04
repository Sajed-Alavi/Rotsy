"""backup schedules: recurring backup configuration + retention

Revision ID: 20260802_0900
Revises: 20260801_1400
Create Date: 2026-08-02 09:00:00.000000

Adds ``backup_schedules`` (one row per independently-enabled recurring backup
config — daily/weekly/monthly/cron cadence, its own retention rule) and a
nullable ``schedule_id`` FK on ``backup_runs`` so runs triggered by a schedule
are attributable to it (manual/on-demand runs keep it NULL and are never
auto-pruned).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0900"
down_revision: Union[str, None] = "20260801_1400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backup_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("repos", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("time_of_day", sa.String(length=5), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("cron_expression", sa.String(length=128), nullable=True),
        sa.Column("retention_keep_last", sa.Integer(), nullable=True),
        sa.Column("retention_max_age_days", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_backup_schedules_next_run_at", "backup_schedules", ["next_run_at"])
    op.create_index("ix_backup_schedules_enabled", "backup_schedules", ["enabled"])

    op.add_column("backup_runs", sa.Column("schedule_id", sa.Integer(), nullable=True))
    op.create_index("ix_backup_runs_schedule_id", "backup_runs", ["schedule_id"])
    op.create_foreign_key(
        "fk_backup_runs_schedule_id", "backup_runs", "backup_schedules",
        ["schedule_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_backup_runs_schedule_id", "backup_runs", type_="foreignkey")
    op.drop_index("ix_backup_runs_schedule_id", table_name="backup_runs")
    op.drop_column("backup_runs", "schedule_id")

    op.drop_index("ix_backup_schedules_enabled", table_name="backup_schedules")
    op.drop_index("ix_backup_schedules_next_run_at", table_name="backup_schedules")
    op.drop_table("backup_schedules")
