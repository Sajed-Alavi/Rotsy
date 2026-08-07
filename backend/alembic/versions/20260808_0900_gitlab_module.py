"""gitlab module: connections, repositories

Revision ID: 20260808_0900
Revises: 20260807_0900
Create Date: 2026-08-08 09:00:00.000000

Owned by ``app/modules/gitlab``. Mirrors the GitHub module's shape
(``gitlab_repositories.project_id`` is the only cross-module reference, into
the core ``projects`` table) with one difference: every repository row
carries its own encrypted token, since GitLab connections can be either
user-level (one PAT, many repos discovered under it) or repository-level
(one PAT per repo, no shared connection at all) — see models/gitlab.py.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0900"
down_revision: Union[str, None] = "20260807_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gitlab_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gitlab_url", sa.String(length=512), nullable=False),
        sa.Column("account_username", sa.String(length=255), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "gitlab_repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("gitlab_connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("gitlab_url", sa.String(length=512), nullable=False),
        sa.Column("gitlab_project_id", sa.BigInteger(), nullable=False),
        sa.Column("full_path", sa.String(length=255), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("webhook_id", sa.BigInteger(), nullable=True),
        sa.Column("webhook_secret", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_gitlab_repositories_connection_id", "gitlab_repositories", ["connection_id"])
    op.create_index("ix_gitlab_repositories_project_id", "gitlab_repositories", ["project_id"])
    op.create_unique_constraint("uq_gitlab_repositories_full_path", "gitlab_repositories", ["full_path"])


def downgrade() -> None:
    op.drop_constraint("uq_gitlab_repositories_full_path", "gitlab_repositories", type_="unique")
    op.drop_index("ix_gitlab_repositories_project_id", table_name="gitlab_repositories")
    op.drop_index("ix_gitlab_repositories_connection_id", table_name="gitlab_repositories")
    op.drop_table("gitlab_repositories")
    op.drop_table("gitlab_connections")
