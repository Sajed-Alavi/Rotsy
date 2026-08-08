"""SonarQube connection management, project setup, analysis history, and the
manual "Run Analysis" trigger.

Connecting a project to Sonar is explicit (this router), separate from the
push-triggered analysis flow (``workers/analysis_worker.py``): the language
allowlist has to be picked once by a human, since Rotsy does not attempt to
auto-detect a build-free-safe language from repo contents.

Manual analysis enqueues the exact same ``clone_and_analyze`` job the GitHub
webhook enqueues (see ``run_analysis`` below) — there is one analysis
implementation, not two.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import asc, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..core.config_store import (
    get_github_app_config,
    get_sonar_connection,
    get_sonar_last_success,
    record_sonar_success,
    save_sonar_connection,
    sonar_connection_masked,
)
from ..core.jobs import JobQueue
from ..core.source_provider import RepoRef
from ..db.session import get_session_factory
from ..dependencies import RequirePermission, get_session, get_settings
from ..models import (
    AnalysisRun, GitHubInstallation, GitHubRepository, GitLabRepository, Project, QualityGateResult,
    SonarHotspot, SonarIssue, SonarProject,
)
from ..models.sonar import SUPPORTED_LANGUAGES
from ..modules.github.provider import GitHubProvider
from ..modules.gitlab.provider import GitLabProvider
from ..modules.sonar.connector import SonarClient, SonarError
from ..modules.sonar.provisioning import create_sonar_project_row, pick_supported_language
from ..schemas.sonar import (
    AnalysisRunOut,
    QualityGateResultOut,
    SonarHotspotPage,
    SonarIssuePage,
    SonarProjectCreate,
    SonarProjectOut,
    SonarProjectUpdate,
)
from ..services.sonar_report_pdf import build_analysis_report_pdf
from ..state import app_state, AppState

router = APIRouter(prefix="/modules/sonar", tags=["sonar"])
logger = logging.getLogger(__name__)

# SonarQube versions below this are not tested against and may not support
# the Web API calls this module relies on (project analysis tokens in
# particular are a relatively recent addition). This is a floor, not a
# license-edition check — Community Edition at or above this version works.
MIN_SUPPORTED_MAJOR = 9

_UNREACHABLE_MESSAGE = "Unable to connect to SonarQube. Verify the server URL, token, and network connectivity."
_SONAR_PROJECT_NOT_FOUND = "Sonar project not found"


def _compatibility(version: str | None) -> tuple[bool, str | None]:
    """(compatible, warning) from a Sonar version string like "10.4.1.88267"."""
    if not version:
        return False, None
    try:
        major = int(version.split(".")[0])
    except (ValueError, IndexError):
        return False, f"Could not parse SonarQube version {version!r}."
    if major < MIN_SUPPORTED_MAJOR:
        return False, f"SonarQube {version} is older than the minimum supported major version ({MIN_SUPPORTED_MAJOR}.x)."
    return True, None


# ---------------------------------------------------------------------------
# Connection management (Settings -> Integrations -> SonarQube)
# ---------------------------------------------------------------------------
class SonarConnectionUpdate(BaseModel):
    url: str = Field(..., min_length=1, max_length=512)
    token: str = Field(..., min_length=1, max_length=256, description="Leave as the current value to keep it unchanged")


@router.get("/config", dependencies=[Depends(RequirePermission("system:execute"))])
async def get_sonar_config(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    """The stored connection with the token masked — never returned in the clear."""
    return await sonar_connection_masked(session)


@router.put("/config", dependencies=[Depends(RequirePermission("system:execute"))])
async def update_sonar_config(
    body: SonarConnectionUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Save the SonarQube connection. Stored encrypted; masked on every read back."""
    await save_sonar_connection(session, settings, body.url, body.token)
    return await sonar_connection_masked(session)


