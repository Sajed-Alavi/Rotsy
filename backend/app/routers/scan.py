"""Vulnerability scanning endpoints.

Scans are **event-driven only** — see :mod:`app.services.scan_events`. There is
no "scan everything" endpoint: the previous ``POST /scan/scan-all`` fanned a job
out for every image in every enabled repository, which is exactly the behaviour
this system must not have. An image is scanned when it is pushed
(``POST /scan/events/nexus``, or the new-image watcher) or when an operator asks
(``POST /scan/image``).

Registry endpoints are discovered from Nexus, never configured:
``GET /scan/registry`` shows what discovery found and whether each endpoint
answers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config_store import get_or_create_webhook_secret, rotate_webhook_secret
from ..core.jobs import JobQueue
from ..config import Settings
from ..dependencies import RequirePermission, get_session, get_settings
from ..models import ScannedImage, ScanReport, ScanTarget, Vulnerability
from ..services import registry as registry_discovery
from ..services import scan_events, scanner_db
from ..state import app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scan", tags=["scan"])


def _require_backend(request: Request) -> tuple[Any, Any]:
    """Return ``(nexus, cache)``, or 503 when the backend is not ready."""
    state = app_state(request)
    if state.nexus is None or state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Backend not ready")
    return state.nexus, state.cache


def _default_scanners(settings: Settings) -> list[str]:
    return settings.scanners_enabled


# ---------------------------------------------------------------------------
# Scan targets (per-repository opt-in)
# ---------------------------------------------------------------------------
class TargetCreate(BaseModel):
    repo: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    auto_scan: bool = Field(default=True, description="Scan images pushed from now on")
    scanners: str = Field(default="", max_length=255, description="csv; empty = global default")


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
    baseline_at: datetime | None
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
    """Enable scanning for a repository.

    Enabling does **not** scan what is already there: the repository's existing
    images are adopted as a baseline on first observation and left alone. Scan
    them individually if you want them covered.
    """
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
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(target, key, value)
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
# Known images (the ledger) + per-image manual scan
# ---------------------------------------------------------------------------
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
    """
    stmt = select(ScannedImage).order_by(desc(ScannedImage.first_seen_at)).limit(limit)
    if repo:
        stmt = stmt.where(ScannedImage.repo == repo)
    if state:
        stmt = stmt.where(ScannedImage.state == state)
    entries = (await session.execute(stmt)).scalars().all()

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


class ScanRequest(BaseModel):
    repo: str = Field(..., min_length=1, description="Nexus Docker repository name")
    image: str = Field(..., min_length=1, description="Image reference within the repo, e.g. nginx:1.25")
    scanners: list[str] | None = Field(default=None, description="Override the enabled scanners")


