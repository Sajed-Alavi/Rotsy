"""Encrypted key/value store for dashboard-managed settings.

The Nexus connection (URL, username, password) is editable from the dashboard
and stored in the ``system_config`` table. The password is encrypted at rest
using Fernet symmetric encryption. The encryption key is derived **only** from
``NEXUS_CONFIG_ENCRYPTION_KEY``, which :mod:`app.config` requires at startup
and forbids from equalling ``JWT_SECRET``.

This used to fall back to ``JWT_SECRET`` when the dedicated key was unset. That
made one secret do two jobs with different threat models — leaking the token
signing key also decrypted stored Nexus admin credentials from a DB backup. The
two are now separate and independently rotatable.

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
    """Resolved Nexus connection parameters.

    Intentionally has no Docker registry field: the scanners' registry endpoints
    are discovered per repository from Nexus itself (see
    :mod:`app.services.registry`), not configured.
    """

    url: str
    username: str
    password: str
    verify_ssl: bool = True

    def is_configured(self) -> bool:
        return bool(self.url and self.username and self.password)


def _fernet(settings: Settings) -> Fernet:
    """Build a Fernet cipher from the dedicated at-rest encryption key.

    No fallback: ``NEXUS_CONFIG_ENCRYPTION_KEY`` is a required setting and is
    validated at startup (see :meth:`app.config.Settings._reject_weak_encryption_key`).
    """
    seed = settings.NEXUS_CONFIG_ENCRYPTION_KEY
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
    return NexusConnection(
        url=url.rstrip("/"),
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )


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


# ---------------------------------------------------------------------------
# Nexus webhook secret (shared with the Nexus webhook capability)
# ---------------------------------------------------------------------------
WEBHOOK_SECRET_KEY = "nexus_webhook_secret"


async def get_or_create_webhook_secret(session: AsyncSession, settings: Settings) -> str:
    """Return the shared secret Nexus signs its webhook deliveries with.

    Generated on first use and persisted, so the operator has one value to copy
    into the Nexus webhook capability and never has to invent it. An env-provided
    ``NEXUS_WEBHOOK_SECRET`` seeds it on first call.
    """
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == WEBHOOK_SECRET_KEY))
    if row is not None:
        try:
            secret = json.loads(row.value_json).get("secret", "")
        except (json.JSONDecodeError, TypeError):
            secret = ""
        if secret:
            return secret

    import secrets as _secrets
    secret = (settings.NEXUS_WEBHOOK_SECRET or "").strip() or _secrets.token_hex(24)
    blob = json.dumps({"secret": secret})
    if row is None:
        session.add(SystemConfig(key=WEBHOOK_SECRET_KEY, value_json=blob))
    else:
        row.value_json = blob
    await session.commit()
    logger.info("Nexus webhook secret provisioned.")
    return secret


async def rotate_webhook_secret(session: AsyncSession) -> str:
    """Issue a new webhook secret. Nexus must be updated to match."""
    import secrets as _secrets
    secret = _secrets.token_hex(24)
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == WEBHOOK_SECRET_KEY))
    blob = json.dumps({"secret": secret})
    if row is None:
        session.add(SystemConfig(key=WEBHOOK_SECRET_KEY, value_json=blob))
    else:
        row.value_json = blob
    await session.commit()
    logger.warning("Nexus webhook secret rotated — update the Nexus webhook capability to match.")
    return secret


# ---------------------------------------------------------------------------
# SonarQube connection (dashboard-managed, same encrypted-at-rest pattern as
# the Nexus connection above — reuses encrypt_password/decrypt_password
# rather than a second cipher, since NEXUS_CONFIG_ENCRYPTION_KEY protects any
# dashboard-entered secret, not only the Nexus password despite its name).
# ---------------------------------------------------------------------------
SONAR_CONFIG_KEY = "sonar_connection"
SONAR_LAST_SUCCESS_KEY = "sonar_last_success"


@dataclass
class SonarConnection:
    url: str
    token: str

    def is_configured(self) -> bool:
        return bool(self.url and self.token)


async def get_sonar_connection(session: AsyncSession, settings: Settings) -> SonarConnection:
    """Dashboard value if present, otherwise the env/bootstrap default."""
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == SONAR_CONFIG_KEY))
    if row is not None:
        try:
            data = json.loads(row.value_json)
            return SonarConnection(
                url=data.get("url", ""),
                token=decrypt_password(data.get("token_enc", ""), settings),
            )
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupt sonar_connection config row — falling back to env.")

    return SonarConnection(url=settings.SONAR_URL, token=settings.SONAR_ADMIN_TOKEN)


async def save_sonar_connection(session: AsyncSession, settings: Settings, url: str, token: str) -> SonarConnection:
    """Persist the SonarQube connection. ``token`` is stored encrypted."""
    blob = json.dumps({
        "url": url.rstrip("/"),
        "token_enc": encrypt_password(token, settings),
    })
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == SONAR_CONFIG_KEY))
    if row is None:
        session.add(SystemConfig(key=SONAR_CONFIG_KEY, value_json=blob))
    else:
        row.value_json = blob
    await session.commit()
    logger.info("SonarQube connection updated via dashboard.")
    return SonarConnection(url=url.rstrip("/"), token=token)


async def sonar_connection_masked(session: AsyncSession) -> dict:
    """The connection with the token masked, plus when it was last saved."""
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == SONAR_CONFIG_KEY))
    if row is None:
        return {"configured": False}
    try:
        data = json.loads(row.value_json)
        return {
            "configured": True,
            "url": data.get("url", ""),
            "token_set": bool(data.get("token_enc")),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    except (json.JSONDecodeError, TypeError):
        return {"configured": False}


async def record_sonar_success(session: AsyncSession) -> None:
    """Timestamp the most recent successful SonarQube health check.

    Separate from ``updated_at`` on the connection row (which only changes
    when credentials are saved) — this is what "last successful
    communication" on the Integrations card actually means: the last time
    Rotsy proved it could reach Sonar, not the last time someone edited a URL.
    """
    import datetime as _dt
    blob = json.dumps({"at": _dt.datetime.now(_dt.timezone.utc).isoformat()})
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == SONAR_LAST_SUCCESS_KEY))
    if row is None:
        session.add(SystemConfig(key=SONAR_LAST_SUCCESS_KEY, value_json=blob))
    else:
        row.value_json = blob
    await session.commit()


async def get_sonar_last_success(session: AsyncSession) -> str | None:
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == SONAR_LAST_SUCCESS_KEY))
    if row is None:
        return None
    try:
        return json.loads(row.value_json).get("at")
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# GitHub App (dashboard-managed, same encrypted-at-rest pattern as above).
#
# Unlike Nexus/Sonar, this is normally populated by the App Manifest flow
# (see routers/github.py: create_manifest/manifest_callback), not typed in by
# hand — clicking "Connect to GitHub" creates a real GitHub App via GitHub's
# own manifest API and Rotsy saves whatever it returns here. Manual entry
# (typing an existing App's id/key/secret into a form) is still supported as
# a fallback for an App created outside that flow, since both end up in the
# same place.
# ---------------------------------------------------------------------------
GITHUB_APP_CONFIG_KEY = "github_app_connection"


@dataclass
class GitHubAppConfig:
    app_id: str
    app_slug: str
    private_key: str
    # Empty when the App was created without a webhook (e.g. local dev on
    # localhost, where GitHub cannot deliver webhooks at all) — the App is
    # still fully usable for cloning, repo access, and commit status; only
    # automatic push-triggered analysis needs this.
    webhook_secret: str

    def is_configured(self) -> bool:
        return bool(self.app_id and self.private_key)

    def has_webhook(self) -> bool:
        return bool(self.webhook_secret)


async def get_github_app_config(session: AsyncSession, settings: Settings) -> GitHubAppConfig:
    """Dashboard value if present, otherwise the env/bootstrap default."""
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == GITHUB_APP_CONFIG_KEY))
    if row is not None:
        try:
            data = json.loads(row.value_json)
            return GitHubAppConfig(
                app_id=data.get("app_id", ""),
                app_slug=data.get("app_slug", ""),
                private_key=decrypt_password(data.get("private_key_enc", ""), settings),
                webhook_secret=decrypt_password(data.get("webhook_secret_enc", ""), settings),
            )
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupt github_app_connection config row — falling back to env.")

    return GitHubAppConfig(
        app_id=settings.GITHUB_APP_ID, app_slug=settings.GITHUB_APP_SLUG,
        private_key=settings.GITHUB_APP_PRIVATE_KEY, webhook_secret=settings.GITHUB_WEBHOOK_SECRET,
    )


async def save_github_app_config(
    session: AsyncSession, settings: Settings, app_id: str, app_slug: str, private_key: str, webhook_secret: str,
) -> GitHubAppConfig:
    """Persist a GitHub App's credentials. The private key and webhook secret
    are stored encrypted; never returned in the clear (see
    :func:`github_app_config_masked`)."""
    blob = json.dumps({
        "app_id": app_id,
        "app_slug": app_slug,
        "private_key_enc": encrypt_password(private_key, settings),
        "webhook_secret_enc": encrypt_password(webhook_secret, settings),
    })
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == GITHUB_APP_CONFIG_KEY))
    if row is None:
        session.add(SystemConfig(key=GITHUB_APP_CONFIG_KEY, value_json=blob))
    else:
        row.value_json = blob
    await session.commit()
    logger.info("GitHub App connection saved (app_id=%s, slug=%s).", app_id, app_slug)
    return GitHubAppConfig(app_id=app_id, app_slug=app_slug, private_key=private_key, webhook_secret=webhook_secret)


async def github_app_config_masked(session: AsyncSession, settings: Settings) -> dict:
    """For the Settings -> Integrations -> GitHub card — never the private key or webhook secret."""
    cfg = await get_github_app_config(session, settings)
    return {
        "configured": cfg.is_configured(),
        "app_id": cfg.app_id or None,
        "app_slug": cfg.app_slug or None,
    }


# ---------------------------------------------------------------------------
# Telegram bot (dashboard-managed, same encrypted-at-rest pattern as Sonar).
# ---------------------------------------------------------------------------
TELEGRAM_CONFIG_KEY = "telegram_connection"


@dataclass
class TelegramConnection:
    token: str

    def is_configured(self) -> bool:
        return bool(self.token)


async def get_telegram_connection(session: AsyncSession, settings: Settings) -> TelegramConnection:
    """Dashboard value if present, otherwise the env/bootstrap default."""
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == TELEGRAM_CONFIG_KEY))
    if row is not None:
        try:
            data = json.loads(row.value_json)
            return TelegramConnection(token=decrypt_password(data.get("token_enc", ""), settings))
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupt telegram_connection config row — falling back to env.")

    return TelegramConnection(token=settings.TELEGRAM_BOT_TOKEN)


async def save_telegram_connection(session: AsyncSession, settings: Settings, token: str) -> TelegramConnection:
    """Persist the Telegram bot token. Stored encrypted; never returned in the clear."""
    blob = json.dumps({"token_enc": encrypt_password(token, settings)})
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == TELEGRAM_CONFIG_KEY))
    if row is None:
        session.add(SystemConfig(key=TELEGRAM_CONFIG_KEY, value_json=blob))
    else:
        row.value_json = blob
    await session.commit()
    logger.info("Telegram bot token updated via dashboard.")
    return TelegramConnection(token=token)


async def telegram_connection_masked(session: AsyncSession, settings: Settings) -> dict:
    """For the Settings -> Integrations -> Telegram card — never the token itself."""
    cfg = await get_telegram_connection(session, settings)
    return {"configured": cfg.is_configured()}
