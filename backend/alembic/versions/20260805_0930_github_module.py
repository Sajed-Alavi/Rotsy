"""github module: installations, repositories

Revision ID: 20260805_0930
Revises: 20260805_0900
Create Date: 2026-08-05 09:30:00.000000

Owned by ``app/modules/github``. ``github_repositories.project_id`` is the
only cross-module reference, and it points at the core ``projects`` table,
never at another module's tables.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0930"
down_revision: Union[str, None] = "20260805_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("integration_id", sa.Integer(), sa.ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_github_installations_integration_id", "github_installations", ["integration_id"])
    op.create_unique_constraint(
        "uq_github_installations_installation_id", "github_installations", ["installation_id"]
    )

    op.create_table(
        "github_repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", sa.Integer(), sa.ForeignKey("github_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("webhook_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_github_repositories_installation_id", "github_repositories", ["installation_id"])
    op.create_index("ix_github_repositories_project_id", "github_repositories", ["project_id"])
    op.create_unique_constraint(
        "uq_github_repositories_full_name", "github_repositories", ["full_name"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_github_repositories_full_name", "github_repositories", type_="unique")
    op.drop_index("ix_github_repositories_project_id", table_name="github_repositories")
    op.drop_index("ix_github_repositories_installation_id", table_name="github_repositories")
    op.drop_table("github_repositories")

    op.drop_constraint("uq_github_installations_installation_id", "github_installations", type_="unique")
    op.drop_index("ix_github_installations_integration_id", table_name="github_installations")
    op.drop_table("github_installations")
