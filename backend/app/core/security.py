"""Security primitives: password hashing and JWT issuance/verification.

JWTs carry the user id and a ``type`` claim (``access`` or ``refresh``) so an
access token cannot be replayed as a refresh token and vice versa. Tokens are
delivered exclusively via httpOnly cookies (see :mod:`app.routers.auth`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt
from jwt import PyJWTError

from ..config import Settings

# bcrypt only consumes the first 72 bytes of a password. passlib (removed here,
# see LOW-01) truncated silently; the bcrypt library raises instead. Truncate
# explicitly on both hash and verify so hashes created under passlib keep
# verifying and behaviour is identical either side of the migration.
_BCRYPT_MAX_BYTES = 72

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired, or of the wrong type."""


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def _encode(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt, returning the standard ``$2b$`` string."""
    return bcrypt.hashpw(_encode(plain), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a password against a stored bcrypt hash.

    Returns ``False`` rather than raising on a malformed or non-bcrypt hash, so
    a corrupt row can't turn a failed login into a 500.
    """
    try:
        return bcrypt.checkpw(_encode(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


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
