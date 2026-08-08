"""Orchestration and persistence: run the scanners, write the reports.

The scanner adapters (:mod:`.trivy`, :mod:`.grype`) know nothing about the
database; this module is the only place scan results become ORM rows. That
separation is what makes the adapters and their parsers directly testable
without a session.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import ScanReport, Vulnerability
from . import db as scanner_db
from . import grype, trivy
from .base import SEVERITIES, Credentials, ScanOutcome, assert_static_ref
from .registry import DockerRegistry

logger = logging.getLogger(__name__)

# The scanner registry. Adding a backend means adding a module with a `run`
# coroutine and one entry here.
_RUNNERS = {"trivy": trivy.run, "grype": grype.run}


async def scan_image(
    session: AsyncSession,
    registry: DockerRegistry,
    image: str,
    scanners: list[str],
    creds: Credentials,
    *,
    verify_tls: bool = True,
) -> list[ScanReport]:
    """Scan one image with each requested scanner and persist the reports.

    ``image`` is ``name:tag`` within ``registry.repo``. One :class:`ScanReport`
    is written per scanner, including on failure — a failed scan is a result the
    operator needs to see, with the reason attached, not a silent gap.
    """
    image_ref = registry.image_ref(image)
    assert_static_ref(image_ref)
    ready = scanner_db.readiness(scanners)
    reports: list[ScanReport] = []

    for name in scanners:
        name = name.lower()
        report = ScanReport(
            target_repo=registry.repo, image=image, scanner=name, status="running",
            registry_ref=image_ref,
        )
        session.add(report)
        await session.flush()

        outcome = await _run_one(name, registry, image_ref, creds, ready, verify_tls=verify_tls)
        apply_outcome(session, report, outcome, registry.repo)
        reports.append(report)

    await session.commit()
    return reports


async def reap_stale_reports(session: AsyncSession) -> int:
    """Close out reports left in ``running`` by a worker that went away.

    A report row is written before the scanner is invoked so an in-flight scan
    is visible. If the process dies mid-scan that row would otherwise sit at
    ``running`` for ever and the image would look permanently in progress.
    Called once at startup — this inspects the database only and starts no scans.
    """
    result = await session.execute(
        sql_update(ScanReport)
        .where(ScanReport.status == "running")
        .values(
            status="failed",
            error="Interrupted: the backend restarted while this scan was running. "
                  "Scan the image again to retry.",
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("Marked %d interrupted scan report(s) as failed at startup", count)
    return count


async def _run_one(
    name: str,
    registry: DockerRegistry,
    image_ref: str,
    creds: Credentials,
    ready: dict[str, scanner_db.Readiness],
    *,
    verify_tls: bool,
) -> ScanOutcome:
    """Preflight, then invoke one scanner, converting exceptions into outcomes."""
    check = ready.get(name)
    if check is not None and not check.ready:
        return ScanOutcome(name, False, error=check.reason)
    runner = _RUNNERS.get(name)
    if runner is None:
        return ScanOutcome(name, False, error=f"unknown scanner '{name}' (expected trivy or grype)")
    try:
        return await runner(registry, image_ref, creds, verify_tls=verify_tls)
    except Exception as exc:  # noqa: BLE001 - one scanner must not sink the others
        logger.exception("%s scan of %s failed unexpectedly", name, image_ref)
        return ScanOutcome(name, False, error=f"{type(exc).__name__}: {exc}")


def severity_from_cvss(score: float) -> str:
    """Standard NVD severity bands, used only when a scanner omits severity."""
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


def _finding_to_row(finding: dict, report: ScanReport, repo: str, scanner: str) -> tuple[str, Vulnerability]:
    severity = (finding.get("severity") or "UNKNOWN").upper()
    if severity not in SEVERITIES:
        severity = "UNKNOWN"
    cvss = float(finding.get("cvss") or 0.0)
    if severity == "UNKNOWN" and cvss > 0:
        # The scanner gave no usable severity but did give a CVSS score —
        # without this, a real Critical/High finding silently undercounts
        # into "Unknown" instead of report.critical/report.high.
        severity = severity_from_cvss(cvss)
    row = Vulnerability(
        report_id=report.id, repo=repo, scanner=scanner,
        cve=finding.get("cve") or "UNKNOWN", severity=severity,
        package=finding.get("package") or "",
        installed_version=finding.get("installed_version") or "",
        fixed_version=finding.get("fixed_version") or "",
        title=finding.get("title") or "",
        cvss=cvss,
    )
    return severity, row


def apply_outcome(
    session: AsyncSession, report: ScanReport, outcome: ScanOutcome, repo: str,
) -> None:
    """Write an outcome onto its report row, with severity counts and findings."""
    counts = dict.fromkeys(SEVERITIES, 0)
    rows: list[Vulnerability] = []
    for finding in outcome.vulnerabilities:
        severity, row = _finding_to_row(finding, report, repo, outcome.scanner)
        counts[severity] += 1
        rows.append(row)
    if rows:
        session.add_all(rows)

    report.status = "success" if outcome.ok else "failed"
    report.critical = counts["CRITICAL"]
    report.high = counts["HIGH"]
    report.medium = counts["MEDIUM"]
    report.low = counts["LOW"]
    report.unknown = counts["UNKNOWN"]
    report.error = outcome.error or None
    report.duration_ms = outcome.duration_ms
    report.finished_at = datetime.now(timezone.utc)
    report.raw_json = json.dumps({
        "findings": len(outcome.vulnerabilities),
        "error": outcome.error or None,
        "detail": outcome.detail,
    }, default=str)
