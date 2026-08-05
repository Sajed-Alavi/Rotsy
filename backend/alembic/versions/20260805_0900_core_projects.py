"""core: projects, integrations, insights

Revision ID: 20260805_0900
Revises: 20260802_0900
Create Date: 2026-08-05 09:00:00.000000

Foundation tables for the modular-monolith direction (Rotsy as a DevSecOps
intelligence platform over GitHub/GitLab, SonarQube, and Nexus).

``projects`` is the cross-module anchor entity. ``integrations`` is one row
per (project, module) connection, vendor-agnostic — module-owned tables such
as ``github_repositories`` will carry their own ``project_id`` FK later, but
never a FK into another module's tables. ``insights`` is the Smart Insights
engine's output; it is denormalized on write so it never needs to join across
module tables to render.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0900"
down_revision: Union[str, None] = "20260802_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("module_key", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("credential_ref", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_integrations_project_id", "integrations", ["project_id"])
    op.create_unique_constraint(
        "uq_integrations_project_module", "integrations", ["project_id", "module_key"]
    )

    op.create_table(
        "insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("related_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("related_source", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_insights_project_id", "insights", ["project_id"])
    op.create_index("ix_insights_related_commit_sha", "insights", ["related_commit_sha"])


def downgrade() -> None:
    op.drop_index("ix_insights_related_commit_sha", table_name="insights")
    op.drop_index("ix_insights_project_id", table_name="insights")
    op.drop_table("insights")

    op.drop_constraint("uq_integrations_project_module", "integrations", type_="unique")
    op.drop_index("ix_integrations_project_id", table_name="integrations")
    op.drop_table("integrations")

    op.drop_table("projects")
