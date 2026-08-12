"""sonar: persist each repository's quality gate preset

Revision ID: 20260817_0900
Revises: 20260816_0900
Create Date: 2026-08-17 09:00:00.000000

The Repositories tab lets an operator switch a connected repository onto a
different quality-gate preset (Strict/Standard/Relaxed/Bugs-only), but the
choice was only ever applied to SonarQube itself, never stored locally — so
the picker always reopened showing "Standard" regardless of what was
actually assigned, and re-saving it silently reverted a Strict/Relaxed repo
back to Standard. Backfills existing rows to "standard", matching what
``ensure_quality_gate``'s default already assigns a repository with no
explicit choice.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0900"
down_revision: Union[str, None] = "20260816_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sonar_projects",
        sa.Column("quality_gate_preset", sa.String(length=16), nullable=False, server_default="standard"),
    )


def downgrade() -> None:
    op.drop_column("sonar_projects", "quality_gate_preset")
