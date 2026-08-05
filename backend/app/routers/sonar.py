"""Sonar project setup + read endpoints for analysis runs / quality gates.

Connecting a project to Sonar is explicit (this router), separate from the
push-triggered analysis flow (``workers/analysis_worker.py``): the language
allowlist has to be picked once by a human, since Rotsy does not attempt to
auto-detect a build-free-safe language from repo contents.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..dependencies import RequirePermission, get_session, get_settings
from ..models import AnalysisRun, QualityGateResult, SonarProject
from ..models.sonar import SUPPORTED_LANGUAGES
from ..modules.sonar.connector import SonarClient, SonarError
from ..schemas.sonar import (
    AnalysisRunOut,
    QualityGateResultOut,
    SonarProjectCreate,
    SonarProjectOut,
)

router = APIRouter(prefix="/modules/sonar", tags=["sonar"])
logger = logging.getLogger(__name__)


@router.get("/status", dependencies=[Depends(RequirePermission("projects:read"))])
async def sonar_status(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    """Connection status for the Settings -> Integrations -> SonarQube card.

    Never returns the token itself — only whether one is configured. Reused
    by both the card's summary state and its "Test Connection" action, so
    there is exactly one code path that talks to Sonar for a health check.
    """
    configured = bool(settings.SONAR_URL and settings.SONAR_ADMIN_TOKEN)
    if not configured:
        return {"configured": False, "reachable": False, "version": None, "server_url": None, "error": None}

    server_url = settings.SONAR_URL
    try:
        client = SonarClient(settings)
        info = await client.server_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sonar status check failed for %s: %s", server_url, exc)
        return {"configured": True, "reachable": False, "version": None, "server_url": server_url,
                "error": "Unable to connect to SonarQube. Verify the server URL, token, and network connectivity."}

    status_value = info.get("status", "")
    return {
        "configured": True,
        "reachable": status_value == "UP",
        "version": info.get("version"),
        "server_url": server_url,
        "error": None if status_value == "UP" else f"SonarQube reports status {status_value or 'UNKNOWN'}.",
    }


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

    sonar_project_key = f"rotsy-{project.id}-{project.name}".lower().replace(" ", "-")

    try:
        client = SonarClient(settings)
        await client.ensure_project(sonar_project_key, project.name)
    except SonarError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    row = SonarProject(project_id=project.id, sonar_project_key=sonar_project_key, language=body.language)
    session.add(row)

    await projects_core.connect_integration(
        session, project.id, "sonar", "analysis_engine", config={"language": body.language}, credential_ref=None,
    )
    await session.commit()
    await session.refresh(row)
    return row


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
