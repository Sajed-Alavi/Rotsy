"""Project + integration endpoints.

Thin HTTP layer only — all logic lives in ``core/projects.py``. This router
knows about ``Project``/``Integration``, never about GitHub, GitLab, Sonar or
Nexus specifics; a module-specific router (e.g. ``routers/github.py``) calls
into ``core.projects.connect_integration`` the same way this one does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import projects as projects_core
from ..core.health import compute_health_score
from ..core.integrations import get_module
from ..dependencies import RequirePermission, get_session
from ..models import GitHubRepository, GitLabRepository, Insight, SonarProject
from ..schemas.project import (
    HealthScoreOut,
    InsightOut,
    IntegrationConnect,
    IntegrationOut,
    ProjectCreate,
    ProjectOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProjectOut]:
    return await projects_core.list_projects(session)


@router.post("", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def create_project(
    body: ProjectCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectOut:
    return await projects_core.create_project(session, body.name)


@router.get("/{project_id}",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def get_project(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectOut:
    return await projects_core.get_project(session, project_id)


@router.delete("/{project_id}", status_code=204,
                dependencies=[Depends(RequirePermission("projects:write"))])
async def delete_project(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await projects_core.delete_project(session, project_id)


@router.get("/{project_id}/integrations",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_integrations(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[IntegrationOut]:
    return await projects_core.list_integrations(session, project_id)


@router.post("/{project_id}/integrations", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def connect_integration(
    project_id: int,
    body: IntegrationConnect,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IntegrationOut:
    # connect_integration 400s if the key isn't registered; when it is, the
    # manifest is always present, so this default is never actually used.
    manifest = get_module(body.module_key)
    kind = manifest.kind if manifest else "source"
    return await projects_core.connect_integration(
        session, project_id, body.module_key, kind, body.config, body.credential_ref
    )


@router.get("/{project_id}/insights", response_model=list[InsightOut],
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_insights(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Insight]:
    await projects_core.get_project(session, project_id)  # 404s if missing
    rows = (
        await session.execute(
            select(Insight)
            .where(Insight.project_id == project_id)
            .order_by(desc(Insight.created_at))
            .limit(100)
        )
    ).scalars().all()
    return list(rows)


@router.get("/{project_id}/repositories", dependencies=[Depends(RequirePermission("projects:read"))])
async def list_project_repositories(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Every repository connected to this Project — a Project is a grouping,
    so this can be one repo or a thousand, each independently analyzed and
    potentially in a different language. The natural data source for the
    Projects section's repository list/organization view.
    """
    await projects_core.get_project(session, project_id)  # 404s if missing

    sonar_by_github: dict[int, SonarProject] = {}
    sonar_by_gitlab: dict[int, SonarProject] = {}
    for sp in (await session.execute(select(SonarProject).where(SonarProject.project_id == project_id))).scalars():
        if sp.github_repository_id:
            sonar_by_github[sp.github_repository_id] = sp
        if sp.gitlab_repository_id:
            sonar_by_gitlab[sp.gitlab_repository_id] = sp

    out: list[dict] = []
    github_repos = (
        await session.execute(select(GitHubRepository).where(GitHubRepository.project_id == project_id))
    ).scalars().all()
    for r in github_repos:
        sp = sonar_by_github.get(r.id)
        out.append({
            "source_module": "github",
            "repository_id": r.id,
            "full_name": r.full_name,
            "default_branch": r.default_branch,
            # A repo needs both the delivery mechanism (App installation)
            # and the per-repository toggle on to actually auto-analyze.
            "auto_analyze_on_push": r.installation_id is not None and (sp is None or sp.auto_analyze_enabled),
            "sonar_project_id": sp.id if sp else None,
            "language": sp.language if sp else None,
            "auto_analyze_enabled": sp.auto_analyze_enabled if sp else None,
            "auto_analyze_branches": sp.auto_analyze_branches if sp else None,
            "created_at": r.created_at,
        })

    gitlab_repos = (
        await session.execute(select(GitLabRepository).where(GitLabRepository.project_id == project_id))
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
            "created_at": r.created_at,
        })

    out.sort(key=lambda row: row["full_name"])
    return out


@router.get("/{project_id}/health",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def get_health_score(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthScoreOut:
    """0-100 deterministic health score — see core/health.py for the formula.
    Higher is better; 0 with ``has_data=False`` means "nothing scored yet",
    not "this project is unhealthy"."""
    await projects_core.get_project(session, project_id)  # 404s if missing
    result = await compute_health_score(session, project_id)
    return HealthScoreOut(score=result.score, factors=result.factors, has_data=result.has_data)
