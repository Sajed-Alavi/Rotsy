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
    AnalysisRun, GitHubInstallation, GitHubRepository, GitLabRepository, QualityGateResult,
    SonarHotspot, SonarIssue, SonarProject,
)
from ..models.sonar import SUPPORTED_LANGUAGES
from ..modules.github.provider import GitHubProvider
from ..modules.gitlab.provider import GitLabProvider
from ..modules.sonar.connector import SonarClient, SonarError
from ..modules.sonar.provisioning import create_sonar_project_row
from ..schemas.sonar import (
    AnalysisRunOut,
    QualityGateResultOut,
    SonarHotspotPage,
    SonarIssuePage,
    SonarProjectCreate,
    SonarProjectOut,
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
    if not url.startswith(("http://", "https://")):
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


# ---------------------------------------------------------------------------
# Project mapping
# ---------------------------------------------------------------------------
@router.post("/projects", response_model=SonarProjectOut, status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def create_sonar_project(
    body: SonarProjectCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SonarProjectOut:
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

    project = await projects_core.get_project(session, body.project_id)

    if body.github_repository_id:
        repo = await session.get(GitHubRepository, body.github_repository_id)
        repo_label = repo.full_name if repo else None
    else:
        repo = await session.get(GitLabRepository, body.gitlab_repository_id)
        repo_label = repo.full_path if repo else None
    if repo is None or repo.project_id != body.project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That repository is not connected to this Project.")

    existing = await session.scalar(
        select(SonarProject).where(
            SonarProject.github_repository_id == body.github_repository_id
            if body.github_repository_id else SonarProject.gitlab_repository_id == body.gitlab_repository_id
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{repo_label} already has a Sonar project")

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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sonar project not found")
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


@router.get("/analysis-runs/{run_id}/issues", response_model=SonarIssuePage,
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


@router.get("/analysis-runs/{run_id}/hotspots", response_model=SonarHotspotPage,
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
            stmt.order_by(desc(SonarHotspot.vulnerability_probability), desc(SonarHotspot.creation_date))
            .limit(limit).offset(offset)
        )
    ).scalars().all()
    return SonarHotspotPage(items=list(rows), total=total)


@router.get("/analysis-runs/{run_id}/report.pdf",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def download_analysis_report(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Full analysis report as a PDF — metadata, quality gate, metrics, and
    every issue/hotspot — same shape as the vulnerability-scan report export
    (``routers/scan/reports.py`` + ``services/scan_report_pdf.py``)."""
    run = await _run_or_404(session, run_id)
    sonar_project = await session.get(SonarProject, run.sonar_project_id)
    if sonar_project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sonar project not found")
    pdf_bytes = await build_analysis_report_pdf(session, run, sonar_project)
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sonar project not found")
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


async def _resolve_repo(session: AsyncSession, settings: Settings, state: AppState, sonar_project: SonarProject):
    """Provider/credential/RepoRef for whichever repository a SonarProject
    belongs to, plus its (github_repository_id, gitlab_repository_id) pair
    for re-threading into the job payload."""
    if sonar_project.github_repository_id:
        github_repo = await session.get(GitHubRepository, sonar_project.github_repository_id)
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
        return "github", provider, credential_ref, repo_ref, (github_repo.id, None)

    if sonar_project.gitlab_repository_id:
        gitlab_repo = await session.get(GitLabRepository, sonar_project.gitlab_repository_id)
        if gitlab_repo is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Connected GitLab repository is missing")
        provider = GitLabProvider(get_session_factory())
        repo_ref = RepoRef(external_id=gitlab_repo.full_path, name=gitlab_repo.full_path.rsplit("/", 1)[-1],
                            default_branch=gitlab_repo.default_branch, private=True)
        return "gitlab", provider, str(gitlab_repo.id), repo_ref, (None, gitlab_repo.id)

    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                         "Sonar project has no linked GitHub or GitLab repository")
