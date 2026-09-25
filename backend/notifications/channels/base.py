"""The contract every delivery channel implements.

A channel is responsible for three things and nothing else: saying whether it
is configured, working out who its recipients are for a given
:class:`~notifications.message.Audience`, and rendering + delivering the
notification in whatever form it speaks.

It is explicitly *not* responsible for deciding whether a notification should
be sent, or for its wording. Those belong to the producer and to
``notifications.subscribers``, so behaviour stays the same regardless of which
channels happen to be enabled.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from ..message import Notification


@runtime_checkable
class NotificationChannel(Protocol):
    """Implementations live in this package, one module per channel."""

    #: Stable identifier, used in logs and to replace a channel on re-register.
    name: str

    async def is_configured(self, session: AsyncSession) -> bool:
        """Whether this channel can deliver anything at all right now.

        Checked before a notification is rendered, so an unconfigured channel
        costs nothing — in particular it is what stops an attachment from
        being produced for nobody.
        """
        ...

    async def deliver(
        self, session: AsyncSession, notification: Notification, attachment: bytes | None = None,
    ) -> int:
        """Deliver to every recipient this channel resolves for the
        notification's audience. Returns how many it reached.

        ``attachment`` is the already-rendered payload, or ``None`` — rendered
        once by the dispatcher and shared across channels, rather than each
        channel producing its own copy of the same report.

        Must not raise for an ordinary delivery failure — an unreachable
        provider, a blocked bot, one bad address. Those are logged and counted
        as not-delivered. Raising is reserved for a programming error.
        """
        ...
