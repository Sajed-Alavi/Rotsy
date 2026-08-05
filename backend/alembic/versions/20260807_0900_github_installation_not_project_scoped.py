"""github: installations are not project-scoped

Revision ID: 20260807_0900
Revises: 20260806_0900
Create Date: 2026-08-07 09:00:00.000000

Drops ``github_installations.integration_id``. It was wrong from the start:
GitHub's App-install redirect only ever carries ``installation_id``, never a
Project, so nothing could actually populate a per-project integration at
install time. One installation's repositories can end up mapped to several
different Projects, so a per-project ``github`` Integration row is now
created lazily when a repository is actually mapped to a Project instead.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0900"
down_revision: Union[str, None] = "20260806_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_github_installations_integration_id", table_name="github_installations")
    op.drop_column("github_installations", "integration_id")


def downgrade() -> None:
    op.add_column(
        "github_installations",
        sa.Column("integration_id", sa.Integer(), sa.ForeignKey("integrations.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_github_installations_integration_id", "github_installations", ["integration_id"])
