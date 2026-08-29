"""Best-effort Telegram notifications for background-job outcomes.

Two audiences, mirroring the two roles the bot itself already distinguishes
(see ``dispatcher.py``): project members (analysis results — success ships
the PDF report, failure a short reason) and global admins holding
``system:execute`` (infra-level failures: backup, scanner DB update).

Every function here is silent by design — no Telegram configured, no chat
resolvable, or a send that fails all just log and move on. A background
job's own outcome must never depend on whether anyone happened to be
listening in Telegram, the same reasoning ``services/notifier.py`` documents
for webhook alerts.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from ...config import get_settings
from ...core import project_access
from ...core.config_store import get_telegram_connection
from ...db.session import get_session_factory
from ...dependencies import user_permissions
from ...models import TelegramLink
from .auth import linked_user
from .client import TelegramClient, TelegramError

logger = logging.getLogger(__name__)


async def _client_or_none(session, settings) -> TelegramClient | None:
    conn = await get_telegram_connection(session, settings)
    if not conn.is_configured():
        return None
    return TelegramClient(conn.token, proxy=settings.TELEGRAM_PROXY_URL)


async def notify_project(
    project_id: int, text: str, *, pdf: bytes | None = None, filename: str | None = None,
) -> None:
    """Send ``text`` (optionally with a PDF document attached as its
    caption) to every linked, active user with at least viewer access to
    ``project_id`` — the same audience that can see the project in the bot's
    own project list."""
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        client = await _client_or_none(session, settings)
        if client is None:
            return
        links = (await session.execute(select(TelegramLink))).scalars().all()
        for link in links:
            user = await linked_user(session, link.chat_id)
            if user is None:
                continue
            has_access = project_access.is_global_admin(user) or (
                await project_access.get_membership(session, project_id, user.id) is not None
            )
            if not has_access:
                continue
            try:
                if pdf is not None:
                    await client.send_document(link.chat_id, pdf, filename or "report.pdf", caption=text)
                else:
                    await client.send_message(link.chat_id, text)
            except TelegramError:
                logger.warning("Telegram project notify failed for chat %s", link.chat_id, exc_info=True)


async def notify_admins(text: str) -> None:
    """Send ``text`` to every linked, active user holding the global
    ``system:execute`` permission — the same gate the bot's own admin panel
    uses."""
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        client = await _client_or_none(session, settings)
        if client is None:
            return
        links = (await session.execute(select(TelegramLink))).scalars().all()
        for link in links:
            user = await linked_user(session, link.chat_id)
            if user is None or "system:execute" not in user_permissions(user):
                continue
            try:
                await client.send_message(link.chat_id, text)
            except TelegramError:
                logger.warning("Telegram admin notify failed for chat %s", link.chat_id, exc_info=True)
