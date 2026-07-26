"""Authentication endpoints.

Token model: short-lived **access** JWT + long-lived **refresh** JWT, both
delivered exclusively in httpOnly cookies (path-scoped to ``/api/auth``).
The frontend never sees the token bytes; it just sends ``credentials: include``
and reads ``GET /auth/me`` to know who is logged in.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from ..dependencies import _load_user_permissions, get_current_user, get_session
from ..models import User
from ..schemas.auth import LoginRequest, MeResponse, RoleBrief

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_me(user: User, permissions: list[str]) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        roles=[RoleBrief(id=r.id, name=r.name, is_system=r.is_system) for r in user.roles],
        permissions=permissions,
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """Authenticate, set access + refresh cookies, return the user profile."""
    user = await session.scalar(select(User).where(User.username == body.username))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")

    access = create_access_token(settings, user.id)
    refresh = create_refresh_token(settings, user.id)

    response.set_cookie(value=access, **settings.access_cookie)
    response.set_cookie(value=refresh, **settings.refresh_cookie)

    perms = await _load_user_permissions(session, user)
    return _build_me(user, perms)


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    """Clear both auth cookies."""
    response.delete_cookie("access_token", path="/api")
    response.delete_cookie("refresh_token", path="/api")
    return {"ok": True}


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """Exchange a valid refresh cookie for a fresh access cookie.

    Re-issues the refresh token too (rolling refresh) to limit replay window.
    """
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing refresh token.")

    try:
        payload = decode_token(settings, refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid refresh token: {exc}") from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token subject.") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive.")

    # Idle-timeout: if the user has been inactive longer than the configured
    # window, refuse to refresh — they must sign in again.
    from datetime import datetime, timedelta, timezone
    if user.last_seen_at is not None:
        idle = datetime.now(timezone.utc) - user.last_seen_at
        if idle > timedelta(seconds=settings.SESSION_IDLE_TIMEOUT_SECONDS):
            # Clear cookies so the browser drops the session immediately.
            response.delete_cookie("access_token", path="/api")
            response.delete_cookie("refresh_token", path="/api")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired due to inactivity.")

    response.set_cookie(value=create_access_token(settings, user.id), **settings.access_cookie)
    response.set_cookie(value=create_refresh_token(settings, user.id), **settings.refresh_cookie)

    perms = await _load_user_permissions(session, user)
    return _build_me(user, perms)


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    """Return the current user with roles + effective permissions."""
    return _build_me(user, getattr(user, "_effective_permissions", []))
