"""Shared FastAPI dependencies.

- :func:`get_session` is re-exported here so routers import dependencies from
  a single place.
- :func:`get_current_user` reads the access JWT from the ``access_token``
  httpOnly cookie and loads the user + their effective permissions.
- :class:`RequirePermission` is a callable dependency factory: a router
  declares ``Depends(RequirePermission("storage:analyze"))`` and the request
  is rejected with 403 if the user lacks the permission.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .core.security import TokenError, decode_token
from .db.session import get_session
from .models import User

# Re-export for convenience.
__all__ = ["get_settings", "get_session", "get_current_user", "RequirePermission"]


async def _load_user_permissions(session: AsyncSession, user: User) -> list[str]:
    """Flatten + dedupe the permission keys across all of a user's roles.

    Roles and permissions are eager-loaded (``selectin``) on the :class:`User`,
    so this is computed in-memory without an extra query.
    """
    del session  # unused — kept in signature for symmetry with other deps
    keys: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            keys.add(perm.key)
    return sorted(keys)


async def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the authenticated user from the access-token cookie.

    Raises 401 if the cookie is missing/invalid or the user no longer exists.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")

    try:
        payload = decode_token(settings, token, expected_type="access")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token subject.") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive.")

    # Update last-seen timestamp for idle-timeout enforcement. Commit happens
    # via the session's request lifecycle; we keep this cheap by only writing
    # if the stored value is older than a minute (avoids a write per request).
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    if user.last_seen_at is None or (now - user.last_seen_at) > timedelta(minutes=1):
        user.last_seen_at = now
        await session.commit()

    # Stash effective permissions on the instance for downstream access.
    user._effective_permissions = await _load_user_permissions(session, user)  # type: ignore[attr-defined]
    return user


def user_permissions(user: User) -> list[str]:
    """Helper to read permissions attached by :func:`get_current_user`."""
    return getattr(user, "_effective_permissions", [])  # type: ignore[attr-defined]


class RequirePermission:
    """Dependency enforcing that the current user holds a permission key.

    Usage::

        @router.post("/analyze", dependencies=[Depends(RequirePermission("storage:analyze"))])
    """

    def __init__(self, *required: str) -> None:
        if not required:
            raise ValueError("RequirePermission needs at least one permission key.")
        self.required = set(required)

    async def __call__(self, user: Annotated[User, Depends(get_current_user)]) -> User:
        held = set(user_permissions(user))
        missing = self.required - held
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Missing permission(s): {', '.join(sorted(missing))}.",
            )
        return user
