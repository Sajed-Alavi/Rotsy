"""Helpers shared by the scan router modules."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...state import app_state


def require_backend(request: Request) -> tuple[Any, Any]:
    """Return ``(nexus, cache)``, or 503 when the backend is not ready."""
    state = app_state(request)
    if state.nexus is None or state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Backend not ready")
    return state.nexus, state.cache


async def default_scanners(settings: Settings, session: AsyncSession | None = None) -> list[str]:
    from ...services.scanner_config import get_enabled_scanners
    return await get_enabled_scanners(settings, session)
