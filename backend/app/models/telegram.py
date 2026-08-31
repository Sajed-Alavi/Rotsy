"""Telegram account linking.

A :class:`TelegramLink` is a one-to-one mapping between a Rotsy
:class:`~app.models.user.User` and a Telegram chat. Admin-managed only —
there is deliberately no self-service linking flow (no ``/link <code>``):
the admin obtains a person's chat id (the bot tells an unlinked chat its own
id when messaged) and pastes it into Settings -> Integrations -> Telegram.

Once linked, the bot acts as that user for every purpose — it re-derives
their live RBAC (global permissions and per-project membership, see
:mod:`app.core.project_access`) on every request rather than caching
anything here. This table only answers "whose chat is this", nothing about
what they may do.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class TelegramLink(Base):
    __tablename__ = "telegram_links"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_telegram_links_user_id"),
        UniqueConstraint("chat_id", name="uq_telegram_links_chat_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Telegram chat ids can exceed 32-bit range for some account types.
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Display only (whatever Telegram reports at link time) — never used for
    # auth; the chat_id is the only identifier that matters.
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Admin username who created the link — an audit breadcrumb, not a
    # foreign key, so it survives that admin account later being removed.
    linked_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
