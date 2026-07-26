"""Vulnerability scanning endpoints.

Manages per-repository scan targets (Trivy + Grype), runs scans on demand as
background jobs, lists reports + vulnerabilities for the dashboard, and lets
the user refresh vulnerability databases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.jobs import JobQueue
from ..dependencies import RequirePermission, get_session
from ..models import ScanReport, ScanTarget, Vulnerability
from ..state import app_state

router = APIRouter(prefix="/scan", tags=["scan"])


# ---------------------------------------------------------------------------
# Scan targets (enable scanning per repository)
# ---------------------------------------------------------------------------
class TargetCreate(BaseModel):
    repo: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    auto_scan: bool = True
    scanners: str = Field(default="", max_length=255)  # csv; empty = global default


class TargetUpdate(BaseModel):
    enabled: bool | None = None
    auto_scan: bool | None = None
    scanners: str | None = Field(default=None, max_length=255)


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repo: str
    enabled: bool
    auto_scan: bool
    scanners: str
    created_at: datetime
    updated_at: datetime


@router.get("/targets", response_model=list[TargetOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_targets(session: Annotated[AsyncSession, Depends(get_session)]):
    rows = (await session.execute(select(ScanTarget).order_by(ScanTarget.repo))).scalars().all()
    return list(rows)


@router.post("/targets", response_model=TargetOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def create_target(body: TargetCreate, session: Annotated[AsyncSession, Depends(get_session)]):
    clash = await session.scalar(select(ScanTarget).where(ScanTarget.repo == body.repo))
    if clash is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Target for this repo already exists.")
    target = ScanTarget(**body.model_dump())
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


@router.patch("/targets/{target_id}", response_model=TargetOut,
              dependencies=[Depends(RequirePermission("scan:execute"))])
async def update_target(target_id: int, body: TargetUpdate,
                        session: Annotated[AsyncSession, Depends(get_session)]):
    target = await session.get(ScanTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scan target not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(target, k, v)
    await session.commit()
    await session.refresh(target)
    return target


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_target(target_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    target = await session.get(ScanTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scan target not found")
    await session.delete(target)
    await session.commit()


# ---------------------------------------------------------------------------
# Run scans + status
# ---------------------------------------------------------------------------
class ScanRequest(BaseModel):
    repo: str
    image: str  # e.g. "docker-hosted/nginx:1.25"
    scanners: list[str] | None = None  # None = global default


@router.post("/image", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def enqueue_scan(request: Request, body: ScanRequest) -> dict[str, Any]:
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("scan_image", body.model_dump())
    return {"job_id": job_id}


@router.post("/db-update", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def enqueue_db_update(request: Request,
                            force: Annotated[bool, Query(description="Bypass date-check, force download")] = False) -> dict[str, str]:
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("scanner_db_update", {"force": force})
    return {"job_id": job_id}


@router.post("/scan-all", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def scan_all_existing(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """Retroactive scan: enqueue a scan job for every Docker image in every
    enabled scan target. Use this to scan images that were already in Nexus
    before scanning was enabled."""
    nexus = app_state(request).nexus
    cache = app_state(request).cache
    if nexus is None or cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Backend not ready")

    targets = (await session.execute(
        select(ScanTarget).where(ScanTarget.enabled.is_(True))
    )).scalars().all()
    job_ids: list[str] = []
    for t in targets:
        try:
            resp = await nexus.client.get("/service/rest/v1/components", params={"repository": t.repo})
            if resp.status_code != 200:
                continue
            scanners_list = t.scanners.split(",") if t.scanners else None
            for c in (resp.json() or {}).get("items", []) or []:
                if c.get("format") != "docker":
                    continue
                name = c.get("name")
                version = c.get("version")
                if not name or not version:
                    continue
                jid = await JobQueue(cache).enqueue("scan_image", {
                    "repo": t.repo,
                    "image": f"{name}:{version}",
                    "scanners": scanners_list,
                })
                job_ids.append(jid)
        except Exception:  # noqa: BLE001
            pass
    return {"job_ids": job_ids, "count": len(job_ids)}


@router.get("/db-status", dependencies=[Depends(RequirePermission("scan:read"))])
async def scanner_db_status() -> dict[str, Any]:
    """Return each scanner's DB info: version, the date it's for, size, install state.

    Example::

        {
          "trivy": {"installed": true, "present": true,
                    "version": "v2", "created_at": "2026-07-20T...",
                    "next_update": "...", "downloaded_at": "...",
                    "size_bytes": 184549376, "path": "/home/app/.cache/trivy/db"},
          "grype": {"installed": true, "present": true, "version": "5",
                    "built": "...", "size_bytes": 95000000, "path": "..."}
        }
    """
    from ..services.scanners import db_status
    return db_status()


@router.post("/db-import", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def enqueue_db_import(request: Request) -> dict[str, str]:
    """Import scanner DBs from pre-downloaded offline archives (no network).

    Use this on a restricted/air-gapped network where ``/db-update`` can't
    reach Docker Hub / ghcr.io. Drop the archives into the mounted offline
    dir first (see ``GET /scan/db-offline`` for the expected filenames), then
    call this to extract/import them into the scanner caches.
    """
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("scanner_db_import", {})
    return {"job_id": job_id}


@router.get("/db-offline", dependencies=[Depends(RequirePermission("scan:read"))])
async def scanner_offline_status() -> dict[str, Any]:
    """List archives detected in the offline import directory.

    Tells the operator exactly which files to drop in and whether they've been
    detected yet::

        {"dir": "/app/offline-db", "exists": true,
         "trivy_db": true, "grype_db": false,
         "files": [{"name": "db.tar.gz", "size_bytes": 52428800}]}
    """
    from ..services.scanners import offline_status
    return offline_status()


# ---------------------------------------------------------------------------
# Reports + vulnerabilities
# ---------------------------------------------------------------------------
class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_repo: str
    image: str
    scanner: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    critical: int
    high: int
    medium: int
    low: int
    unknown: int


@router.get("/reports", response_model=list[ReportOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_reports(
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    stmt = select(ScanReport).order_by(desc(ScanReport.started_at)).limit(limit)
    if repo:
        stmt = stmt.where(ScanReport.target_repo == repo)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


class VulnerabilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    repo: str
    scanner: str
    cve: str
    severity: str
    package: str
    installed_version: str
    fixed_version: str
    title: str
    cvss: float


@router.get("/vulnerabilities", response_model=list[VulnerabilityOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_vulnerabilities(
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    stmt = select(Vulnerability).order_by(
        # CRITICAL first, then HIGH, MEDIUM, LOW, UNKNOWN
        func.substring(Vulnerability.severity, 1, 1),
        desc(Vulnerability.cvss),
    ).limit(limit)
    if repo:
        stmt = stmt.where(Vulnerability.repo == repo)
    if severity:
        stmt = stmt.where(Vulnerability.severity == severity.upper())
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/summary", dependencies=[Depends(RequirePermission("scan:read"))])
async def scan_summary(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """Aggregate counts for the dashboard: totals across all latest reports."""
    # Latest report per repo/image.
    sub = (
        select(
            ScanReport.target_repo,
            ScanReport.image,
            func.max(ScanReport.started_at).label("ts"),
        )
        .group_by(ScanReport.target_repo, ScanReport.image)
        .subquery()
    )
    stmt = (
        select(ScanReport)
        .join(sub, (ScanReport.target_repo == sub.c.target_repo)
              & (ScanReport.image == sub.c.image)
              & (ScanReport.started_at == sub.c.ts))
    )
    rows = (await session.execute(stmt)).scalars().all()
    totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0, "scanned_images": 0, "failed": 0}
    per_repo: dict[str, dict[str, int]] = {}
    for r in rows:
        if r.status == "failed":
            totals["failed"] += 1
            continue
        totals["scanned_images"] += 1
        totals["critical"] += r.critical
        totals["high"] += r.high
        totals["medium"] += r.medium
        totals["low"] += r.low
        totals["unknown"] += r.unknown
        bucket = per_repo.setdefault(r.target_repo, {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0})
        bucket["critical"] += r.critical
        bucket["high"] += r.high
        bucket["medium"] += r.medium
        bucket["low"] += r.low
        bucket["unknown"] += r.unknown
    return {"totals": totals, "per_repo": per_repo}


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_report(report_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    """Delete a scan report and its vulnerabilities (cascade)."""
    report = await session.get(ScanReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    await session.delete(report)
    await session.commit()


@router.delete("/reports", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_all_reports(session: Annotated[AsyncSession, Depends(get_session)]):
    """Delete ALL scan reports and vulnerabilities."""
    from sqlalchemy import delete as sa_delete
    await session.execute(sa_delete(Vulnerability))
    await session.execute(sa_delete(ScanReport))
    await session.commit()


@router.get("/reports/{report_id}/vulnerabilities", dependencies=[Depends(RequirePermission("scan:read"))])
async def report_vulnerabilities(
    report_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    severity: Annotated[str | None, Query()] = None,
) -> list[VulnerabilityOut]:
    """Get all vulnerabilities for a specific report (detailed view)."""
    report = await session.get(ScanReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    stmt = select(Vulnerability).where(Vulnerability.report_id == report_id)
    if severity:
        stmt = stmt.where(Vulnerability.severity == severity.upper())
    stmt = stmt.order_by(
        func.substring(Vulnerability.severity, 1, 1),
        desc(Vulnerability.cvss),
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)
