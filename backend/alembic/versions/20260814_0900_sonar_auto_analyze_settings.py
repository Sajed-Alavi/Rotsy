"""sonar: per-repository auto-analyze enable/disable + watched branches

Revision ID: 20260814_0900
Revises: 20260813_0900
Create Date: 2026-08-14 09:00:00.000000

Push-triggered analysis previously had no per-repository control: any repo
with a GitHub App installation (or a registered GitLab webhook) analyzed
every push, on every branch, with no way to opt out or restrict which
branches mattered. Adds ``auto_analyze_enabled`` (default true, so existing
behavior is unchanged for anyone who never touches the new setting) and
``auto_analyze_branches`` (default empty, meaning "default branch only" —
the safe default, since any other branch needs SonarQube Developer Edition+).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0900"
down_revision: Union[str, None] = "20260813_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sonar_projects",
        sa.Column("auto_analyze_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "sonar_projects",
        sa.Column("auto_analyze_branches", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("sonar_projects", "auto_analyze_branches")
    op.drop_column("sonar_projects", "auto_analyze_enabled")
