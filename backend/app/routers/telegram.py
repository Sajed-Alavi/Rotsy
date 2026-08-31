"""Telegram bot admin endpoints: bot token config + who's linked.

Everything here is gated on ``system:execute``, the same permission the rest
of Settings -> Integrations already requires (no new permission key) — this
router only manages *account linking*, an admin action; the bot's own
runtime behavior (what a linked user can see/do) is entirely re-derived from
that user's live RBAC by ``modules/telegram/dispatcher.py``, not decided
here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core.config_store import get_telegram_connection, save_telegram_connection, telegram_connection_masked
from ..dependencies import RequirePermission, get_current_user, get_session, get_settings
from ..models import TelegramLink, User
from ..modules.telegram.client import TelegramClient, TelegramError

router = APIRouter(prefix="/telegram", tags=["telegram"])

_LINK_NOT_FOUND = "Link not found"


class TelegramConfigUpdate(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)


@router.put("/config", dependencies=[Depends(RequirePermission("system:execute"))])
async def update_config(
    body: TelegramConfigUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    await save_telegram_connection(session, settings, body.token)
    return await telegram_connection_masked(session, settings)


@router.post("/config/test", dependencies=[Depends(RequirePermission("system:execute"))])
async def test_config(
    body: TelegramConfigUpdate,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Try the given token without saving — same shape as the Sonar/Nexus
    test-connection endpoints."""
    try:
        client = TelegramClient(body.token, proxy=settings.TELEGRAM_PROXY_URL)
        me = await client.get_me()
    except TelegramError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "bot_username": me.get("username")}


@router.get("/status", dependencies=[Depends(RequirePermission("system:execute"))])
async def get_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Connection status + bot identity + how many users are linked, for the
    Settings card. A live ``getMe`` call, not just whether a token is saved
    — a saved-but-revoked token should show as broken, not "configured"."""
    cfg = await get_telegram_connection(session, settings)
    link_count = len((await session.execute(select(TelegramLink))).scalars().all())
    if not cfg.is_configured():
        return {"configured": False, "bot_username": None, "link_count": link_count}
    try:
        client = TelegramClient(cfg.token, proxy=settings.TELEGRAM_PROXY_URL)
        me = await client.get_me()
        bot_username = me.get("username")
    except TelegramError:
        bot_username = None
    return {"configured": True, "bot_username": bot_username, "link_count": link_count}


@router.get("/links", dependencies=[Depends(RequirePermission("system:execute"))])
async def list_links(session: Annotated[AsyncSession, Depends(get_session)]) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(TelegramLink, User)
            .join(User, User.id == TelegramLink.user_id)
            .order_by(User.username)
        )
    ).all()
    return [
        {
            "id": link.id,
            "user_id": link.user_id,
            "username": user.username,
            "chat_id": link.chat_id,
            "telegram_username": link.telegram_username,
            "linked_by": link.linked_by,
            "created_at": link.created_at,
        }
        for link, user in rows
    ]


class LinkCreate(BaseModel):
    user_id: int
    chat_id: int
    telegram_username: str | None = Field(default=None, max_length=64)

    @field_validator("chat_id")
    @classmethod
    def _chat_id_must_be_private(cls, value: int) -> int:
        """Telegram private-chat ids are always positive (they equal the
        user's own Telegram user id); group/supergroup/channel ids are
        always negative. Rejecting non-positive ids here guarantees every
        stored chat_id names exactly one person, which is what
        TelegramLink's whole security model rests on — see
        models/telegram.py. Without this, an admin who pastes a group's
        chat id (easy to end up with: it's what /start replies with in a
        group too) would let every member of that group act as whichever
        Rotsy account gets linked to it."""
        if value <= 0:
            raise ValueError("Telegram chat ID must be a private chat ID (a positive number), not a group or channel.")
        return value


@router.post("/links", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("system:execute"))])
async def create_link(
    body: LinkCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    admin: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    target = await session.get(User, body.user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    existing_user = await session.scalar(select(TelegramLink).where(TelegramLink.user_id == body.user_id))
    if existing_user is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This user is already linked to a Telegram chat.")
    existing_chat = await session.scalar(select(TelegramLink).where(TelegramLink.chat_id == body.chat_id))
    if existing_chat is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This chat ID is already linked to another user.")

    link = TelegramLink(
        user_id=body.user_id, chat_id=body.chat_id,
        telegram_username=body.telegram_username, linked_by=admin.username,
    )
    session.add(link)
    try:
        await session.commit()
    except IntegrityError:
        # The two pre-checks above are separate statements from this INSERT,
        # so two concurrent submissions for the same user/chat can both pass
        # them; the unique constraints are the real guard, this just turns
        # the resulting 500 back into the same 409 the pre-checks intend.
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This user or chat ID is already linked.") from None
    await session.refresh(link)
    return {
        "id": link.id, "user_id": link.user_id, "username": target.username,
        "chat_id": link.chat_id, "telegram_username": link.telegram_username,
        "linked_by": link.linked_by, "created_at": link.created_at,
    }


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("system:execute"))])
async def delete_link(link_id: int, session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    link = await session.get(TelegramLink, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _LINK_NOT_FOUND)
    await session.delete(link)
    await session.commit()


@router.get("/users", dependencies=[Depends(RequirePermission("system:execute"))])
async def search_users(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = None,
) -> list[dict[str, Any]]:
    """Active users not yet linked, for the "add link" picker — a lighter
    query than the full Users admin page, and reachable on ``system:execute``
    alone (the whole Integrations page's own gate) rather than also
    requiring ``users:manage``, same reasoning as
    ``core/projects.py::search_member_candidates``."""
    linked_user_ids = select(TelegramLink.user_id)
    stmt = select(User).where(User.is_active.is_(True), User.id.notin_(linked_user_ids))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.email.ilike(like)))
    stmt = stmt.order_by(User.username).limit(20)
    rows = (await session.execute(stmt)).scalars().all()
    return [{"id": u.id, "username": u.username, "email": u.email} for u in rows]
