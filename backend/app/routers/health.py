"""Health probes.

Three endpoints with three different jobs, deliberately not one:

``GET /health/live`` — **liveness**, unauthenticated. Answers only "is this
process running and able to serve HTTP". It touches no dependency, on
purpose: a liveness probe that fails because Postgres is briefly unavailable
tells an orchestrator to restart a backend that was working fine, turning a
recoverable dependency blip into a restart loop that makes the outage worse.

``GET /health/ready`` — **readiness**, unauthenticated. Answers "can this
process serve real traffic *right now*", which does depend on Postgres and
Redis, and returns 503 when it cannot. Losing readiness takes an instance out
of rotation; it does not kill it.

``GET /health`` — the detailed, **authenticated** view the Dashboard reads.
Unchanged in shape and still reports Nexus/Redis reachability at a glance.

The split exists because there was no unauthenticated probe at all: Postgres
and Redis each declare a compose healthcheck while the backend could not,
since every endpoint required a session cookie.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..dependencies import get_current_user, get_session
from ..models import User

router = APIRouter(prefix="/health", tags=["health"])


async def _nexus_reachable(request: Request) -> bool:
    nexus_client = getattr(request.app.state, "nexus", None)
    if nexus_client is None:
        return False
    try:
        resp = await nexus_client.client.get(
            f"{nexus_client.settings.NEXUS_URL}/service/rest/v1/status/check"
        )
        return resp.status_code < 500
    except Exception:  # noqa: BLE001 - reachability probe, never fatal
        return False


async def _redis_reachable(request: Request) -> bool:
    cache = getattr(request.app.state, "cache", None)
    if cache is None or cache.redis is None:
        return False
    try:
        await cache.redis.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


@router.get("/live")
async def liveness() -> dict[str, Any]:
    """Always 200 while the process is serving. Checks nothing else — see
    this module's docstring for why that is the point rather than a
    shortcut."""
    return {"status": "alive", "version": __version__}


@router.get("/ready")
async def readiness(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """200 when this instance can serve traffic, 503 when it cannot.

    Postgres is required — practically every endpoint reads it. Redis is
    reported but not required: without it the job queue and cache are
    unavailable while ordinary reads still succeed, so degrading readiness to
    "not ready" would take the whole app out of rotation over a partial loss.
    """
    try:
        await session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:  # noqa: BLE001
        database_ok = False

    redis_ok = await _redis_reachable(request)
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if database_ok else "not-ready",
        "database": database_ok,
        "redis": redis_ok,
    }


@router.get("")
async def health(
    request: Request,
    _: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Service version + Nexus/Redis reachability, for the Dashboard."""
    return {
        "status": "ok",
        "version": __version__,
        "nexus_reachable": await _nexus_reachable(request),
        "redis_reachable": await _redis_reachable(request),
    }
