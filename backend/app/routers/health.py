"""Health endpoint (authenticated).

Used by the Dashboard to show Nexus/Redis reachability at a glance. The
shared Nexus client and Redis cache live on ``app.state`` (created in the
FastAPI lifespan), so we read them via the incoming :class:`Request`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from .. import __version__
from ..dependencies import get_current_user
from ..models import User

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health(
    request: Request,
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Return service version + Nexus/Redis reachability."""
    nexus_ok = False
    redis_ok = False

    nexus_client = getattr(request.app.state, "nexus", None)
    if nexus_client is not None:
        try:
            resp = await nexus_client.client.get(
                f"{nexus_client.settings.NEXUS_URL}/service/rest/v1/status/check"
            )
            nexus_ok = resp.status_code < 500
        except Exception:  # noqa: BLE001 - reachability probe, never fatal
            nexus_ok = False

    cache = getattr(request.app.state, "cache", None)
    if cache is not None and cache.redis is not None:
        try:
            await cache.redis.ping()
            redis_ok = True
        except Exception:  # noqa: BLE001
            redis_ok = False

    return {
        "status": "ok",
        "version": __version__,
        "nexus_reachable": nexus_ok,
        "redis_reachable": redis_ok,
    }
