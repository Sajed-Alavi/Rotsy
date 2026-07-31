"""role image scopes: wildcard image-level RBAC restriction

Revision ID: 20260731_1500
Revises: 20260731_1200
Create Date: 2026-07-31 15:00:00.000000

Adds ``role_image_scopes`` — restricts a role's access to per-image data
within one repository to names matching a shell-glob pattern. A role with no
rows for a repo stays unrestricted there (additive, backward-compatible).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_1500"
down_revision: Union[str, None] = "20260731_1200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "role_image_scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("pattern", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("role_id", "repo", "pattern", name="uq_role_image_scope"),
    )
    op.create_index("ix_role_image_scopes_role_id", "role_image_scopes", ["role_id"])
    op.create_index("ix_role_image_scopes_repo", "role_image_scopes", ["repo"])


def downgrade() -> None:
    op.drop_index("ix_role_image_scopes_repo", table_name="role_image_scopes")
    op.drop_index("ix_role_image_scopes_role_id", table_name="role_image_scopes")
    op.drop_table("role_image_scopes")
