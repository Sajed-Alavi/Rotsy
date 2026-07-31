"""System operations: backup + Nexus-to-Nexus sync.

These are heavy operations → they enqueue background jobs (the request returns
a job_id immediately, progress streams via /jobs/{id}/stream). Backup also has
a synchronous 'download DB snapshot' endpoint for convenience.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..core import config_store
from ..core.jobs import JobQueue
from ..core.outbound import OutboundURLError, validate_outbound_url
from ..dependencies import RequirePermission, get_current_user, get_session
from ..models import BackupRun, User
from ..services.backup_archive import InvalidRepositoryName, safe_repo_dirname
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


class BackupArchiveRequest(BaseModel):
    mode: str = Field(..., description="full | selective")
    repos: list[str] | None = Field(default=None, description="Required, non-empty when mode == 'selective'")

    @model_validator(mode="after")
    def _validate(self) -> "BackupArchiveRequest":
        if self.mode not in ("full", "selective"):
            raise ValueError("mode must be 'full' or 'selective'")
        if self.mode == "selective" and not self.repos:
            raise ValueError("selective backup requires a non-empty 'repos' list")
        if self.repos:
            for repo in self.repos:
                try:
                    safe_repo_dirname(repo)
                except InvalidRepositoryName as exc:
                    raise ValueError(str(exc)) from exc
        return self


@router.post("/backup/archive", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def enqueue_backup_archive(
    request: Request,
    body: BackupArchiveRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    """Enqueue a real byte-level backup (full or selective) to the backup volume."""
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    payload = body.model_dump()
    payload["triggered_by"] = user.username
    job_id = await JobQueue(cache).enqueue("backup_archive", payload)
    return {"job_id": job_id}


@router.get("/backup/archive", dependencies=[Depends(RequirePermission("system:read"))])
async def list_backup_archives(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    """Recent backup archive runs, newest first."""
    rows = (await session.execute(
        select(BackupRun).order_by(desc(BackupRun.started_at)).limit(limit)
    )).scalars().all()
    return [
        {
            "id": r.id, "mode": r.mode, "repos": json.loads(r.repos or "[]"),
            "status": r.status, "output_path": r.output_path,
            "total_bytes": r.total_bytes, "asset_count": r.asset_count,
            "error": r.error, "triggered_by": r.triggered_by,
            "started_at": r.started_at, "finished_at": r.finished_at,
        }
        for r in rows
    ]


@router.get("/backup/archive/{run_id}/download",
            dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def download_backup_archive(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Zip a completed archive run's directory on demand and stream it down."""
    import asyncio
    import shutil

    run = await session.get(BackupRun, run_id)
    if run is None or not run.output_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup run not found")
    run_dir = Path(run.output_path)
    if not run_dir.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archive directory no longer exists on disk")

    # Written under the same volume (not the system tmpdir), which may be a
    # much smaller overlay/tmpfs inside the container than the dedicated
    # backup volume that already has room reserved for archives.
    tmp_dir = Path(settings.BACKUP_OUTPUT_DIR) / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_base = tmp_dir / f"backup-{run_id}"
    zip_path = Path(await asyncio.to_thread(shutil.make_archive, str(zip_base), "zip", str(run_dir)))

    async def gen():
        try:
            with open(zip_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            zip_path.unlink(missing_ok=True)

    return StreamingResponse(
        gen(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="backup-{run_id}.zip"'},
    )


@router.post("/scripts/{name}", status_code=status.HTTP_501_NOT_IMPLEMENTED,
             dependencies=[Depends(RequirePermission("system:execute"))])
async def trigger_script(name: str) -> dict[str, str]:
    """TODO: trigger a whitelisted host maintenance script."""
    return {"status": "not_implemented", "feature": "System — Host scripts"}


# ---------------------------------------------------------------------------
# Sync (Nexus → Nexus)
# ---------------------------------------------------------------------------
class RepoMapping(BaseModel):
    source_repo: str = Field(..., description="Repository name on the primary Nexus (the one this wrapper manages).")
    target_repo: str = Field(..., description="Repository name on the target Nexus.")


class SyncRequest(BaseModel):
    repos: list[RepoMapping] = Field(..., min_length=1, description="One or more source→target repo pairs (selective sync).")
    target_base_url: str = Field(..., description="Target Nexus base URL, e.g. https://other-nexus.example.com")
    target_username: str
    target_password: str = Field(..., min_length=1)
    verify_ssl: bool = True

    @field_validator("target_base_url")
    @classmethod
    def _check_target_base_url(cls, value: str) -> str:
        """Reject sync targets the backend must not be pointed at.

        The backend dials this URL from inside the deployment network *and*
        sends ``target_username``/``target_password`` to it, so an unvalidated
        destination leaks credentials as well as reachability. See
        :mod:`app.core.outbound`.
        """
        try:
            return validate_outbound_url(value, get_settings())
        except OutboundURLError as exc:
            raise ValueError(f"target_base_url rejected: {exc}") from exc


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission(_SYNC_JOB_PERM))])
async def enqueue_sync(
    request: Request,
    body: SyncRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    """Enqueue a sync job: copy all components for each source→target repo pair.

    The target password is encrypted before it enters the payload. The queue is
    Redis-backed and the payload doubles as an inspectable record of the job, so
    a plaintext credential there would outlive the request and leak into any
    future job-inspector or debug-logging surface.
    """
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    payload = body.model_dump()
    payload["target_password_enc"] = config_store.encrypt_password(
        payload.pop("target_password"), settings
    )
    job_id = await JobQueue(cache).enqueue("sync", payload)
    return {"job_id": job_id}
