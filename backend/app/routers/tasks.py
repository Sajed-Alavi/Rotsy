"""Nexus scheduled tasks — list, run, stop.

Replaces the ``/analytics/tasks`` scaffold, which returned 501. The three
analytics endpoints alongside it (bandwidth, top-downloads, cache hit-rate) were
removed rather than implemented: Nexus OSS does not expose per-request data and
this app counts no requests, so there is no honest source for those numbers. A
tile showing zeros is worse than no tile.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.nexus_client import NexusClient
from ..dependencies import RequirePermission
from ..services import nexus_tasks
from ..state import app_state

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _nexus(request: Request) -> NexusClient:
    client = app_state(request).nexus
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not initialised")
    return client


@router.get("", dependencies=[Depends(RequirePermission("tasks:control"))])
async def list_tasks(request: Request) -> dict[str, Any]:
    """Every scheduled task Nexus reports, in one normalised shape.

    ``available`` distinguishes "Nexus has no tasks" from "this Nexus does not
    serve the task API" — the two look identical in an empty list but need
    completely different responses from the operator.
    """
    return await nexus_tasks.list_tasks(await _nexus(request))


@router.post("/{task_id}/run", dependencies=[Depends(RequirePermission("tasks:control"))])
async def run_task(request: Request, task_id: str) -> dict[str, Any]:
    """Run a task now, ignoring its schedule."""
    try:
        return await nexus_tasks.run_task(await _nexus(request), task_id)
    except nexus_tasks.TaskUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.post("/{task_id}/stop", dependencies=[Depends(RequirePermission("tasks:control"))])
async def stop_task(request: Request, task_id: str) -> dict[str, Any]:
    """Request that a running task stop at its next checkpoint."""
    try:
        return await nexus_tasks.stop_task(await _nexus(request), task_id)
    except nexus_tasks.TaskUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
