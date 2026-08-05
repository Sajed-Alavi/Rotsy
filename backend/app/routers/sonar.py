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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..core.config_store import (
    get_sonar_connection,
    get_sonar_last_success,
    record_sonar_success,
    save_sonar_connection,
    sonar_connection_masked,
)
from ..core.jobs import JobQueue
from ..core.source_provider import RepoRef
from ..dependencies import RequirePermission, get_session, get_settings
from ..models import AnalysisRun, GitHubInstallation, GitHubRepository, QualityGateResult, SonarProject
from ..models.sonar import SUPPORTED_LANGUAGES
from ..modules.github.provider import GitHubProvider
from ..modules.sonar.connector import SonarClient, SonarError
from ..schemas.sonar import (
    AnalysisRunOut,
    QualityGateResultOut,
    SonarProjectCreate,
    SonarProjectOut,
)
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
            f"Supported for MVP: {', '.join(SUPPORTED_LANGUAGES)}.",
        )
    project = await projects_core.get_project(session, body.project_id)

    existing = await session.scalar(
        select(SonarProject).where(SonarProject.project_id == body.project_id)
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Project already has a Sonar project")

    conn = await get_sonar_connection(session, settings)
    if not conn.is_configured():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SonarQube is not configured. Set it up in Settings -> Integrations -> SonarQube first.",
        )

    sonar_project_key = f"rotsy-{project.id}-{project.name}".lower().replace(" ", "-")

    try:
        client = SonarClient(conn.url, conn.token)
        await client.ensure_project(sonar_project_key, project.name)
    except SonarError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _UNREACHABLE_MESSAGE) from exc

    row = SonarProject(project_id=project.id, sonar_project_key=sonar_project_key, language=body.language)
    session.add(row)

    await projects_core.connect_integration(
        session, project.id, "sonar", "analysis_engine", config={"language": body.language}, credential_ref=None,
    )
    await session.commit()
    await session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Analysis history / detail
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/analysis-runs", response_model=list[AnalysisRunOut],
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_analysis_runs(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AnalysisRun]:
    sonar_project = await session.scalar(select(SonarProject).where(SonarProject.project_id == project_id))
    if sonar_project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project has no Sonar project configured")
    rows = (
        await session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.sonar_project_id == sonar_project.id)
            .order_by(desc(AnalysisRun.started_at))
            .limit(50)
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
# Manual analysis — same job, same handler as the push webhook
# ---------------------------------------------------------------------------
@router.post("/projects/{project_id}/run-analysis", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def run_analysis(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict:
    """Trigger analysis on demand — for troubleshooting or validating a new
    connection, without waiting for a push. Resolves the project's mapped
    GitHub repository, looks up the current HEAD of its default branch, and
    enqueues the exact same ``clone_and_analyze`` job the webhook enqueues.
    """
    await projects_core.get_project(session, project_id)  # 404s if missing

    sonar_project = await session.scalar(select(SonarProject).where(SonarProject.project_id == project_id))
    if sonar_project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Project has no Sonar project configured")

    github_repo = await session.scalar(select(GitHubRepository).where(GitHubRepository.project_id == project_id))
    if github_repo is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Project has no GitHub repository mapped")
    installation = await session.get(GitHubInstallation, github_repo.installation_id)
    if installation is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "GitHub installation for this repository is missing")

    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue is not available")

    provider = GitHubProvider(settings, state.cache)
    repo_ref = RepoRef(external_id=github_repo.full_name, name=github_repo.full_name.split("/")[-1],
                        default_branch=github_repo.default_branch, private=True)
    try:
        sha = await provider.get_latest_commit_sha(
            str(installation.installation_id), repo_ref, github_repo.default_branch,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to resolve latest commit for manual analysis on project %s: %s", project_id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Unable to reach GitHub to find the latest commit. Verify the GitHub App installation.",
        ) from exc

    queue = JobQueue(state.cache)
    job_id = await queue.enqueue(
        "clone_and_analyze",
        {
            "project_id": project_id,
            "github_repository_id": github_repo.id,
            "repo_full_name": github_repo.full_name,
            "ref": github_repo.default_branch,
            "sha": sha,
            "trigger": "manual",
        },
    )
    return {"job_id": job_id, "commit_sha": sha}
