"""API tokens for CI/CD and other non-interactive callers.

The dashboard authenticates with httpOnly cookies, which a pipeline cannot use.
Without this a CI job had to either embed a human's password or run with the
service account — so tokens exist to make the narrow, revocable option the easy
one.

**The token itself is never stored.** Only a SHA-256 hash is, and the plaintext
is shown exactly once at creation. SHA-256 rather than bcrypt is deliberate and
is the opposite of the right choice for passwords: a token is 32 bytes of
CSPRNG output, so there is no dictionary to attack and no work factor needed,
and it has to be looked up by an indexed equality match on every single API
request. bcrypt cannot be indexed and would put ~100ms on every call.

``prefix`` is the first few characters of the plaintext, stored in the clear so
the UI can show "which token is this" in a list. It is not a secret and is not
sufficient to authenticate.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class AccessToken(Base):
    __tablename__ = "access_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Indexed + unique: this is the lookup key on every token-authenticated request.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Display-only fragment, e.g. "shp_a1b2c3". Never enough to authenticate.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # Comma-separated permission keys. Empty means "inherit the owner's
    # permissions" — a token can narrow what its owner can do, never widen it.
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
