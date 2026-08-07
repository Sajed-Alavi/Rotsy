"""Scan reports, their findings, the dashboard summary, and deletion."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, case, delete as sa_delete, desc, func, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.access_control import DELETE, AccessResolver
from ...dependencies import RequirePermission, get_access, get_session
from ...models import ScannedImage, ScanReport, Vulnerability
from ...schemas.scan import ReportOut, VulnerabilityPage
from ...services.scan_report_pdf import build_report_pdf

router = APIRouter()


async def _visible_report_ids(
    session: AsyncSession, access: AccessResolver, *, action: str = "read",
) -> list[int] | None:
    """Report ids the caller may see, or ``None`` when nothing is restricted.

    ``None`` means "add no WHERE clause". Resolving the id set up front and
    pushing it into SQL keeps ``LIMIT``, ``OFFSET`` and ``total`` exact —
    filtering a page after the fact would report a total the caller cannot
    reach and hand back short pages whose length leaks what is hidden.
    """
    if access.unrestricted_everywhere:
        return None
    rows = (await session.execute(
        select(ScanReport.id, ScanReport.target_repo, ScanReport.image)
    )).all()
    return [rid for rid, repo, image in rows if access.repo(repo).allows(image, action)]


def _restrict(stmt, column, ids: list[int] | None):
    """Constrain ``stmt`` to ``ids`` unless the caller is unrestricted."""
    return stmt if ids is None else stmt.where(column.in_(ids))


@router.get("/reports", response_model=list[ReportOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_reports(
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
    repo: Annotated[str | None, Query()] = None,
    image: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """Recent scan reports, restricted to images the caller may read."""
    stmt = select(ScanReport).order_by(desc(ScanReport.started_at))
    if repo:
        stmt = stmt.where(ScanReport.target_repo == repo)
    if image:
        stmt = stmt.where(ScanReport.image == image)
    stmt = _restrict(stmt, ScanReport.id, await _visible_report_ids(session, access))
    return list((await session.execute(stmt.limit(limit))).scalars().all())


@router.get("/reports/{report_id}", dependencies=[Depends(RequirePermission("scan:read"))])
async def get_report(
    report_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
) -> dict[str, Any]:
    """One report including the diagnostic detail for a failure.

    ``detail`` carries the scanner's command line (with the password redacted),
    its exit code and the tail of its output — enough to tell a missing database
    from an unreachable connector from a permissions problem.
    """
    report = await _readable_report_or_404(session, access, report_id)
    try:
        raw = json.loads(report.raw_json or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    return {
        **ReportOut.model_validate(report).model_dump(),
        "detail": raw.get("detail", ""),
        "findings": raw.get("findings", 0),
    }


async def _readable_report_or_404(
    session: AsyncSession, access: AccessResolver, report_id: int, *, action: str = "read",
) -> ScanReport:
    """Fetch a report the caller may reach, or 404.

    A report outside the caller's rules is reported as missing rather than
    forbidden: by-id lookups would otherwise be an existence oracle over other
    teams' report ids, which the name-based endpoints do not leak.
    """
    report = await session.get(ScanReport, report_id)
    if report is None or not access.repo(report.target_repo).allows(report.image, action):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return report


# Rank severities explicitly. The previous ordering keyed off the first letter
# of the severity string, which put CRITICAL after nothing in particular and
# collated MEDIUM with anything else starting "M".
_SEVERITY_RANK = case(
    {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3},
    value=Vulnerability.severity,
    else_=4,
)

_SORT_COLUMNS = {
    "severity": None,  # handled specially — falls back to the rank case below
    "cvss": Vulnerability.cvss,
    "cve": Vulnerability.cve,
    "package": Vulnerability.package,
}


def _ordered_findings(stmt, sort: str = "severity", order: str = "desc"):
    """Most serious first by default: severity rank, then CVSS descending.

    ``sort``/``order`` let the caller pick a different column; ``severity``
    (the default) always orders by rank first, CVSS descending as a tiebreak,
    regardless of ``order`` — the other three columns honor ``order`` directly.
    """
    if sort not in _SORT_COLUMNS or sort == "severity":
        return stmt.order_by(_SEVERITY_RANK, desc(Vulnerability.cvss))
    column = _SORT_COLUMNS[sort]
    direction = asc if order == "asc" else desc
    return stmt.order_by(direction(column), _SEVERITY_RANK)


def _apply_finding_filters(
    stmt, *, repo: str | None = None, severity: str | None = None,
    scanner: str | None = None, q: str | None = None,
):
    if repo:
        stmt = stmt.where(Vulnerability.repo == repo)
    if severity:
        values = [s.strip().upper() for s in severity.split(",") if s.strip()]
        if values:
            stmt = stmt.where(Vulnerability.severity.in_(values))
    if scanner:
        stmt = stmt.where(Vulnerability.scanner == scanner)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            Vulnerability.cve.ilike(like),
            Vulnerability.package.ilike(like),
            Vulnerability.title.ilike(like),
        ))
    return stmt


@router.get("/vulnerabilities",
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_vulnerabilities(
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
    repo: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query(description="Comma-separated, e.g. CRITICAL,HIGH")] = None,
    scanner: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Free-text match against CVE id, package, title")] = None,
    sort: Annotated[str, Query(description="severity | cvss | cve | package")] = "severity",
    order: Annotated[str, Query(description="asc | desc")] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VulnerabilityPage:
    stmt = _apply_finding_filters(select(Vulnerability), repo=repo, severity=severity, scanner=scanner, q=q)
    # Vulnerability carries repo but not image, so the image dimension has to
    # come from the parent report.
    stmt = _restrict(stmt, Vulnerability.report_id, await _visible_report_ids(session, access))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        _ordered_findings(stmt, sort, order).limit(limit).offset(offset)
    )).scalars().all()
    return VulnerabilityPage(items=list(rows), total=total)


@router.get("/reports/{report_id}/vulnerabilities",
            dependencies=[Depends(RequirePermission("scan:read"))])
async def report_vulnerabilities(
    report_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
    severity: Annotated[str | None, Query(description="Comma-separated, e.g. CRITICAL,HIGH")] = None,
    scanner: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Free-text match against CVE id, package, title")] = None,
    sort: Annotated[str, Query(description="severity | cvss | cve | package")] = "severity",
    order: Annotated[str, Query(description="asc | desc")] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VulnerabilityPage:
    """Every finding for one report."""
    await _readable_report_or_404(session, access, report_id)
    stmt = _apply_finding_filters(
        select(Vulnerability).where(Vulnerability.report_id == report_id),
        severity=severity, scanner=scanner, q=q,
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        _ordered_findings(stmt, sort, order).limit(limit).offset(offset)
    )).scalars().all()
    return VulnerabilityPage(items=list(rows), total=total)


@router.get("/reports/{report_id}/pdf", dependencies=[Depends(RequirePermission("scan:read"))])
async def download_report_pdf(
    report_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
) -> StreamingResponse:
    """Render one report — metadata, severity breakdown, full CVE list, and
    derived recommendations — as a downloadable PDF.

    Gated with the same ``scan:read`` permission and the same
    ``_readable_report_or_404`` access-scoping helper as the other
    report-scoped endpoints above, so a report outside the caller's rules
    404s here exactly as it does for ``GET /reports/{report_id}``.
    """
    report = await _readable_report_or_404(session, access, report_id)
    pdf_bytes = await build_report_pdf(session, report)
    filename = f"scan-report-{report_id}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/summary", dependencies=[Depends(RequirePermission("scan:read"))])
async def scan_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
) -> dict[str, Any]:
    """Dashboard totals across the latest report per (image, scanner).

    Also reports the ledger breakdown, which is what shows that the system is
    *not* scanning history: a large ``baseline`` count alongside a small
    ``scanned`` count is the expected steady state on an established Nexus.
    """
    newest = (
        select(
            ScanReport.target_repo, ScanReport.image, ScanReport.scanner,
            func.max(ScanReport.started_at).label("ts"),
        )
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
    reports = [
        r for r in (await session.execute(stmt)).scalars().all()
        if access.repo(r.target_repo).allows(r.image)
    ]

    totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0,
              "scanned_images": 0, "failed": 0}
    per_repo: dict[str, dict[str, int]] = {}
    scanned_images: set[tuple[str, str]] = set()
    for report in reports:
        if report.status != "success":
            totals["failed"] += 1
            continue
        scanned_images.add((report.target_repo, report.image))
        bucket = per_repo.setdefault(
            report.target_repo,
            {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0},
        )
        for severity in ("critical", "high", "medium", "low", "unknown"):
            count = getattr(report, severity)
            totals[severity] += count
            bucket[severity] += count
    totals["scanned_images"] = len(scanned_images)

    # Counted in Python rather than with a GROUP BY: the aggregate would
    # otherwise reveal how many images exist in repositories the caller has no
    # access to.
    ledger: dict[str, int] = {}
    for state, repo, image in (await session.execute(
        select(ScannedImage.state, ScannedImage.repo, ScannedImage.image)
    )).all():
        if access.repo(repo).allows(image):
            ledger[state] = ledger.get(state, 0) + 1

    return {"totals": totals, "per_repo": per_repo, "ledger": ledger}


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_report(
    report_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    """Delete one report and its findings (cascade)."""
    report = await _readable_report_or_404(session, access, report_id, action=DELETE)
    await session.delete(report)
    await session.commit()


@router.delete("/reports", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_all_reports(
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
    repo: Annotated[str | None, Query(
        description="Scope the delete to one repository instead of every report.",
    )] = None,
    image: Annotated[str | None, Query(
        description="Scope the delete to one image (\"name:tag\"); requires repo. "
                    "Used by the Images tree's per-tag \"delete reports\" action.",
    )] = None,
    reset_ledger: Annotated[bool, Query(
        description="Also forget which images have been scanned. This does NOT trigger scans; "
                    "affected images return to 'baseline' and are only scanned on the next push "
                    "or on request.",
    )] = False,
):
    """Delete reports and findings — every report, or one repo/image scope.

    Unscoped (no ``repo``) is refused outright for a caller whose rules do not
    cover everything: a partial bulk delete is worse than a refusal, since the
    caller asked to clear the lot and would be told it worked. A scoped delete
    only needs ``delete`` on that one repo/image, matching the single-report
    delete's own access check.
    """
    if image and not repo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'image' requires 'repo'")

    if repo:
        image_name = image.split(":", 1)[0] if image else None
        allowed = access.repo(repo)
        permitted = allowed.unrestricted or (image_name is not None and allowed.allows(image_name, DELETE))
        if not permitted:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Not permitted to delete reports in '{repo}'" + (f"/{image}" if image else ""),
            )
        conditions = [ScanReport.target_repo == repo]
        if image:
            conditions.append(ScanReport.image == image)
        report_ids = select(ScanReport.id).where(*conditions)
        await session.execute(sa_delete(Vulnerability).where(Vulnerability.report_id.in_(report_ids)))
        await session.execute(sa_delete(ScanReport).where(*conditions))
        if reset_ledger:
            ledger_conditions = [ScannedImage.repo == repo]
            if image:
                ledger_conditions.append(ScannedImage.image == image)
            await session.execute(
                sa_update(ScannedImage).where(*ledger_conditions)
                .values(state="baseline", last_scan_at=None, scan_count=0, last_job_id="")
            )
        await session.commit()
        return

    if not access.unrestricted_everywhere:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Deleting every report requires unrestricted access. Delete the reports you "
            "can reach individually instead.",
        )
    await session.execute(sa_delete(Vulnerability))
    await session.execute(sa_delete(ScanReport))
    if reset_ledger:
        await session.execute(
            sa_update(ScannedImage)
            .values(state="baseline", last_scan_at=None, scan_count=0, last_job_id="")
        )
    await session.commit()
