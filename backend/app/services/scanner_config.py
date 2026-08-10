"""Which scanners are enabled — a dashboard setting overriding SCANNERS_ENABLED.

Mirrors the ``scanner_proxy`` SystemConfig pattern (see
``job_handlers._scanner_proxy``): a dashboard value, when present, wins over
the env var, and any read failure (row missing, bad JSON, no DB) falls back
to the env var rather than erroring — this is consulted on nearly every
scan/DB-update path, so it must never be the reason one fails.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.session import get_session_factory
from ..models import SystemConfig

logger = logging.getLogger(__name__)

CONFIG_KEY = "scanners_enabled"

# Every scanner the app knows how to run, regardless of which are currently
# enabled — the settings UI needs this to offer a toggle for a scanner that
# is turned off (and so hidden from scanner_config's own output). Adding a
# new scanner module means adding its name here too.
ALL_KNOWN_SCANNERS = ("trivy", "grype")


def _clean(names: object) -> list[str] | None:
    if not isinstance(names, list):
        return None
    cleaned = [n.strip().lower() for n in names if isinstance(n, str) and n.strip()]
    return cleaned or None


async def get_enabled_scanners(settings: Settings, session: AsyncSession | None = None) -> list[str]:
    """Enabled scanner names, lowercased — dashboard value first, then env."""
    try:
        if session is not None:
            row = await session.scalar(select(SystemConfig).where(SystemConfig.key == CONFIG_KEY))
        else:
            factory = get_session_factory()
            async with factory() as owned_session:
                row = await owned_session.scalar(select(SystemConfig).where(SystemConfig.key == CONFIG_KEY))
        if row is not None:
            cleaned = _clean(json.loads(row.value_json).get("scanners"))
            if cleaned is not None:
                return cleaned
    except Exception:  # noqa: BLE001 - the env fallback below still applies
        logger.debug("could not read the dashboard scanner-enable setting", exc_info=True)
    return settings.scanners_enabled


async def set_enabled_scanners(session: AsyncSession, names: list[str]) -> list[str]:
    """Persist the dashboard scanner-enable setting. Empty list means "all"
    (falls back to the env var on next read) rather than "none"."""
    cleaned = [n.strip().lower() for n in names if isinstance(n, str) and n.strip()
               and n.strip().lower() in ALL_KNOWN_SCANNERS]
    blob = json.dumps({"scanners": cleaned})
    row = await session.scalar(select(SystemConfig).where(SystemConfig.key == CONFIG_KEY))
    if row is None:
        session.add(SystemConfig(key=CONFIG_KEY, value_json=blob))
    else:
        row.value_json = blob
    await session.commit()
    return cleaned
