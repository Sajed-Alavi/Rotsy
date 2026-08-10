"""Vulnerability-database status, refresh and offline import."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...dependencies import RequirePermission, get_session, get_settings
from ...modules.nexus import db as scanner_db
from ...modules.nexus.db.tracking import current_db_job, enqueue_db_job
from ...services.scanner_config import get_enabled_scanners
from ._common import require_backend

router = APIRouter()


@router.get("/db-status", dependencies=[Depends(RequirePermission("scan:read"))])
async def scanner_db_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Each enabled scanner's database: version, build date, size, install
    state, readiness. A scanner turned off in Settings is omitted entirely —
    not just marked not-ready — so the Database page doesn't show a card for
    something the operator deliberately doesn't want to run."""
    enabled = set(await get_enabled_scanners(settings, session))
    snapshot = {name: info for name, info in scanner_db.status().items() if name in enabled}
    ready = scanner_db.readiness(list(snapshot.keys()))
    for name, info in snapshot.items():
        check = ready.get(name)
        info["ready"] = bool(check and check.ready)
        info["stale"] = bool(check and check.stale)
        info["reason"] = check.reason if check else ""
    return snapshot


@router.post("/db-update", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def enqueue_db_update(
    request: Request,
    force: Annotated[bool, Query(description="Download even if the local database is current")] = False,
    scanner: Annotated[
        str | None,
        Query(description="Update only this scanner (\"trivy\" or \"grype\"). Omit to update every enabled one."),
    ] = None,
) -> dict[str, str]:
    """Refresh the vulnerability databases over the network."""
    _, cache = require_backend(request)
    payload: dict[str, Any] = {"force": force}
    if scanner:
        payload["scanners"] = [scanner]
    return {"job_id": await enqueue_db_job(cache, "scanner_db_update", payload)}


@router.post("/db-import", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def enqueue_db_import(request: Request) -> dict[str, str]:
    """Install the vulnerability databases from offline archives (no network).

    For restricted or air-gapped networks where ``/db-update`` cannot reach
    Docker Hub / ghcr.io. Drop the archives into the mounted offline directory
    first — ``GET /scan/db-offline`` lists the expected filenames.
    """
    _, cache = require_backend(request)
    return {"job_id": await enqueue_db_job(cache, "scanner_db_import", {})}


@router.get("/db-offline", dependencies=[Depends(RequirePermission("scan:read"))])
async def scanner_offline_status() -> dict[str, Any]:
    """Archives detected in the offline import directory."""
    return scanner_db.offline_status()


@router.get("/db-job", dependencies=[Depends(RequirePermission("scan:read"))])
async def scanner_db_job(request: Request) -> dict[str, Any] | None:
    """The in-flight database job, or the most recent one.

    Lets the UI attach to an update it did not start — a scheduled refresh, the
    startup fetch, another operator's click, or its own job after a page reload.
    ``active`` says whether to open ``GET /jobs/{id}/stream``; ``detail`` carries
    the current bytes/speed/ETA so the bar is correct on first paint rather than
    waiting for the next event. Null when no database job has run recently.
    """
    _, cache = require_backend(request)
    return await current_db_job(cache)
