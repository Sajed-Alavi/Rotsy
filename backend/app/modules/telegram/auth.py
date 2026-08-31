"""Resolve a Telegram chat to a live, permission-loaded Rotsy user.

No request goes through FastAPI's dependency chain here (the poll loop is
not a request), so this does by hand exactly what
:func:`app.dependencies.get_current_user` does for every other caller:
loads the user, re-checks ``is_active``, and attaches effective permissions
so :func:`app.dependencies.user_permissions` reads correctly downstream —
skipping this would make every ``projects:write`` check silently wrong.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import _load_user_permissions
from ...models import TelegramLink, User


async def linked_user(session: AsyncSession, chat_id: int) -> User | None:
    """The active, permission-loaded :class:`User` for ``chat_id``, or
    ``None`` if the chat isn't linked or the linked account is deactivated —
    a deactivated Rotsy user must lose bot access on their very next tap,
    same as they'd lose web access on their next request."""
    link = await session.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
    if link is None:
        return None
    user = await session.get(User, link.user_id)
    if user is None or not user.is_active:
        return None
    user._effective_permissions = _load_user_permissions(session, user)  # type: ignore[attr-defined]
    return user
