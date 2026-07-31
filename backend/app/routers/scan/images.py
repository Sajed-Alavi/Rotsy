"""The known-image ledger, and the per-image manual scan trigger."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...core.image_scope import allowed_image_patterns, image_visible
from ...dependencies import RequirePermission, get_current_user, get_session, get_settings
from ...models import ScannedImage, ScanReport, User
from ...schemas.scan import ScanRequest
from ...services.scanning import events as scan_events
from ._common import default_scanners, require_backend

router = APIRouter()


async def _latest_reports(
    session: AsyncSession, pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[ScanReport]]:
    """Most recent report per (repo, image, scanner) for the given images."""
    if not pairs:
        return {}
    repos = {repo for repo, _ in pairs}
    images = {image for _, image in pairs}
    newest = (
        select(
            ScanReport.target_repo, ScanReport.image, ScanReport.scanner,
            func.max(ScanReport.started_at).label("ts"),
        )
        .where(ScanReport.target_repo.in_(repos), ScanReport.image.in_(images))
        .group_by(ScanReport.target_repo, ScanReport.image, ScanReport.scanner)
        .subquery()
    )
    stmt = select(ScanReport).join(
        newest,
        (ScanReport.target_repo == newest.c.target_repo)
        & (ScanReport.image == newest.c.image)
        & (ScanReport.scanner == newest.c.scanner)
        & (ScanReport.started_at == newest.c.ts),
    )
    grouped: dict[tuple[str, str], list[ScanReport]] = {}
    for report in (await session.execute(stmt)).scalars().all():
        grouped.setdefault((report.target_repo, report.image), []).append(report)
    return grouped


@router.get("/images", dependencies=[Depends(RequirePermission("scan:read"))])
async def list_known_images(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    repo: Annotated[str | None, Query(description="Filter to one repository")] = None,
    state: Annotated[str | None, Query(description="baseline | queued | scanned | failed")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, Any]]:
    """Every image the system knows about, with its latest scan result.

    This is what backs the per-image Scan button. ``state`` explains why an image
    has or has not been scanned:

      * ``baseline`` — present before scanning was enabled; never auto-scanned.
      * ``queued``   — a scan is in flight.
      * ``scanned``  — scanned successfully; will not be re-scanned implicitly.
      * ``failed``   — the last attempt failed; see the report for the reason.

    Entries outside the caller's image-scope patterns for their repo (if any
    of their roles are scoped there) are omitted.
    """
    stmt = select(ScannedImage).order_by(desc(ScannedImage.first_seen_at)).limit(limit)
    if repo:
        stmt = stmt.where(ScannedImage.repo == repo)
    if state:
        stmt = stmt.where(ScannedImage.state == state)
    entries = (await session.execute(stmt)).scalars().all()

    # Patterns per distinct repo, computed once rather than per-row.
    patterns_by_repo: dict[str, list[str] | None] = {}
    visible_entries = []
    for entry in entries:
        if entry.repo not in patterns_by_repo:
            patterns_by_repo[entry.repo] = await allowed_image_patterns(session, user, entry.repo)
        if image_visible(patterns_by_repo[entry.repo], entry.image):
            visible_entries.append(entry)
    entries = visible_entries

    reports = await _latest_reports(session, [(e.repo, e.image) for e in entries])
    out: list[dict[str, Any]] = []
    for entry in entries:
        latest = reports.get((entry.repo, entry.image), [])
        out.append({
            "id": entry.id,
            "repo": entry.repo,
            "image": entry.image,
            "digest": entry.digest,
            "state": entry.state,
            "source": entry.source,
            "first_seen_at": entry.first_seen_at,
            "last_scan_at": entry.last_scan_at,
            "scan_count": entry.scan_count,
            "critical": sum(r.critical for r in latest),
            "high": sum(r.high for r in latest),
            "medium": sum(r.medium for r in latest),
            "low": sum(r.low for r in latest),
            "unknown": sum(r.unknown for r in latest),
            "reports": [
                {"id": r.id, "scanner": r.scanner, "status": r.status, "error": r.error,
                 "started_at": r.started_at, "duration_ms": r.duration_ms}
                for r in sorted(latest, key=lambda r: r.scanner)
            ],
        })
    return out


@router.post("/image", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def scan_one_image(
    request: Request,
    body: ScanRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Trigger (b): scan one image because an operator asked.

    The only path that may re-scan an image that is already scanned or
    baselined — an explicit request is always honoured.
    """
    patterns = await allowed_image_patterns(session, user, body.repo)
    if not image_visible(patterns, body.image):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"'{body.image}' is outside your image scope for '{body.repo}'.")

    _, cache = require_backend(request)
    return await scan_events.request_manual_scan(
        session, cache, body.repo, body.image,
        scanners=body.scanners, default_scanners=default_scanners(settings),
    )
