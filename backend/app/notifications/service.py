"""Dispatching one notification across every configured channel.

The ordering here is deliberate and is the reason this layer exists rather
than each producer calling a channel directly: channels are asked whether
they are *configured* before anything is rendered, and the attachment is only
built if at least one configured channel could carry it. A deployment with no
notification channel set up — the common case — therefore pays nothing for a
notification beyond constructing a small dataclass.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session_factory
from .channels.base import NotificationChannel
from .message import Notification

logger = logging.getLogger(__name__)

_channels: list[NotificationChannel] = []


def register(channel: NotificationChannel) -> None:
    """Add a delivery channel. Registering the same channel name twice
    replaces the earlier one, so re-importing a wiring module cannot cause
    double delivery."""
    global _channels
    _channels = [c for c in _channels if c.name != channel.name] + [channel]


def registered() -> list[str]:
    return [c.name for c in _channels]


def clear() -> None:
    """Drop every channel. For tests."""
    _channels.clear()


async def dispatch(notification: Notification, session: AsyncSession | None = None) -> int:
    """Deliver ``notification`` on every configured channel; return the total
    number of recipients reached.

    Never raises. A notification is always secondary to whatever produced it,
    so a delivery problem is logged and swallowed rather than allowed to fail
    a job that has already done its real work.
    """
    if session is not None:
        return await _dispatch_with_session(session, notification)
    factory = get_session_factory()
    async with factory() as own_session:
        return await _dispatch_with_session(own_session, notification)


async def _dispatch_with_session(session: AsyncSession, notification: Notification) -> int:
    delivered = 0
    for channel in list(_channels):
        try:
            if not await channel.is_configured(session):
                continue
            delivered += await channel.deliver(session, notification)
        except Exception:  # noqa: BLE001 - a broken channel must not take down the others, or the caller
            logger.exception("Notification channel %r failed", channel.name)
    return delivered
