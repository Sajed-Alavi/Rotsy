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
from ..core import rate_limit
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
from ..state import AppState, app_state

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
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[AppState, Depends(app_state)],
) -> MeResponse:
    """Authenticate, set access + refresh cookies, return the user profile.

    Two guards run before the password is ever hashed. A per-source throttle
    counts every attempt, so one host cycling through usernames is bounded.
    A per-account lockout counts consecutive *failures*: five wrong passwords
    in a row lock that username for 60 seconds from the fifth, and during the
    lock even the correct password is refused — otherwise a guess that
    happened to be right would still get through. See core/rate_limit.py.
    """
    ip = rate_limit.client_ip(request)
    await rate_limit.check(state.cache, "login-ip", ip, rate_limit.LOGIN_PER_IP)
    await rate_limit.assert_not_locked(state.cache, body.username)

    user = await session.scalar(select(User).where(User.username == body.username))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        locked_for = await rate_limit.record_login_failure(state.cache, body.username)
        if locked_for:
            # Tell them on the attempt that caused it, rather than letting the
            # sixth try be the first sign anything changed.
            raise rate_limit.lockout_error(locked_for)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")

    # Succeeded — the failures were not consecutive after all, and the source
    # throttle should not keep counting a user who got it right.
    await rate_limit.clear_login_failures(state.cache, body.username)
    await rate_limit.reset(state.cache, "login-ip", ip, rate_limit.LOGIN_PER_IP)

    access = create_access_token(settings, user.id)
    refresh = create_refresh_token(settings, user.id)

    response.set_cookie(value=access, **settings.access_cookie)
    response.set_cookie(value=refresh, **settings.refresh_cookie)

    perms = _load_user_permissions(session, user)
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

    perms = _load_user_permissions(session, user)
    return _build_me(user, perms)


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    """Return the current user with roles + effective permissions."""
    return _build_me(user, getattr(user, "_effective_permissions", []))
