"""access tokens for CI/CD

Revision ID: 20260801_1000
Revises: 20260731_1700
Create Date: 2026-08-01 10:00:00.000000

Adds ``access_tokens``. The dashboard authenticates with httpOnly cookies,
which a pipeline cannot present, so a CI job previously had to reuse a human's
password or the service account. This table backs narrow, expiring, revocable
tokens instead.

Only the SHA-256 hash of a token is stored; the plaintext is shown once at
creation and never again. ``token_hash`` is unique and indexed because it is
the equality lookup performed on every token-authenticated request.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_1000"
down_revision: Union[str, None] = "20260731_1700"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_access_tokens_token_hash", "access_tokens", ["token_hash"], unique=True)
    op.create_index("ix_access_tokens_owner_id", "access_tokens", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_access_tokens_owner_id", table_name="access_tokens")
    op.drop_index("ix_access_tokens_token_hash", table_name="access_tokens")
    op.drop_table("access_tokens")
