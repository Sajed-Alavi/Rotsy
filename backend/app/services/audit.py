"""Audit logging helper — call from any router to record an action."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog

logger = logging.getLogger(__name__)


async def log_action(
    session: AsyncSession,
    username: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    detail: str = "",
) -> None:
    """Record an audit entry. Best-effort — never raises."""
    try:
        entry = AuditLog(
            username=username,
            action=action.upper(),
            resource_type=resource_type,
            resource_id=str(resource_id),
            detail=detail[:500],
        )
        session.add(entry)
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to write audit log", exc_info=True)
        await session.rollback()
