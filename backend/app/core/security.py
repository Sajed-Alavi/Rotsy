"""Security primitives: password hashing and JWT issuance/verification.

JWTs carry the user id and a ``type`` claim (``access`` or ``refresh``) so an
access token cannot be replayed as a refresh token and vice versa. Tokens are
delivered exclusively via httpOnly cookies (see :mod:`app.routers.auth`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from jwt import PyJWTError
from passlib.context import CryptContext

from ..config import Settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired, or of the wrong type."""


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def _create_token(
    settings: Settings,
    subject: str | int,
    token_type: TokenType,
    ttl_seconds: int,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(settings: Settings, user_id: int) -> str:
    return _create_token(
        settings, user_id, "access", settings.JWT_ACCESS_TTL_SECONDS
    )


def create_refresh_token(settings: Settings, user_id: int) -> str:
    return _create_token(
        settings, user_id, "refresh", settings.JWT_REFRESH_TTL_SECONDS
    )


def decode_token(settings: Settings, token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a JWT, enforcing its ``type`` claim.

    Raises :class:`TokenError` on any failure so callers can map to 401.
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"Expected {expected_type} token, got {payload.get('type')!r}")
    return payload
