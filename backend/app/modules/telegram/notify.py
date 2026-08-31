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

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings, get_settings
from ...core import project_access
from ...core.config_store import get_telegram_connection
from ...db.session import get_session_factory
from ...dependencies import user_permissions
from ...models import TelegramLink, User
from .auth import linked_user
from .client import TelegramClient, TelegramError

logger = logging.getLogger(__name__)

# A fan-out runs inline inside a background job handler, so it must never
# outlive the job that triggered it. Telegram being unreachable is the
# normal state this feature was built around (see TELEGRAM_PROXY_URL), and
# in that state every individual send burns its full httpx timeout — 20s for
# a message, 60s for a document. An unbounded serial loop would therefore add
# N * 60s to a job that has already finished its real work, holding a
# JobRunner slot the whole time. Sends run concurrently, capped, under one
# overall deadline instead.
_MAX_CONCURRENT_SENDS = 8
_FANOUT_TIMEOUT = 90.0


async def _client_or_none(session: AsyncSession, settings: Settings) -> TelegramClient | None:
    conn = await get_telegram_connection(session, settings)
    if not conn.is_configured():
        return None
    return TelegramClient(conn.token, proxy=settings.TELEGRAM_PROXY_URL)


async def _recipient_chat_ids(
    session: AsyncSession, wants: Callable[[User], Awaitable[bool]],
) -> list[int]:
    """Chat ids of every linked, *active* user ``wants`` accepts.

    Resolved up front, before anything expensive is built or sent, so a
    caller can skip work entirely when nobody is listening.
    """
    chat_ids: list[int] = []
    for link in (await session.execute(select(TelegramLink))).scalars().all():
        user = await linked_user(session, link.chat_id)
        if user is None:
            continue
        if await wants(user):
            chat_ids.append(link.chat_id)
    return chat_ids


async def _fan_out(chat_ids: list[int], send: Callable[[int], Awaitable[object]]) -> None:
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SENDS)

    async def _one(chat_id: int) -> None:
        async with semaphore:
            try:
                await send(chat_id)
            except TelegramError:
                logger.warning("Telegram notify failed for chat %s", chat_id, exc_info=True)

    try:
        async with asyncio.timeout(_FANOUT_TIMEOUT):
            await asyncio.gather(*(_one(chat_id) for chat_id in chat_ids))
    except TimeoutError:
        logger.warning(
            "Telegram notification fan-out to %d recipient(s) timed out after %.0fs",
            len(chat_ids), _FANOUT_TIMEOUT,
        )


async def notify_project(
    project_id: int, text: str, *,
    pdf_factory: Callable[[], Awaitable[bytes]] | None = None,
    filename: str | None = None,
) -> None:
    """Send ``text`` to every linked, active user with at least viewer access
    to ``project_id`` — the same audience that can see the project in the
    bot's own project list.

    ``pdf_factory`` is a callable, not the bytes themselves, so that building
    a report (an unbounded query over every issue and hotspot, plus a
    multi-page render) only happens once there is somebody configured to
    receive it — the common case is a deployment with no bot token at all.
    """
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        client = await _client_or_none(session, settings)
        if client is None:
            return

        async def _has_project_access(user: User) -> bool:
            return project_access.is_global_admin(user) or (
                await project_access.get_membership(session, project_id, user.id) is not None
            )

        chat_ids = await _recipient_chat_ids(session, _has_project_access)

    if not chat_ids:
        return

    pdf = await pdf_factory() if pdf_factory is not None else None
    if pdf is None:
        await _fan_out(chat_ids, lambda chat_id: client.send_message(chat_id, text))
        return
    await _fan_out(
        chat_ids,
        lambda chat_id: client.send_document(chat_id, pdf, filename or "report.pdf", caption=text),
    )


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

        async def _is_admin(user: User) -> bool:
            return "system:execute" in user_permissions(user)

        chat_ids = await _recipient_chat_ids(session, _is_admin)

    if not chat_ids:
        return
    await _fan_out(chat_ids, lambda chat_id: client.send_message(chat_id, text))