@router.post("/config/test", dependencies=[Depends(RequirePermission("system:execute"))])
async def test_sonar_config(body: SonarConnectionUpdate) -> dict:
    """Try the given URL/token without saving — same shape as the Nexus test-connection endpoint."""
    url = (body.url or "").strip()
    if not url:
        return {"ok": False, "error": "URL is required."}
    # http:// is deliberately accepted — SonarQube on a trusted internal
    # network (host.docker.internal, this app's own documented setup) is
    # routinely plain HTTP; this validates URL *format*, not transport security.
    if not url.startswith(("http://", "https://")):  # NOSONAR
        return {"ok": False, "error": "URL must start with http:// or https://"}

    try:
        client = SonarClient(url, body.token)
        info = await client.server_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sonar test-connection failed for %s: %s", url, exc)
        return {"ok": False, "error": _UNREACHABLE_MESSAGE}

    version = info.get("version")
    sonar_status_value = info.get("status", "")
    if sonar_status_value != "UP":
        return {"ok": False, "error": f"SonarQube reports status {sonar_status_value or 'UNKNOWN'}."}
    compatible, warning = _compatibility(version)
    return {"ok": True, "version": version, "compatible": compatible, "warning": warning}


@router.get("/status", dependencies=[Depends(RequirePermission("projects:read"))])
async def sonar_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Connection status for the Settings -> Integrations -> SonarQube card.

    Never returns the token itself. Reads the effective connection (dashboard
    value if saved, otherwise the env bootstrap default) so this reflects
    whatever Rotsy would actually use for the next analysis.
    """
    conn = await get_sonar_connection(session, settings)
    if not conn.is_configured():
        return {"configured": False, "reachable": False, "version": None, "server_url": None,
                "compatible": None, "last_success_at": None, "error": None}

    try:
        client = SonarClient(conn.url, conn.token)
        info = await client.server_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sonar status check failed for %s: %s", conn.url, exc)
        return {"configured": True, "reachable": False, "version": None, "server_url": conn.url,
                "compatible": None, "last_success_at": await get_sonar_last_success(session),
                "error": _UNREACHABLE_MESSAGE}

    status_value = info.get("status", "")
    reachable = status_value == "UP"
    version = info.get("version")
    compatible, compat_warning = _compatibility(version) if reachable else (None, None)

    error = None
    if not reachable:
        error = f"SonarQube reports status {status_value or 'UNKNOWN'}."
    elif compat_warning:
        error = compat_warning

    if reachable:
        await record_sonar_success(session)

    return {
        "configured": True,
        "reachable": reachable,
        "version": version,
        "server_url": conn.url,
        "compatible": compatible,
        "last_success_at": await get_sonar_last_success(session),
        "error": error,
    }


@router.post("/check-updates", dependencies=[Depends(RequirePermission("system:execute"))])
async def check_sonar_updates(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Settings -> Integrations -> SonarQube -> "Check for Updates".

    Reports what SonarQube's own update center says is newer than the
    running instance. Does not install anything — Rotsy doesn't manage the
    Sonar deployment (container/VM/bare install all look the same from
    here), so an in-place upgrade isn't something this can safely trigger.
    """
    conn = await get_sonar_connection(session, settings)
    if not conn.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SonarQube is not configured.")
    try:
        client = SonarClient(conn.url, conn.token)
        info = await client.server_status()
        upgrades = await client.check_upgrades()
    except SonarError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _UNREACHABLE_MESSAGE) from exc

    return {
        "current_version": info.get("version"),
        "update_available": bool(upgrades),
        "latest_version": upgrades[0]["version"] if upgrades else None,
        "upgrades": upgrades,
    }


def _validate_create_body(body: SonarProjectCreate) -> None:
    if body.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{body.language!r} is not analyzable without a build step. "
            f"Supported: {', '.join(SUPPORTED_LANGUAGES)}.",
        )
    if not body.github_repository_id and not body.gitlab_repository_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                             "github_repository_id or gitlab_repository_id is required — "
                             "a Sonar project belongs to one specific repository under the Project.")
    if body.github_repository_id and body.gitlab_repository_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Specify only one of github_repository_id/gitlab_repository_id.")


async def _resolve_target_repo(session: AsyncSession, body: SonarProjectCreate) -> str:
    """The label of the repository this Sonar project will belong to, after
    checking it's actually connected to the Project in ``body``."""
    if body.github_repository_id:
        repo = await session.get(GitHubRepository, body.github_repository_id)
        repo_label = repo.full_name if repo else None
    else:
        repo = await session.get(GitLabRepository, body.gitlab_repository_id)
        repo_label = repo.full_path if repo else None
    if repo is None or repo.project_id != body.project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That repository is not connected to this Project.")
    return repo_label


