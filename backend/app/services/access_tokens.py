"""Issue, verify and revoke API tokens.

Design notes that matter for security review:

* **Generation** — ``secrets.token_urlsafe(32)``, i.e. 256 bits from the OS
  CSPRNG. Prefixed ``shp_`` so a leaked token is greppable in logs and
  recognisable in a secret scanner.
* **Storage** — SHA-256 of the plaintext, nothing else. See the model docstring
  for why a fast hash is correct here and wrong for passwords.
* **Comparison** — the lookup is an indexed equality match on the hash, so no
  timing-safe compare is needed on the hash itself. The token is never compared
  in Python.
* **Scopes narrow, never widen.** A token's effective permissions are the
  intersection of its scopes with its owner's current permissions, resolved at
  request time. Revoking a role therefore immediately shrinks every token that
  user issued — a token cannot outlive the authority it was minted from.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AccessToken, User

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "shp_"
_PREFIX_DISPLAY_CHARS = 12


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Return ``(plaintext, hash, display_prefix)``."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plaintext, hash_token(plaintext), plaintext[:_PREFIX_DISPLAY_CHARS]


async def create_token(
    session: AsyncSession,
    *,
    name: str,
    owner_id: int,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[AccessToken, str]:
    """Mint a token. The plaintext is returned once and never recoverable after."""
    plaintext, digest, prefix = generate_token()
    token = AccessToken(
        name=name,
        token_hash=digest,
        prefix=prefix,
        scopes=",".join(scopes or []),
        owner_id=owner_id,
        expires_at=expires_at,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return token, plaintext


async def resolve_token(session: AsyncSession, plaintext: str) -> tuple[User, list[str]] | None:
    """Validate a presented token and return ``(user, effective_permissions)``.

    Returns ``None`` for anything not currently usable — unknown, revoked,
    expired, or owned by a deactivated user — deliberately without saying which,
    since the caller turns all of them into the same 401.
    """
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None

    row = (
        await session.execute(
            select(AccessToken).where(AccessToken.token_hash == hash_token(plaintext))
        )
    ).scalar_one_or_none()
    if row is None or row.revoked:
        return None

    now = datetime.now(timezone.utc)
    if row.expires_at is not None:
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if expires <= now:
            return None

    user = (
        await session.execute(select(User).where(User.id == row.owner_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    owner_perms = {p.key for role in user.roles for p in role.permissions}
    requested = {s for s in (row.scopes or "").split(",") if s}
    # Intersection, never union: a token is a subset of its owner's authority.
    effective = sorted(owner_perms & requested) if requested else sorted(owner_perms)

    row.last_used_at = now
    await session.commit()

    return user, effective


async def list_tokens(session: AsyncSession, owner_id: int | None = None) -> list[AccessToken]:
    stmt = select(AccessToken).order_by(AccessToken.created_at.desc())
    if owner_id is not None:
        stmt = stmt.where(AccessToken.owner_id == owner_id)
    return list((await session.execute(stmt)).scalars().all())


async def revoke_token(session: AsyncSession, token_id: int) -> bool:
    """Revoke immediately. Rows are kept so the audit trail survives."""
    row = (
        await session.execute(select(AccessToken).where(AccessToken.id == token_id))
    ).scalar_one_or_none()
    if row is None or row.revoked:
        return False
    row.revoked = True
    row.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info("Revoked access token %s (%s)", row.id, row.name)
    return True
