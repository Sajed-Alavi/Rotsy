"""role image scope unrestricted flag

Revision ID: 20260731_1700
Revises: 20260731_1600
Create Date: 2026-07-31 17:00:00.000000

Adds ``roles.image_scope_unrestricted`` (default true). A role with no
``role_image_scopes`` rows for a repo has always been treated as unrestricted
there, and a user's effective access is the union across held roles — so any
second held role without scope rows silently reopened access an explicitly
scoped role was meant to restrict, with no way to prevent it. This column
lets an admin flip a specific role to ``false`` so it always defers to scope
rows instead of granting blanket access. Defaults to true (and every existing
row is backfilled to true) so current behavior is unchanged until an admin
opts a role out.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_1700"
down_revision: Union[str, None] = "20260731_1600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("image_scope_unrestricted", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # server_default backfills existing rows to true; drop it afterward so
    # future inserts must go through the ORM default like every other column.
    op.alter_column("roles", "image_scope_unrestricted", server_default=None)


def downgrade() -> None:
    op.drop_column("roles", "image_scope_unrestricted")
