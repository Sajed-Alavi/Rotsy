"""System operations: backup + Nexus-to-Nexus sync.

These are heavy operations → they enqueue background jobs (the request returns
a job_id immediately, progress streams via /jobs/{id}/stream). Backup also has
a synchronous 'download DB snapshot' endpoint for convenience.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Callable

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..core import config_store
from ..core.jobs import JobQueue
from ..core.outbound import OutboundURLError, validate_outbound_url
from ..dependencies import RequirePermission, get_current_user, get_session
from ..models import BackupRun, BackupSchedule, User
from ..services import backup_schedule as backup_schedule_service
from ..services.backup_archive import InvalidRepositoryName, safe_repo_dirname
from ..state import app_state

router = APIRouter(prefix="/system", tags=["system"])

_SYNC_JOB_PERM = "system:execute"
_BACKUP_JOB_PERM = "system:execute"
_CACHE_UNAVAILABLE = "Cache unavailable"
_BACKUP_SCHEDULE_NOT_FOUND = "Backup schedule not found"


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
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CACHE_UNAVAILABLE)
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

    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not available")
    try:
        data = await export_metadata(nexus)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Export failed: {exc}") from exc

    filename = f"nexus-export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    content = json.dumps(data, indent=2, default=str).encode()

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
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CACHE_UNAVAILABLE)
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


async def _stream_file_chunks(path: Path, *, cleanup: Callable[[], None] | None = None) -> AsyncIterator[bytes]:
    """Stream a file's bytes off the event loop — a synchronous read would
    block every other request and job this worker process is handling for
    however long each chunk read takes. Optionally runs ``cleanup`` (e.g.
    deleting a temp file) once the stream ends."""
    f = await asyncio.to_thread(open, path, "rb")
    try:
        while True:
            chunk = await asyncio.to_thread(f.read, 1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(f.close)
        if cleanup is not None:
            cleanup()


@router.get("/backup/archive/{run_id}/download",
            dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def download_backup_archive(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Stream a completed archive run down.

    A scheduled (compressed) run already sits on disk as a single
    ``<run_id>.tar.gz`` — that's streamed directly. A manual (uncompressed) run
    is still a directory, zipped on demand exactly as before.
    """
    import shutil

    run = await session.get(BackupRun, run_id)
    if run is None or not run.output_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup run not found")
    output_path = Path(run.output_path)

    if output_path.is_file():
        filename = output_path.name
        return StreamingResponse(
            _stream_file_chunks(output_path), media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if not output_path.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archive no longer exists on disk")

    # Written under the same volume (not the system tmpdir), which may be a
    # much smaller overlay/tmpfs inside the container than the dedicated
    # backup volume that already has room reserved for archives.
    tmp_dir = Path(settings.BACKUP_OUTPUT_DIR) / "_tmp"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        zip_base = tmp_dir / f"backup-{run_id}"
        zip_path = Path(await asyncio.to_thread(shutil.make_archive, str(zip_base), "zip", str(output_path)))
    except PermissionError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Cannot write temporary archive: the backup directory is not writable "
            "by the application user — see the System troubleshooting docs.",
        ) from exc

    return StreamingResponse(
        _stream_file_chunks(zip_path, cleanup=lambda: zip_path.unlink(missing_ok=True)),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="backup-{run_id}.zip"'},
    )


@router.post("/scripts/{name}", status_code=status.HTTP_501_NOT_IMPLEMENTED,
             dependencies=[Depends(RequirePermission("system:execute"))])
async def trigger_script(name: str) -> dict[str, str]:
    """TODO: trigger a whitelisted host maintenance script."""
    return {"status": "not_implemented", "feature": "System — Host scripts"}


# ---------------------------------------------------------------------------
# Scheduled backups
# ---------------------------------------------------------------------------
class BackupScheduleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    mode: str = Field(..., description="full | selective")
    repos: list[str] | None = Field(default=None, description="Required, non-empty when mode == 'selective'")
    frequency: str = Field(..., description="daily | weekly | monthly | cron")
    time_of_day: str | None = Field(default=None, description="HH:MM, 24h — daily/weekly/monthly")
    day_of_week: int | None = Field(default=None, ge=0, le=6, description="0=Monday..6=Sunday — weekly")
    day_of_month: int | None = Field(default=None, ge=1, le=31, description="Clamped to month length — monthly")
    cron_expression: str | None = Field(default=None, max_length=128, description="Standard 5-field cron — cron")
    retention_keep_last: int | None = Field(default=None, ge=0)
    retention_max_age_days: int | None = Field(default=None, ge=1)
    enabled: bool = True

    def _validate_repos(self) -> None:
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

    def _validate_cron(self) -> None:
        if not self.cron_expression:
            raise ValueError("cron frequency requires cron_expression")
        if not croniter.is_valid(self.cron_expression):
            raise ValueError(f"invalid cron expression: {self.cron_expression!r}")

    def _validate_time_of_day(self) -> None:
        if not self.time_of_day:
            raise ValueError(f"{self.frequency} frequency requires time_of_day (HH:MM)")
        try:
            hh, mm = self.time_of_day.split(":")
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError
        except ValueError:
            raise ValueError("time_of_day must be 'HH:MM' 24h") from None
        if self.frequency == "weekly" and self.day_of_week is None:
            raise ValueError("weekly frequency requires day_of_week")
        if self.frequency == "monthly" and self.day_of_month is None:
            raise ValueError("monthly frequency requires day_of_month")

    @model_validator(mode="after")
    def _validate(self) -> "BackupScheduleBase":
        self._validate_repos()
        if self.frequency not in backup_schedule_service.FREQUENCIES:
            raise ValueError(f"frequency must be one of {backup_schedule_service.FREQUENCIES}")
        if self.frequency == "cron":
            self._validate_cron()
        else:
            self._validate_time_of_day()
        return self


