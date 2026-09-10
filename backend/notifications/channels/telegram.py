"""Delivery over the Telegram bot.

The only place that knows notifications reach Telegram as HTML, that its
recipients are chat ids, or that a document is sent differently from a
message. ``app.modules.telegram.client`` remains the protocol adapter — how to
talk to the Bot API — while this is the *notification channel* built on it.

Recipients are re-derived per delivery rather than cached: a deactivated user,
or one whose Project membership was removed, must stop receiving that
Project's notifications immediately, exactly as they would stop being able to
open it in the web app.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..message import Notification, Severity
from ..ports import (
    Settings,
    TelegramClient,
    TelegramError,
    TelegramLink,
    User,
    escape_html,
    get_settings,
    get_telegram_connection,
    linked_user,
    project_access,
    user_permissions,
)

logger = logging.getLogger(__name__)

# A fan-out runs inline inside whatever published the event — usually a
# background job — so it must not outlive it. Telegram being unreachable is a
# normal state on some networks (see Settings.TELEGRAM_PROXY_URL), and there
# every send burns its full httpx timeout: 20s for a message, 60s for a
# document. Serially that would add minutes to a job that has already finished
# its real work, holding a JobRunner slot the whole time.
_MAX_CONCURRENT_SENDS = 8
_FANOUT_TIMEOUT = 90.0

_SEVERITY_ICON = {
    Severity.INFO: "ℹ️",
    Severity.SUCCESS: "✅",
    Severity.WARNING: "⚠️",
    Severity.ERROR: "❌",
}


class TelegramChannel:
    name = "telegram"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def _get_settings(self) -> Settings:
        return self._settings or get_settings()

    async def _client(self, session: AsyncSession) -> TelegramClient | None:
        settings = self._get_settings()
        conn = await get_telegram_connection(session, settings)
        if not conn.is_configured():
            return None
        return TelegramClient(conn.token, proxy=settings.TELEGRAM_PROXY_URL)

    async def is_configured(self, session: AsyncSession) -> bool:
        return await self._client(session) is not None

    # -- rendering ----------------------------------------------------------

    def render(self, notification: Notification) -> str:
        """The notification as Telegram HTML. Every interpolated value is
        escaped here — Telegram rejects malformed tags outright (which reads
        to the recipient as the message simply never arriving) and renders
        live ``<a href>`` links, so an unescaped Project name is both a
        breakage and an injection vector."""
        icon = _SEVERITY_ICON.get(notification.severity, "")
        lines = [f"{icon} <b>{escape_html(notification.title)}</b>".strip()]
        if notification.body:
            lines.append(escape_html(notification.body))
        lines.extend(
            f"{escape_html(label)}: {escape_html(value)}" for label, value in notification.fields
        )
        return "\n".join(lines)

    # -- recipients ---------------------------------------------------------

    async def _wants(self, session: AsyncSession, user: User, notification: Notification) -> bool:
        audience = notification.audience
        if audience.admins:
            return "system:execute" in user_permissions(user)
        if audience.project_id is not None:
            return project_access.is_global_admin(user) or (
                await project_access.get_membership(session, audience.project_id, user.id) is not None
            )
        return False

    async def _recipients(self, session: AsyncSession, notification: Notification) -> list[int]:
        chat_ids: list[int] = []
        for link in (await session.execute(select(TelegramLink))).scalars().all():
            user = await linked_user(session, link.chat_id)
            if user is None:
                continue
            if await self._wants(session, user, notification):
                chat_ids.append(link.chat_id)
        return chat_ids

    # -- delivery -----------------------------------------------------------

    async def deliver(
        self, session: AsyncSession, notification: Notification, attachment: bytes | None = None,
    ) -> int:
        client = await self._client(session)
        if client is None:
            return 0

        chat_ids = await self._recipients(session, notification)
        if not chat_ids:
            return 0

        text = self.render(notification)
        filename = notification.attachment.filename if notification.attachment else "report.pdf"
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SENDS)

        async def _send(chat_id: int) -> bool:
            async with semaphore:
                try:
                    if attachment is None:
                        await client.send_message(chat_id, text)
                    else:
                        await client.send_document(chat_id, attachment, filename, caption=text)
                    return True
                except TelegramError:
                    logger.warning("Telegram delivery failed for chat %s", chat_id, exc_info=True)
                    return False

        try:
            async with asyncio.timeout(_FANOUT_TIMEOUT):
                results = await asyncio.gather(*(_send(chat_id) for chat_id in chat_ids))
            return sum(1 for ok in results if ok)
        except TimeoutError:
            logger.warning(
                "Telegram fan-out to %d recipient(s) timed out after %.0fs",
                len(chat_ids), _FANOUT_TIMEOUT,
            )
            return 0
