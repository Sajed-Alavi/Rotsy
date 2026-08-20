"""core: telegram links

Revision ID: 20260820_0900
Revises: 20260817_0900
Create Date: 2026-08-20 09:00:00.000000

Admin-managed mapping between a Rotsy user and a Telegram chat, powering the
new Telegram bot integration (view/manage Project membership, trigger
analysis) — see app/modules/telegram/. No self-service linking flow; rows
here are only ever created by an admin from Settings -> Integrations.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0900"
down_revision: Union[str, None] = "20260817_0900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("linked_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_links_user_id", "telegram_links", ["user_id"])
    op.create_unique_constraint("uq_telegram_links_user_id", "telegram_links", ["user_id"])
    op.create_unique_constraint("uq_telegram_links_chat_id", "telegram_links", ["chat_id"])


def downgrade() -> None:
    op.drop_constraint("uq_telegram_links_chat_id", "telegram_links", type_="unique")
    op.drop_constraint("uq_telegram_links_user_id", "telegram_links", type_="unique")
    op.drop_index("ix_telegram_links_user_id", table_name="telegram_links")
    op.drop_table("telegram_links")
