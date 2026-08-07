"""sonar: per-issue and per-hotspot findings

Revision ID: 20260813_0900
Revises: 20260812_0900
Create Date: 2026-08-13 09:00:00.000000

Adds ``sonar_issues`` and ``sonar_hotspots`` — one row per finding, the same
shape ``scan_vulnerabilities`` gives container-scan findings — so an
AnalysisRun's detail view/export can show every bug, vulnerability, code
smell, and security hotspot Sonar reported, not just the aggregate counts
already on ``analysis_runs``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_0900"
down_revision: Union[str, None] = "20260812_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sonar_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(),
                   sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issue_key", sa.String(64), nullable=False),
        sa.Column("rule", sa.String(128), nullable=False, server_default=""),
        sa.Column("severity", sa.String(16), nullable=False, server_default="INFO"),
        sa.Column("type", sa.String(16), nullable=False, server_default="CODE_SMELL"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("component", sa.String(1024), nullable=False, server_default=""),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=""),
        sa.Column("assignee", sa.String(255), nullable=False, server_default=""),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("effort", sa.String(32), nullable=False, server_default=""),
        sa.Column("debt", sa.String(32), nullable=False, server_default=""),
        sa.Column("clean_code_attribute", sa.String(64), nullable=True),
        sa.Column("creation_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("update_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sonar_issues_analysis_run_id", "sonar_issues", ["analysis_run_id"])
    op.create_index("ix_sonar_issues_severity", "sonar_issues", ["severity"])
    op.create_index("ix_sonar_issues_type", "sonar_issues", ["type"])

    op.create_table(
        "sonar_hotspots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(),
                   sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hotspot_key", sa.String(64), nullable=False),
        sa.Column("component", sa.String(1024), nullable=False, server_default=""),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default=""),
        sa.Column("vulnerability_probability", sa.String(16), nullable=False, server_default=""),
        sa.Column("security_category", sa.String(64), nullable=False, server_default=""),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column("creation_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("update_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sonar_hotspots_analysis_run_id", "sonar_hotspots", ["analysis_run_id"])


def downgrade() -> None:
    op.drop_index("ix_sonar_hotspots_analysis_run_id", table_name="sonar_hotspots")
    op.drop_table("sonar_hotspots")
    op.drop_index("ix_sonar_issues_type", table_name="sonar_issues")
    op.drop_index("ix_sonar_issues_severity", table_name="sonar_issues")
    op.drop_index("ix_sonar_issues_analysis_run_id", table_name="sonar_issues")
    op.drop_table("sonar_issues")
