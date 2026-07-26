"""Encrypted key/value store for dashboard-managed settings.

The Nexus connection (URL, username, password) is editable from the dashboard
and stored in the ``system_config`` table. The password is encrypted at rest
using Fernet symmetric encryption. The encryption key comes from
``NEXUS_CONFIG_ENCRYPTION_KEY`` (env) or is derived from ``JWT_SECRET`` so the
app always boots.

Resolution order for the Nexus connection at runtime:
  1. dashboard value (DB) if present
  2. env (``NEXUS_*``) as bootstrap default
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import SystemConfig

logger = logging.getLogger(__name__)

NEXUS_CONFIG_KEY = "nexus_connection"


@dataclass
class NexusConnection:
    """Resolved Nexus connection parameters."""

    url: str
    username: str
    password: str
    verify_ssl: bool = True

    def is_configured(self) -> bool:
        return bool(self.url and self.username and self.password)


def _fernet(settings: Settings) -> Fernet:
    """Build a Fernet cipher. Key derived from the configured secret so we
    don't need a second mandatory env var."""
    seed = settings.NEXUS_CONFIG_ENCRYPTION_KEY or settings.JWT_SECRET
    # Fernet needs 32 url-safe base64 bytes; derive deterministically.
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
    return Fernet(key)


def encrypt_password(plain: str, settings: Settings) -> str:
    if not plain:
        return ""
    return _fernet(settings).encrypt(plain.encode()).decode()


def decrypt_password(token: str, settings: Settings) -> str:
    if not token:
        return ""
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken:
        logger.warning("Failed to decrypt stored Nexus password — returning empty.")
        return ""


# ---------------------------------------------------------------------------
# DB read / write
# ---------------------------------------------------------------------------
async def get_nexus_connection(session: AsyncSession, settings: Settings) -> NexusConnection:
    """Return the effective Nexus connection: dashboard value if present,
    otherwise the env/bootstrap default."""
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == NEXUS_CONFIG_KEY))
    if row is not None:
        try:
            data = json.loads(row.value_json)
            return NexusConnection(
                url=data.get("url", ""),
                username=data.get("username", ""),
                password=decrypt_password(data.get("password_enc", ""), settings),
                verify_ssl=bool(data.get("verify_ssl", True)),
            )
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupt nexus_connection config row — falling back to env.")

    # Fallback: env-provided defaults.
    return NexusConnection(
        url=settings.NEXUS_URL,
        username=settings.NEXUS_USERNAME,
        password=settings.NEXUS_PASSWORD,
        verify_ssl=settings.NEXUS_VERIFY_SSL,
    )


async def save_nexus_connection(
    session: AsyncSession,
    settings: Settings,
    url: str,
    username: str,
    password: str,
    verify_ssl: bool,
) -> NexusConnection:
    """Persist the Nexus connection. ``password`` is stored encrypted."""
    blob = json.dumps({
        "url": url.rstrip("/"),
        "username": username,
        "password_enc": encrypt_password(password, settings),
        "verify_ssl": verify_ssl,
    })
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == NEXUS_CONFIG_KEY))
    if row is None:
        row = SystemConfig(key=NEXUS_CONFIG_KEY, value_json=blob)
        session.add(row)
    else:
        row.value_json = blob
    await session.commit()
    logger.info("Nexus connection updated via dashboard.")
    return NexusConnection(url=url.rstrip("/"), username=username, password=password, verify_ssl=verify_ssl)


async def nexus_connection_masked(session: AsyncSession) -> dict:
    """Return the connection with the password masked, for the GET endpoint."""
    # NOTE: this intentionally does NOT decrypt — it just shows that a
    # password is set. Re-resolving requires settings for decryption; callers
    # that need the real password use get_nexus_connection.
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == NEXUS_CONFIG_KEY))
    if row is None:
        return {"configured": False}
    try:
        data = json.loads(row.value_json)
        return {
            "configured": True,
            "url": data.get("url", ""),
            "username": data.get("username", ""),
            "password_set": bool(data.get("password_enc")),
            "verify_ssl": bool(data.get("verify_ssl", True)),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    except (json.JSONDecodeError, TypeError):
        return {"configured": False}
