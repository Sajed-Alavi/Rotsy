"""Scan reports, their findings, the dashboard summary, and deletion."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, case, delete as sa_delete, desc, func, or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import RequirePermission, get_session
from ...models import ScannedImage, ScanReport, Vulnerability
from ...schemas.scan import ReportOut, VulnerabilityPage

router = APIRouter()


@router.get("/reports", response_model=list[ReportOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_reports(
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[str | None, Query()] = None,
    image: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    stmt = select(ScanReport).order_by(desc(ScanReport.started_at)).limit(limit)
    if repo:
        stmt = stmt.where(ScanReport.target_repo == repo)
    if image:
        stmt = stmt.where(ScanReport.image == image)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/reports/{report_id}", dependencies=[Depends(RequirePermission("scan:read"))])
async def get_report(
    report_id: int, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """One report including the diagnostic detail for a failure.

    ``detail`` carries the scanner's command line (with the password redacted),
    its exit code and the tail of its output — enough to tell a missing database
    from an unreachable connector from a permissions problem.
    """
    report = await session.get(ScanReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    try:
        raw = json.loads(report.raw_json or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    return {
        **ReportOut.model_validate(report).model_dump(),
        "detail": raw.get("detail", ""),
        "findings": raw.get("findings", 0),
    }


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


@router.get("/vulnerabilities", response_model=VulnerabilityPage,
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_vulnerabilities(
    session: Annotated[AsyncSession, Depends(get_session)],
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
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        _ordered_findings(stmt, sort, order).limit(limit).offset(offset)
    )).scalars().all()
    return VulnerabilityPage(items=list(rows), total=total)


@router.get("/reports/{report_id}/vulnerabilities", response_model=VulnerabilityPage,
            dependencies=[Depends(RequirePermission("scan:read"))])
async def report_vulnerabilities(
    report_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    severity: Annotated[str | None, Query(description="Comma-separated, e.g. CRITICAL,HIGH")] = None,
    scanner: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Free-text match against CVE id, package, title")] = None,
    sort: Annotated[str, Query(description="severity | cvss | cve | package")] = "severity",
    order: Annotated[str, Query(description="asc | desc")] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VulnerabilityPage:
    """Every finding for one report."""
    if await session.get(ScanReport, report_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    stmt = _apply_finding_filters(
        select(Vulnerability).where(Vulnerability.report_id == report_id),
        severity=severity, scanner=scanner, q=q,
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(
        _ordered_findings(stmt, sort, order).limit(limit).offset(offset)
    )).scalars().all()
    return VulnerabilityPage(items=list(rows), total=total)


@router.get("/summary", dependencies=[Depends(RequirePermission("scan:read"))])
async def scan_summary(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
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
    reports = (await session.execute(stmt)).scalars().all()

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

    ledger_rows = (await session.execute(
        select(ScannedImage.state, func.count()).group_by(ScannedImage.state)
    )).all()
    return {
        "totals": totals,
        "per_repo": per_repo,
        "ledger": {state: count for state, count in ledger_rows},
    }


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_report(report_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    """Delete one report and its findings (cascade)."""
    report = await session.get(ScanReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    await session.delete(report)
    await session.commit()


@router.delete("/reports", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_all_reports(
    session: Annotated[AsyncSession, Depends(get_session)],
    reset_ledger: Annotated[bool, Query(
        description="Also forget which images have been scanned. This does NOT trigger scans; "
                    "affected images return to 'baseline' and are only scanned on the next push "
                    "or on request.",
    )] = False,
):
    """Delete all reports and findings."""
    await session.execute(sa_delete(Vulnerability))
    await session.execute(sa_delete(ScanReport))
    if reset_ledger:
        await session.execute(
            sa_update(ScannedImage)
            .values(state="baseline", last_scan_at=None, scan_count=0, last_job_id="")
        )
    await session.commit()