@router.post("/image", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def scan_one_image(
    request: Request,
    body: ScanRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Trigger (b): scan one image because an operator asked.

    The only path that may re-scan an image that is already scanned or
    baselined — an explicit request is always honoured.
    """
    _, cache = _require_backend(request)
    return await scan_events.request_manual_scan(
        session, cache, body.repo, body.image,
        scanners=body.scanners, default_scanners=_default_scanners(settings),
    )


# ---------------------------------------------------------------------------
# Trigger (a): Nexus push webhook
# ---------------------------------------------------------------------------
@router.post("/events/nexus", status_code=status.HTTP_202_ACCEPTED, include_in_schema=True)
async def nexus_webhook(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Receive a Nexus repository component webhook and scan on push.

    Authenticated by the HMAC signature Nexus puts in
    ``X-Nexus-Webhook-Signature`` — not by a user session, since Nexus calls this
    machine-to-machine. Fetch the shared secret from ``GET /scan/webhook`` and
    paste it into the Nexus webhook capability.

    Always accepted with 202 once the signature checks out, including for events
    that do not lead to a scan (deletions, non-Docker formats, already-known
    content): a webhook receiver that returns errors for uninteresting events
    ends up disabled by the sender.
    """
    body = await request.body()
    signature = request.headers.get("X-Nexus-Webhook-Signature", "")
    secret = await get_or_create_webhook_secret(session, settings)

    if not scan_events.verify_webhook_signature(secret, body, signature):
        logger.warning(
            "Rejected a Nexus webhook delivery with a bad or missing signature (delivery %s)",
            request.headers.get("X-Nexus-Webhook-Delivery", "?"),
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid X-Nexus-Webhook-Signature. Configure the Nexus webhook capability with the "
            "secret from GET /api/scan/webhook.",
        )

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook body is not valid JSON")

    parsed = scan_events.parse_webhook_payload(payload if isinstance(payload, dict) else {})
    if parsed is None:
        return {"scanned": False, "reason": "event is not a Docker component create/update"}

    repo, ref = parsed
    _, cache = _require_backend(request)
    result = await scan_events.ingest_push_event(
        session, cache, repo, ref, source="webhook",
        default_scanners=_default_scanners(settings),
    )
    if not result.get("scanned"):
        response.status_code = status.HTTP_200_OK
    return result


@router.get("/webhook", dependencies=[Depends(RequirePermission("system:execute"))])
async def webhook_setup(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """The values and steps needed to wire the Nexus push webhook up.

    Creating a Nexus capability is an administrative action in Nexus itself, so
    this endpoint hands the operator the exact values rather than attempting it.
    """
    secret = await get_or_create_webhook_secret(session, settings)
    return {
        "event_id": scan_events.WEBHOOK_EVENT_ID,
        "secret": secret,
        "path": "/api/scan/events/nexus",
        "signature_header": "X-Nexus-Webhook-Signature",
        "instructions": [
            "In Nexus, open Administration → System → Capabilities and click Create capability.",
            "Choose type 'Webhook: Repository'.",
            "Set Repository to the Docker repository you want scanned (repeat per repository), "
            "and tick the 'component' event type.",
            "Set URL to this backend's webhook endpoint, reachable from the Nexus host — "
            "http://localhost:<BACKEND_PORT>/api/scan/events/nexus when Nexus runs on the "
            "Docker host and the backend publishes that port.",
            "Set Secret Key to the 'secret' value above.",
            "Save. The next image pushed to that repository is scanned within seconds.",
        ],
    }


@router.post("/webhook/rotate", dependencies=[Depends(RequirePermission("system:execute"))])
async def webhook_rotate(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Issue a new webhook secret. Update the Nexus capability to match."""
    return {"secret": await rotate_webhook_secret(session)}


# ---------------------------------------------------------------------------
# Registry discovery diagnostics
# ---------------------------------------------------------------------------
@router.get("/registry", dependencies=[Depends(RequirePermission("scan:read"))])
async def registry_map(
    request: Request,
    refresh: Annotated[bool, Query(description="Re-interrogate Nexus instead of using the cache")] = False,
    check: Annotated[bool, Query(description="Also probe each endpoint's /v2/ API")] = False,
) -> dict[str, Any]:
    """Show the Docker registry endpoint discovered for each repository.

    This replaces the hand-entered registry URL. ``unresolved`` explains any
    Docker repository whose endpoint could not be derived — almost always a
    repository with no connector port configured in Nexus, or a service account
    without repository-admin read privileges.
    """
    nexus, cache = _require_backend(request)
    result = await registry_discovery.discover(nexus, cache, refresh=refresh)
    payload = result.to_json()
    if check:
        for name, found in result.registries.items():
            payload["registries"][name]["probe"] = await registry_discovery.probe(nexus, found)
    return payload


# ---------------------------------------------------------------------------
# Scanner databases
# ---------------------------------------------------------------------------
@router.get("/db-status", dependencies=[Depends(RequirePermission("scan:read"))])
async def scanner_db_status() -> dict[str, Any]:
    """Each scanner's database: version, build date, size, install state, readiness."""
    snapshot = scanner_db.status()
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
) -> dict[str, str]:
    """Refresh the vulnerability databases over the network."""
    _, cache = _require_backend(request)
    return {"job_id": await JobQueue(cache).enqueue("scanner_db_update", {"force": force})}


@router.post("/db-import", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def enqueue_db_import(request: Request) -> dict[str, str]:
    """Install the vulnerability databases from offline archives (no network).

    For restricted or air-gapped networks where ``/db-update`` cannot reach
    Docker Hub / ghcr.io. Drop the archives into the mounted offline directory
    first — ``GET /scan/db-offline`` lists the expected filenames.
    """
    _, cache = _require_backend(request)
    return {"job_id": await JobQueue(cache).enqueue("scanner_db_import", {})}


@router.get("/db-offline", dependencies=[Depends(RequirePermission("scan:read"))])
async def scanner_offline_status() -> dict[str, Any]:
    """Archives detected in the offline import directory."""
    return scanner_db.offline_status()


# ---------------------------------------------------------------------------
# Reports + findings
# ---------------------------------------------------------------------------
class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_repo: str
    image: str
    scanner: str
    status: str
    registry_ref: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int
    critical: int
    high: int
    medium: int
    low: int
    unknown: int
    error: str | None


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
    import json

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


# Rank severities explicitly. The previous ordering keyed off the first letter
# of the severity string, which put CRITICAL after nothing in particular and
# collated MEDIUM with anything else starting "M".
_SEVERITY_RANK = case(
    {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3},
    value=Vulnerability.severity,
    else_=4,
)


def _ordered_findings(stmt):
    """Most serious first: severity rank, then CVSS descending."""
    return stmt.order_by(_SEVERITY_RANK, desc(Vulnerability.cvss))


@router.get("/vulnerabilities", response_model=list[VulnerabilityOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_vulnerabilities(
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    stmt = select(Vulnerability)
    if repo:
        stmt = stmt.where(Vulnerability.repo == repo)
    if severity:
        stmt = stmt.where(Vulnerability.severity == severity.upper())
    return list((await session.execute(_ordered_findings(stmt).limit(limit))).scalars().all())


@router.get("/reports/{report_id}/vulnerabilities", response_model=list[VulnerabilityOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def report_vulnerabilities(
    report_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    severity: Annotated[str | None, Query()] = None,
):
    """Every finding for one report."""
    if await session.get(ScanReport, report_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    stmt = select(Vulnerability).where(Vulnerability.report_id == report_id)
    if severity:
        stmt = stmt.where(Vulnerability.severity == severity.upper())
    return list((await session.execute(_ordered_findings(stmt))).scalars().all())


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
    from sqlalchemy import delete as sa_delete, update as sa_update

    await session.execute(sa_delete(Vulnerability))
    await session.execute(sa_delete(ScanReport))
    if reset_ledger:
        await session.execute(
            sa_update(ScannedImage)
            .values(state="baseline", last_scan_at=None, scan_count=0, last_job_id="")
        )
    await session.commit()
