"""System operations: backup + Nexus-to-Nexus sync.

These are heavy operations → they enqueue background jobs (the request returns
a job_id immediately, progress streams via /jobs/{id}/stream). Backup also has
a synchronous 'download DB snapshot' endpoint for convenience.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..core.jobs import JobQueue
from ..dependencies import RequirePermission
from ..state import app_state

router = APIRouter(prefix="/system", tags=["system"])

_SYNC_JOB_PERM = "system:execute"
_BACKUP_JOB_PERM = "system:execute"


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
@router.get("/status", dependencies=[Depends(RequirePermission("system:read"))])
async def system_status() -> dict[str, str]:
    """TODO: Nexus version + update-availability check."""
    return {"status": "ok"}


@router.get("/backup/tasks", dependencies=[Depends(RequirePermission("system:read"))])
async def list_backup_tasks(request: Request) -> list[dict[str, Any]]:
    from ..services.backup import list_backup_tasks as _l
    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not available")
    return await _l(nexus)


@router.post("/backup", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def enqueue_backup(request: Request) -> dict[str, str]:
    """Enqueue a background job that triggers the Nexus backup task."""
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("backup", {})
    return {"job_id": job_id}


@router.get("/backup/db", dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def download_backup(request: Request) -> StreamingResponse:
    """Download a full metadata export (repos + assets) as JSON.

    Nexus OSS doesn't expose a DB backup endpoint, so we export repository
    configs + all asset manifests via the standard REST API. This is
    version-independent and can be used for migration/recovery.
    """
    from ..services.backup import export_metadata
    from datetime import datetime
    import json as _json

    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not available")
    try:
        data = await export_metadata(nexus)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Export failed: {exc}") from exc

    filename = f"nexus-export-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    content = _json.dumps(data, indent=2, default=str).encode()

    async def gen():
        yield content

    return StreamingResponse(
        gen(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/scripts/{name}", status_code=status.HTTP_501_NOT_IMPLEMENTED,
             dependencies=[Depends(RequirePermission("system:execute"))])
async def trigger_script(name: str) -> dict[str, str]:
    """TODO: trigger a whitelisted host maintenance script."""
    return {"status": "not_implemented", "feature": "System — Host scripts"}


# ---------------------------------------------------------------------------
# Sync (Nexus → Nexus)
# ---------------------------------------------------------------------------
class SyncRequest(BaseModel):
    source_repo: str = Field(..., description="Repository name on the primary Nexus (the one this wrapper manages).")
    target_base_url: str = Field(..., description="Target Nexus base URL, e.g. https://other-nexus.example.com")
    target_username: str
    target_password: str = Field(..., min_length=1)
    target_repo: str = Field(..., description="Repository name on the target Nexus.")
    verify_ssl: bool = True


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission(_SYNC_JOB_PERM))])
async def enqueue_sync(request: Request, body: SyncRequest) -> dict[str, str]:
    """Enqueue a sync job: copy all components from source_repo to target_repo."""
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("sync", body.model_dump())
    return {"job_id": job_id}
