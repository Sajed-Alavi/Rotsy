"""github: allow installation-less (public, connect-by-URL) repositories

Revision ID: 20260809_0900
Revises: 20260808_0900
Create Date: 2026-08-09 09:00:00.000000

Makes ``github_repositories.installation_id`` nullable. NULL means the
repository was connected by URL (public, no GitHub App installation) rather
than discovered through an installation — see
``routers/github.py:connect_public_repository``. A repository connected this
way only supports manual analysis: GitHub has no installation to deliver
push webhooks through for a repo Rotsy isn't actually installed on.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260809_0900"
down_revision: Union[str, None] = "20260808_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("github_repositories", "installation_id", nullable=True)


def downgrade() -> None:
    op.alter_column("github_repositories", "installation_id", nullable=False)
