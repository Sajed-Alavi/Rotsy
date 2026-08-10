"""retention: per-policy interval schedule override

Revision ID: 20260815_0900
Revises: 20260814_0900
Create Date: 2026-08-15 09:00:00.000000

Retention policies previously all shared one daily sweep time
(``RETENTION_RUN_AT``), with no way to run a specific policy more often (or
less). Adds ``interval_minutes`` (null = keep the legacy shared-daily
behavior) and ``next_run_at``, precomputed the same way
``backup_schedules.next_run_at`` already is, so the new poll loop's query is
a plain indexed comparison rather than recomputing a cadence per row per tick.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0900"
down_revision: Union[str, None] = "20260814_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "retention_policies",
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "retention_policies",
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_retention_policies_next_run_at", "retention_policies", ["next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_retention_policies_next_run_at", table_name="retention_policies")
    op.drop_column("retention_policies", "next_run_at")
    op.drop_column("retention_policies", "interval_minutes")
