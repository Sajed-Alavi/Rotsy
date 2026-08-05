"""sonar module: projects, analysis runs, quality gate results

Revision ID: 20260805_1000
Revises: 20260805_0930
Create Date: 2026-08-05 10:00:00.000000

Owned by ``app/modules/sonar``. ``analysis_runs.commit_sha`` is deliberately
a plain string column, not a foreign key into a GitHub/GitLab table — an
analysis run must be describable without knowing which source module
produced the commit.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_1000"
down_revision: Union[str, None] = "20260805_0930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sonar_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sonar_project_key", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sonar_projects_project_id", "sonar_projects", ["project_id"])
    op.create_unique_constraint("uq_sonar_projects_key", "sonar_projects", ["sonar_project_key"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sonar_project_id", sa.Integer(), sa.ForeignKey("sonar_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("issues_count", sa.Integer(), nullable=True),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("duplication_pct", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_analysis_runs_sonar_project_id", "analysis_runs", ["sonar_project_id"])
    op.create_unique_constraint(
        "uq_analysis_runs_project_sha", "analysis_runs", ["sonar_project_id", "commit_sha"]
    )

    op.create_table(
        "quality_gate_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_quality_gate_results_analysis_run_id", "quality_gate_results", ["analysis_run_id"])


def downgrade() -> None:
    op.drop_index("ix_quality_gate_results_analysis_run_id", table_name="quality_gate_results")
    op.drop_table("quality_gate_results")

    op.drop_constraint("uq_analysis_runs_project_sha", "analysis_runs", type_="unique")
    op.drop_index("ix_analysis_runs_sonar_project_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")

    op.drop_constraint("uq_sonar_projects_key", "sonar_projects", type_="unique")
    op.drop_index("ix_sonar_projects_project_id", table_name="sonar_projects")
    op.drop_table("sonar_projects")
