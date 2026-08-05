"""sonar: analysis run trigger + metric breakdown

Revision ID: 20260806_0900
Revises: 20260805_1000
Create Date: 2026-08-06 09:00:00.000000

Adds columns needed for the analysis history/detail views and manual
analysis: ``trigger`` (push vs. manual — both run through the same job
handler, this only records which one asked), and a breakdown of
``issues_count`` into bugs/vulnerabilities/code_smells/security_hotspots so
the detail page doesn't have to guess a decomposition after the fact.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0900"
down_revision: Union[str, None] = "20260805_1000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("trigger", sa.String(length=16), nullable=False, server_default="push"))
    op.add_column("analysis_runs", sa.Column("bugs", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("vulnerabilities", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("code_smells", sa.Integer(), nullable=True))
    op.add_column("analysis_runs", sa.Column("security_hotspots", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_runs", "security_hotspots")
    op.drop_column("analysis_runs", "code_smells")
    op.drop_column("analysis_runs", "vulnerabilities")
    op.drop_column("analysis_runs", "bugs")
    op.drop_column("analysis_runs", "trigger")