async def _ensure_no_existing_sonar_project(session: AsyncSession, body: SonarProjectCreate, repo_label: str) -> None:
    existing = await session.scalar(
        select(SonarProject).where(
            SonarProject.github_repository_id == body.github_repository_id
            if body.github_repository_id else SonarProject.gitlab_repository_id == body.gitlab_repository_id
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{repo_label} already has a Sonar project")


# ---------------------------------------------------------------------------
# Project mapping
# ---------------------------------------------------------------------------
@router.post("/projects", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def create_sonar_project(
    body: SonarProjectCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SonarProjectOut:
    _validate_create_body(body)
    project = await projects_core.get_project(session, body.project_id)
    repo_label = await _resolve_target_repo(session, body)
    await _ensure_no_existing_sonar_project(session, body, repo_label)

    try:
        return await create_sonar_project_row(
            session, settings, project.id, repo_label, body.language, body.quality_gate,
            github_repository_id=body.github_repository_id, gitlab_repository_id=body.gitlab_repository_id,
        )
    except SonarError as exc:
        if "not configured" in str(exc):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "SonarQube is not configured. Set it up in Settings -> Integrations -> SonarQube first.",
            ) from exc
        if "does not exist" in str(exc):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _UNREACHABLE_MESSAGE) from exc


@router.patch("/projects/{sonar_project_id}", response_model=SonarProjectOut,
              dependencies=[Depends(RequirePermission("projects:write"))])
async def update_sonar_project(
    sonar_project_id: int,
    body: SonarProjectUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SonarProject:
    """Edit a connected repository's push-triggered analysis behavior:
    whether it's on at all, and which branches it watches. Independent of
    whether the webhook mechanism (GitHub App installation / GitLab webhook)
    exists — that only says a push notification *can* arrive, this says
    whether Rotsy should act on it. Manual analysis (Code Quality's "Run
    Analysis", or the per-repository endpoint below) is never affected by
    either setting."""
    sonar_project = await session.get(SonarProject, sonar_project_id)
    if sonar_project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _SONAR_PROJECT_NOT_FOUND)

    if body.auto_analyze_enabled is not None:
        sonar_project.auto_analyze_enabled = body.auto_analyze_enabled
    if body.auto_analyze_branches is not None:
        sonar_project.auto_analyze_branches = body.auto_analyze_branches

    await session.commit()
    await session.refresh(sonar_project)
    return sonar_project


@router.get("/quality-gates", dependencies=[Depends(RequirePermission("projects:read"))])
async def list_quality_gates(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[dict]:
    """Every quality gate defined on the connected SonarQube instance —
    including any the operator created or edited directly in Sonar's own
    UI — for the "connect a project" gate picker. Rotsy's own "Rotsy
    Standard" gate is just one entry in this list, not a special case."""
    conn = await get_sonar_connection(session, settings)
    if not conn.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SonarQube is not configured.")
    try:
        client = SonarClient(conn.url, conn.token)
        return await client.list_quality_gates()
    except SonarError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _UNREACHABLE_MESSAGE) from exc


# ---------------------------------------------------------------------------
# Global repository listing — the Code Quality section's repo picker draws
# from this rather than any one Project's tab, since analysis is no longer
# scoped to a single Project (see routers/projects.py's
# list_project_repositories for the per-Project version this mirrors).
# ---------------------------------------------------------------------------
@router.get("/repositories", dependencies=[Depends(RequirePermission("projects:read"))])
async def list_all_repositories(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Every repository mapped to a Project, across every Project. A
    repository must belong to a Project before it can be analyzed —
    ``SonarProject.project_id`` is non-nullable — so unmapped repositories
    (not yet attached anywhere) are excluded, same as the per-Project view."""
    sonar_by_github: dict[int, SonarProject] = {}
    sonar_by_gitlab: dict[int, SonarProject] = {}
    for sp in (await session.execute(select(SonarProject))).scalars():
        if sp.github_repository_id:
            sonar_by_github[sp.github_repository_id] = sp
        if sp.gitlab_repository_id:
            sonar_by_gitlab[sp.gitlab_repository_id] = sp

    project_names = dict((await session.execute(select(Project.id, Project.name))).all())

    out: list[dict] = []
    github_repos = (
        await session.execute(select(GitHubRepository).where(GitHubRepository.project_id.isnot(None)))
    ).scalars().all()
    for r in github_repos:
        sp = sonar_by_github.get(r.id)
        out.append({
            "source_module": "github",
            "repository_id": r.id,
            "full_name": r.full_name,
            "default_branch": r.default_branch,
            "auto_analyze_on_push": r.installation_id is not None and (sp is None or sp.auto_analyze_enabled),
            "sonar_project_id": sp.id if sp else None,
            "language": sp.language if sp else None,
            "auto_analyze_enabled": sp.auto_analyze_enabled if sp else None,
            "auto_analyze_branches": sp.auto_analyze_branches if sp else None,
            "project_id": r.project_id,
            "project_name": project_names.get(r.project_id),
        })

    gitlab_repos = (
        await session.execute(select(GitLabRepository).where(GitLabRepository.project_id.isnot(None)))
    ).scalars().all()
    for r in gitlab_repos:
        sp = sonar_by_gitlab.get(r.id)
        out.append({
            "source_module": "gitlab",
            "repository_id": r.id,
            "full_name": r.full_path,
            "default_branch": r.default_branch,
            "auto_analyze_on_push": r.webhook_id is not None and (sp is None or sp.auto_analyze_enabled),
            "sonar_project_id": sp.id if sp else None,
            "language": sp.language if sp else None,
            "auto_analyze_enabled": sp.auto_analyze_enabled if sp else None,
            "auto_analyze_branches": sp.auto_analyze_branches if sp else None,
            "project_id": r.project_id,
            "project_name": project_names.get(r.project_id),
        })

    out.sort(key=lambda row: row["full_name"])
    return out


# ---------------------------------------------------------------------------
# Analysis history / detail
# ---------------------------------------------------------------------------
@router.get("/repositories/{sonar_project_id}/analysis-runs", response_model=list[AnalysisRunOut],
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_repository_analysis_runs(
    sonar_project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AnalysisRun]:
    """History for one repository — the primary view, since a Project can
    hold many repositories each with their own independent analysis history."""
    if await session.get(SonarProject, sonar_project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _SONAR_PROJECT_NOT_FOUND)
    rows = (
        await session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.sonar_project_id == sonar_project_id)
            .order_by(desc(AnalysisRun.started_at))
            .limit(50)
        )
    ).scalars().all()
    return list(rows)


@router.get("/projects/{project_id}/analysis-runs", response_model=list[AnalysisRunOut],
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_analysis_runs(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AnalysisRun]:
    """Recent activity across *every* repository under the Project — each
    row's ``sonar_project_id`` says which one. Use
    ``/repositories/{sonar_project_id}/analysis-runs`` for one repo's own history."""
    sonar_project_ids = (
        await session.execute(select(SonarProject.id).where(SonarProject.project_id == project_id))
    ).scalars().all()
    if not sonar_project_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project has no Sonar projects configured")
    rows = (
        await session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.sonar_project_id.in_(sonar_project_ids))
            .order_by(desc(AnalysisRun.started_at))
            .limit(100)
        )
    ).scalars().all()
    return list(rows)


@router.get("/analysis-runs", response_model=list[AnalysisRunOut],
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_all_analysis_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AnalysisRun]:
    """Recent analysis activity across every repository, every Project — the
    Code Quality section's global run history (unlike the two endpoints
    above, not scoped to one repository or one Project)."""
    rows = (
        await session.execute(
            select(AnalysisRun).order_by(desc(AnalysisRun.started_at)).limit(200)
        )
    ).scalars().all()
    return list(rows)


@router.get("/analysis-runs/{run_id}", response_model=AnalysisRunOut,
            dependencies=[Depends(RequirePermission("projects:read"))])
async def get_analysis_run(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AnalysisRun:
    row = await session.get(AnalysisRun, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis run not found")
    return row


@router.get("/analysis-runs/{run_id}/quality-gate", response_model=QualityGateResultOut,
            dependencies=[Depends(RequirePermission("projects:read"))])
async def get_quality_gate(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QualityGateResult:
    row = await session.scalar(
        select(QualityGateResult).where(QualityGateResult.analysis_run_id == run_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No quality gate result for this run")
    return row


# ---------------------------------------------------------------------------
# Findings — per-issue and per-hotspot detail, plus the PDF export
# ---------------------------------------------------------------------------
# Explicit rank so BLOCKER sorts first — the plain string ordering SQL would
# otherwise use puts "BLOCKER" after "MAJOR" alphabetically.
_ISSUE_SEVERITY_RANK = case(
    {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4},
    value=SonarIssue.severity,
    else_=5,
)
_ISSUE_SORT_COLUMNS = {
    "severity": None,  # handled specially below
    "type": SonarIssue.type,
    "component": SonarIssue.component,
    "rule": SonarIssue.rule,
}

# Same reasoning as _ISSUE_SEVERITY_RANK — plain string ordering puts HIGH
# after MEDIUM alphabetically.
_HOTSPOT_PROBABILITY_RANK = case(
    {"HIGH": 0, "MEDIUM": 1, "LOW": 2}, value=SonarHotspot.vulnerability_probability, else_=3,
)


def _ordered_issues(stmt, sort: str = "severity", order: str = "desc"):
    if sort not in _ISSUE_SORT_COLUMNS or sort == "severity":
        return stmt.order_by(_ISSUE_SEVERITY_RANK, desc(SonarIssue.creation_date))
    direction = asc if order == "asc" else desc
    return stmt.order_by(direction(_ISSUE_SORT_COLUMNS[sort]), _ISSUE_SEVERITY_RANK)


async def _run_or_404(session: AsyncSession, run_id: int) -> AnalysisRun:
    run = await session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis run not found")
    return run


def _latest_successful_run_ids(sonar_project_id: int | None = None):
    """Subquery: one ``AnalysisRun.id`` per ``sonar_project_id`` — its most
    recent successful run. Unlike vulnerability scans (roughly one report per
    distinct image, rarely repeated), Sonar analysis repeats on the *same*
    repository across commits, and old runs/issues are never deleted — only
    a re-run of the *same* commit replaces its own rows (see
    ``modules/sonar/findings.py``). A global findings view without this
    scoping would pile up stale issues from every historical commit of every
    repository; scoping to "latest successful run per repo" is what makes
    "Findings" mean *current* state rather than *history*. The existing
    per-run drill-down (``/analysis-runs/{id}/issues``) is unaffected."""
    rn = func.row_number().over(
        partition_by=AnalysisRun.sonar_project_id, order_by=desc(AnalysisRun.started_at),
    )
    ranked = select(AnalysisRun.id, AnalysisRun.sonar_project_id, rn.label("rn")).where(
        AnalysisRun.status == "success"
    )
    if sonar_project_id is not None:
        ranked = ranked.where(AnalysisRun.sonar_project_id == sonar_project_id)
    ranked = ranked.subquery()
    return select(ranked.c.id).where(ranked.c.rn == 1)


@router.get("/issues",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_all_issues(
    session: Annotated[AsyncSession, Depends(get_session)],
    sonar_project_id: Annotated[int | None, Query(description="Narrow to one repository")] = None,
    severity: Annotated[str | None, Query(description="Comma-separated, e.g. BLOCKER,CRITICAL")] = None,
    type_: Annotated[str | None, Query(alias="type", description="Comma-separated, e.g. BUG,VULNERABILITY,CODE_SMELL")] = None,
    q: Annotated[str | None, Query(description="Free-text match against rule, message, or file")] = None,
    sort: Annotated[str, Query(description="severity | type | component | rule")] = "severity",
    order: Annotated[str, Query(description="asc | desc")] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SonarIssuePage:
    """Every open issue from each repository's *latest successful* analysis —
    the Code Quality section's global Findings view. Use
    ``/analysis-runs/{id}/issues`` for one specific historical run instead."""
    stmt = select(SonarIssue).where(
        SonarIssue.analysis_run_id.in_(_latest_successful_run_ids(sonar_project_id))
    )
    if severity:
        values = [s.strip().upper() for s in severity.split(",") if s.strip()]
        if values:
            stmt = stmt.where(SonarIssue.severity.in_(values))
    if type_:
        values = [t.strip().upper() for t in type_.split(",") if t.strip()]
        if values:
            stmt = stmt.where(SonarIssue.type.in_(values))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            SonarIssue.rule.ilike(like), SonarIssue.message.ilike(like), SonarIssue.component.ilike(like),
        ))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(_ordered_issues(stmt, sort, order).limit(limit).offset(offset))).scalars().all()
    return SonarIssuePage(items=list(rows), total=total)


@router.get("/hotspots",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_all_hotspots(
    session: Annotated[AsyncSession, Depends(get_session)],
    sonar_project_id: Annotated[int | None, Query(description="Narrow to one repository")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SonarHotspotPage:
    """Every security hotspot from each repository's *latest successful*
    analysis — same "current state, not history" scoping as ``/issues``."""
    stmt = select(SonarHotspot).where(
        SonarHotspot.analysis_run_id.in_(_latest_successful_run_ids(sonar_project_id))
    )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(_HOTSPOT_PROBABILITY_RANK, desc(SonarHotspot.creation_date))
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    return SonarHotspotPage(items=list(rows), total=total)


@router.get("/analysis-runs/{run_id}/issues",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_issues(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    severity: Annotated[str | None, Query(description="Comma-separated, e.g. BLOCKER,CRITICAL")] = None,
    type_: Annotated[str | None, Query(alias="type", description="Comma-separated, e.g. BUG,VULNERABILITY,CODE_SMELL")] = None,
    q: Annotated[str | None, Query(description="Free-text match against rule, message, or file")] = None,
    sort: Annotated[str, Query(description="severity | type | component | rule")] = "severity",
    order: Annotated[str, Query(description="asc | desc")] = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SonarIssuePage:
    """Every bug/vulnerability/code smell Sonar reported for this run —
    the detail behind ``AnalysisRun.issues_count``/``bugs``/``vulnerabilities``/``code_smells``."""
    await _run_or_404(session, run_id)
    stmt = select(SonarIssue).where(SonarIssue.analysis_run_id == run_id)
    if severity:
        values = [s.strip().upper() for s in severity.split(",") if s.strip()]
        if values:
            stmt = stmt.where(SonarIssue.severity.in_(values))
    if type_:
        values = [t.strip().upper() for t in type_.split(",") if t.strip()]
        if values:
            stmt = stmt.where(SonarIssue.type.in_(values))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            SonarIssue.rule.ilike(like), SonarIssue.message.ilike(like), SonarIssue.component.ilike(like),
        ))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(_ordered_issues(stmt, sort, order).limit(limit).offset(offset))).scalars().all()
    return SonarIssuePage(items=list(rows), total=total)


@router.get("/analysis-runs/{run_id}/hotspots",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_hotspots(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SonarHotspotPage:
    """Every security hotspot Sonar flagged for this run — the detail behind
    ``AnalysisRun.security_hotspots``."""
    await _run_or_404(session, run_id)
    stmt = select(SonarHotspot).where(SonarHotspot.analysis_run_id == run_id)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(_HOTSPOT_PROBABILITY_RANK, desc(SonarHotspot.creation_date))
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    return SonarHotspotPage(items=list(rows), total=total)


@router.get("/analysis-runs/{run_id}/report.pdf",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def download_analysis_report(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Full analysis report as a PDF — metadata, quality gate, metrics, and
    every issue/hotspot — same shape as the vulnerability-scan report export
    (``routers/scan/reports.py`` + ``services/scan_report_pdf.py``)."""
    run = await _run_or_404(session, run_id)
    sonar_project = await session.get(SonarProject, run.sonar_project_id)
    if sonar_project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _SONAR_PROJECT_NOT_FOUND)
    # Best-effort only — the "Suggested Fixes" section is a bonus, not a
    # requirement, so a missing/unreachable Sonar connection at export time
    # just means that section is skipped, not that the download 502s.
    client = None
    conn = await get_sonar_connection(session, settings)
    if conn.is_configured():
        client = SonarClient(conn.url, conn.token)
    pdf_bytes = await build_analysis_report_pdf(session, run, sonar_project, client)
    filename = f"sonar-analysis-{run.commit_sha[:8]}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Manual analysis — same job, same handler as the push webhook
# ---------------------------------------------------------------------------
@router.post("/repositories/{sonar_project_id}/run-analysis", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def run_repository_analysis(
    sonar_project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
    ref: str | None = None,
) -> dict:
    """Trigger analysis on demand for one repository — for troubleshooting,
    validating a new connection, or (via ``ref``) checking a branch other
    than the default without waiting for a push. Looks up the current HEAD
    of the branch, and enqueues the exact same ``clone_and_analyze`` job a
    push or an automatic on-connect run would.
    """
    sonar_project = await session.get(SonarProject, sonar_project_id)
    if sonar_project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _SONAR_PROJECT_NOT_FOUND)
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue is not available")

    source_module, provider, credential_ref, repo_ref, repo_ids = await _resolve_repo(session, settings, state, sonar_project)
    branch = ref or repo_ref.default_branch

    try:
        sha = await provider.get_latest_commit_sha(credential_ref, repo_ref, branch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to resolve latest commit for manual analysis on sonar project %s: %s",
                        sonar_project_id, exc)
        # Every provider call already raises a specific, actionable error
        # (GitHubProviderError/GitHubAuthError/GitLabProviderError — "App not
        # configured", "401 Bad credentials", "branch not found", ...).
        # Replacing it with one generic "verify the connection" sentence
        # threw away exactly the detail needed to fix it. Fall back to the
        # generic message only for an exception with no useful text of its
        # own (e.g. a bare httpx.ConnectTimeout).
        detail = str(exc).strip() or (
            f"Unable to reach {source_module.title()} to find the latest commit on {branch!r}. "
            "Verify the connection."
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail) from exc

    queue = JobQueue(state.cache)
    job_id = await queue.enqueue(
        "clone_and_analyze",
        {
            "project_id": sonar_project.project_id,
            "source_module": source_module,
            "credential_ref": credential_ref,
            "repo_external_id": repo_ref.external_id,
            "repo_name": repo_ref.name,
            "default_branch": repo_ref.default_branch,
            "ref": branch,
            "sha": sha,
            "trigger": "manual",
            "github_repository_id": repo_ids[0],
            "gitlab_repository_id": repo_ids[1],
        },
    )
    return {"job_id": job_id, "commit_sha": sha, "ref": branch}


@router.post("/projects/{project_id}/run-analysis", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def run_analysis(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict:
    """Back-compat convenience for the common single-repository Project: run
    analysis without having to know the Sonar project id. Only works when the
    Project has exactly one connected repository — with more than one, which
    repo to analyze is ambiguous, so use
    ``/repositories/{sonar_project_id}/run-analysis`` instead (the Project
    page's per-repository "Run Analysis" button uses that directly).
    """
    await projects_core.get_project(session, project_id)  # 404s if missing
    sonar_projects = (
        await session.execute(
            select(SonarProject).where(
                SonarProject.project_id == project_id,
                # Defense in depth against a SonarProject with no linked
                # repository (shouldn't exist after the 20260811 migration,
                # but a row like that has no valid repo to analyze — treat
                # it as if it isn't there rather than crashing on it).
                (SonarProject.github_repository_id.isnot(None)) | (SonarProject.gitlab_repository_id.isnot(None)),
            )
        )
    ).scalars().all()
    if not sonar_projects:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Project has no Sonar project configured")
    if len(sonar_projects) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This Project has more than one connected repository — run analysis for a specific one via "
            "POST /modules/sonar/repositories/{sonar_project_id}/run-analysis.",
        )
    return await run_repository_analysis(sonar_projects[0].id, session, settings, state, None)


class AnalyzeRequest(BaseModel):
    source_module: str = Field(..., description="'github' or 'gitlab'")
    repository_id: int
    branch: str | None = Field(default=None, description="Defaults to the repository's default branch")


@router.post("/analyze", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def analyze_repository(
    body: AnalyzeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict:
    """One action for the global Code Quality section: pick a repository,
    pick a branch, run analysis. Auto-provisions a ``SonarProject`` first if
    this repository doesn't have one yet — detecting a supported language the
    same way automatic on-connect provisioning does
    (``modules/sonar/provisioning.py:auto_provision_and_analyze``) — so the
    "Connect Sonar" step the per-Project Repositories tab still exposes isn't
    a prerequisite here. Enqueues the exact same ``clone_and_analyze`` job
    every other analysis trigger does.
    """
    if body.source_module not in ("github", "gitlab"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "source_module must be 'github' or 'gitlab'")
    github_repository_id = body.repository_id if body.source_module == "github" else None
    gitlab_repository_id = body.repository_id if body.source_module == "gitlab" else None

    source_module, provider, credential_ref, repo_ref, repo_ids, project_id = await _resolve_repo_by_ids(
        session, settings, state, github_repository_id, gitlab_repository_id,
    )
    if project_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This repository isn't mapped to a Project yet — connect it from a Project's "
            "Repositories tab first, then it can be analyzed from Code Quality.",
        )

    sonar_project = await session.scalar(
        select(SonarProject).where(
            SonarProject.github_repository_id == github_repository_id
            if github_repository_id else SonarProject.gitlab_repository_id == gitlab_repository_id
        )
    )
    if sonar_project is None:
        try:
            languages = await provider.get_repository_languages(credential_ref, repo_ref)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Unable to reach {source_module.title()} to detect {repo_ref.external_id}'s language: {exc}",
            ) from exc
        language = pick_supported_language(languages)
        if language is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{repo_ref.external_id} has no auto-detectable supported language "
                f"({', '.join(SUPPORTED_LANGUAGES)}) — it can't be analyzed without a build step.",
            )
        try:
            sonar_project = await create_sonar_project_row(
                session, settings, project_id, repo_ref.external_id, language,
                github_repository_id=github_repository_id, gitlab_repository_id=gitlab_repository_id,
            )
        except SonarError as exc:
            if "not configured" in str(exc):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "SonarQube is not configured. Set it up in Settings -> Integrations -> SonarQube first.",
                ) from exc
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, _UNREACHABLE_MESSAGE) from exc

    branch = body.branch or repo_ref.default_branch
    try:
        sha = await provider.get_latest_commit_sha(credential_ref, repo_ref, branch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to resolve latest commit for %s@%s: %s", repo_ref.external_id, branch, exc)
        detail = str(exc).strip() or (
            f"Unable to reach {source_module.title()} to find the latest commit on {branch!r}. Verify the connection."
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail) from exc

    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue is not available")
    queue = JobQueue(state.cache)
    job_id = await queue.enqueue(
        "clone_and_analyze",
        {
            "project_id": project_id,
            "source_module": source_module,
            "credential_ref": credential_ref,
            "repo_external_id": repo_ref.external_id,
            "repo_name": repo_ref.name,
            "default_branch": repo_ref.default_branch,
            "ref": branch,
            "sha": sha,
            "trigger": "manual",
            "github_repository_id": repo_ids[0],
            "gitlab_repository_id": repo_ids[1],
        },
    )
    return {"job_id": job_id, "commit_sha": sha, "ref": branch, "sonar_project_id": sonar_project.id}


async def _resolve_repo(session: AsyncSession, settings: Settings, state: AppState, sonar_project: SonarProject):
    """Provider/credential/RepoRef for whichever repository a SonarProject
    belongs to, plus its (github_repository_id, gitlab_repository_id) pair
    for re-threading into the job payload."""
    (source_module, provider, credential_ref, repo_ref, repo_ids, _project_id) = await _resolve_repo_by_ids(
        session, settings, state, sonar_project.github_repository_id, sonar_project.gitlab_repository_id,
    )
    return source_module, provider, credential_ref, repo_ref, repo_ids


async def _resolve_repo_by_ids(
    session: AsyncSession, settings: Settings, state: AppState,
    github_repository_id: int | None, gitlab_repository_id: int | None,
):
    """Lower-level version of :func:`_resolve_repo`, identified directly by
    a (github_repository_id, gitlab_repository_id) pair rather than an
    existing ``SonarProject`` — what ``/analyze`` uses, since a repository
    picked from the global Code Quality section may not have a
    ``SonarProject`` yet. Also returns the repository's ``project_id`` (the
    Rotsy Project it's mapped to), needed to provision one.
    """
    if github_repository_id:
        github_repo = await session.get(GitHubRepository, github_repository_id)
        if github_repo is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Connected GitHub repository is missing")
        # installation_id is NULL for a repo connected by URL (public, no App
        # installation — see connect_public_repository) — expected, not an
        # error; credential_ref="" tells GitHubProvider to act anonymously.
        installation = (
            await session.get(GitHubInstallation, github_repo.installation_id)
            if github_repo.installation_id else None
        )
        if github_repo.installation_id and installation is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "GitHub installation for this repository is missing")
        if state.cache is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")
        github_app_config = await get_github_app_config(session, settings)
        provider = GitHubProvider(github_app_config, state.cache)
        repo_ref = RepoRef(external_id=github_repo.full_name, name=github_repo.full_name.rsplit("/", 1)[-1],
                            default_branch=github_repo.default_branch, private=installation is not None)
        credential_ref = str(installation.installation_id) if installation else ""
        return "github", provider, credential_ref, repo_ref, (github_repo.id, None), github_repo.project_id

    if gitlab_repository_id:
        gitlab_repo = await session.get(GitLabRepository, gitlab_repository_id)
        if gitlab_repo is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Connected GitLab repository is missing")
        provider = GitLabProvider(get_session_factory())
        repo_ref = RepoRef(external_id=gitlab_repo.full_path, name=gitlab_repo.full_path.rsplit("/", 1)[-1],
                            default_branch=gitlab_repo.default_branch, private=True)
        return "gitlab", provider, str(gitlab_repo.id), repo_ref, (None, gitlab_repo.id), gitlab_repo.project_id

    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                         "Repository is missing both a GitHub and GitLab id")