class BackupScheduleCreate(BackupScheduleBase):
    pass


class BackupScheduleUpdate(BaseModel):
    """Partial update. Re-validated as a merge against the existing row."""
    name: str | None = Field(default=None, min_length=1, max_length=128)
    mode: str | None = None
    repos: list[str] | None = None
    frequency: str | None = None
    time_of_day: str | None = None
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    cron_expression: str | None = Field(default=None, max_length=128)
    retention_keep_last: int | None = Field(default=None, ge=0)
    retention_max_age_days: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class BackupScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    mode: str
    repos: list[str]
    frequency: str
    time_of_day: str | None
    day_of_week: int | None
    day_of_month: int | None
    cron_expression: str | None
    retention_keep_last: int | None
    retention_max_age_days: int | None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None
    next_run_at: datetime | None

    @field_validator("repos", mode="before")
    @classmethod
    def _parse_repos(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return json.loads(value or "[]")
        return value or []


def _schedule_out(schedule: BackupSchedule) -> BackupScheduleOut:
    return BackupScheduleOut.model_validate(schedule)


@router.get("/backup/schedules",
            dependencies=[Depends(RequirePermission("system:read"))])
async def list_backup_schedules(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[BackupScheduleOut]:
    rows = (await session.execute(select(BackupSchedule).order_by(BackupSchedule.id))).scalars().all()
    return [_schedule_out(r) for r in rows]


@router.post("/backup/schedules", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def create_backup_schedule(
    body: BackupScheduleCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BackupScheduleOut:
    data = body.model_dump()
    data["repos"] = json.dumps(data.get("repos") or [])
    schedule = BackupSchedule(**data)
    schedule.next_run_at = backup_schedule_service.compute_next_run(schedule)
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    return _schedule_out(schedule)


@router.patch("/backup/schedules/{schedule_id}",
              dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def update_backup_schedule(
    schedule_id: int,
    body: BackupScheduleUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BackupScheduleOut:
    schedule = await session.get(BackupSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BACKUP_SCHEDULE_NOT_FOUND)

    data = body.model_dump(exclude_unset=True)
    schedule_affecting = {
        "mode", "repos", "frequency", "time_of_day", "day_of_week",
        "day_of_month", "cron_expression",
    }
    merged = BackupScheduleCreate(
        name=data.get("name", schedule.name),
        mode=data.get("mode", schedule.mode),
        repos=data.get("repos", json.loads(schedule.repos or "[]") or None),
        frequency=data.get("frequency", schedule.frequency),
        time_of_day=data.get("time_of_day", schedule.time_of_day),
        day_of_week=data.get("day_of_week", schedule.day_of_week),
        day_of_month=data.get("day_of_month", schedule.day_of_month),
        cron_expression=data.get("cron_expression", schedule.cron_expression),
        retention_keep_last=data.get("retention_keep_last", schedule.retention_keep_last),
        retention_max_age_days=data.get("retention_max_age_days", schedule.retention_max_age_days),
        enabled=data.get("enabled", schedule.enabled),
    )
    for key, value in merged.model_dump().items():
        if key == "repos":
            value = json.dumps(value or [])
        setattr(schedule, key, value)
    if schedule_affecting & data.keys():
        schedule.next_run_at = backup_schedule_service.compute_next_run(schedule)
    await session.commit()
    await session.refresh(schedule)
    return _schedule_out(schedule)


@router.delete("/backup/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def delete_backup_schedule(
    schedule_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    schedule = await session.get(BackupSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BACKUP_SCHEDULE_NOT_FOUND)
    await session.delete(schedule)
    await session.commit()


@router.post("/backup/schedules/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission(_BACKUP_JOB_PERM))])
async def run_backup_schedule_now(
    schedule_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    schedule = await session.get(BackupSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BACKUP_SCHEDULE_NOT_FOUND)
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CACHE_UNAVAILABLE)
    job_id = await JobQueue(cache).enqueue("run_scheduled_backup", {"schedule_id": schedule_id})
    return {"job_id": job_id}


@router.get("/backup/schedules/{schedule_id}/preview",
            dependencies=[Depends(RequirePermission("system:read"))])
async def preview_backup_schedule(
    schedule_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Live ``next_run_at`` for the schedule's saved config — a sanity check,
    not a dry run of any deletion (schedules don't delete anything by
    themselves; ``prune_old_archives`` only removes archives this same
    schedule created)."""
    schedule = await session.get(BackupSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BACKUP_SCHEDULE_NOT_FOUND)
    next_run_at = backup_schedule_service.compute_next_run(schedule)
    return {"next_run_at": next_run_at}


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
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CACHE_UNAVAILABLE)
    payload = body.model_dump()
    payload["target_password_enc"] = config_store.encrypt_password(
        payload.pop("target_password"), settings
    )
    job_id = await JobQueue(cache).enqueue("sync", payload)
    return {"job_id": job_id}
