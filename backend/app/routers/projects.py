"""Project + integration endpoints.

Thin HTTP layer only — all logic lives in ``core/projects.py``. This router
knows about ``Project``/``Integration``, never about GitHub, GitLab, Sonar or
Nexus specifics; a module-specific router (e.g. ``routers/github.py``) calls
into ``core.projects.connect_integration`` the same way this one does.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..core.config_store import get_github_app_config
from ..core.health import compute_health_score
from ..core.integrations import get_module
from ..core.project_access import require_project_access
from ..dependencies import RequirePermission, get_current_user, get_session, get_settings
from ..models import GitHubRepository, GitLabRepository, Insight, SonarProject, User
from ..schemas.project import (
    HealthScoreOut,
    InsightOut,
    IntegrationConnect,
    IntegrationOut,
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberOut,
    ProjectMemberUpdate,
    ProjectOut,
    UserCandidateOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[ProjectOut]:
    return await projects_core.list_projects(session, user)


@router.post("", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def create_project(
    body: ProjectCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> ProjectOut:
    return await projects_core.create_project(session, body.name, user)


@router.get("/{project_id}",
            dependencies=[Depends(RequirePermission("projects:read")), Depends(require_project_access("viewer"))])
async def get_project(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectOut:
    return await projects_core.get_project(session, project_id)


@router.delete("/{project_id}", status_code=204,
                dependencies=[Depends(RequirePermission("projects:write")), Depends(require_project_access("admin"))])
async def delete_project(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await projects_core.delete_project(session, project_id)


@router.get("/{project_id}/integrations",
            dependencies=[Depends(RequirePermission("projects:read")), Depends(require_project_access("viewer"))])
async def list_integrations(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[IntegrationOut]:
    return await projects_core.list_integrations(session, project_id)


@router.post("/{project_id}/integrations", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write")), Depends(require_project_access("member"))])
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
            dependencies=[Depends(RequirePermission("projects:read")), Depends(require_project_access("viewer"))])
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


@router.get("/{project_id}/members", response_model=list[ProjectMemberOut],
            dependencies=[Depends(RequirePermission("projects:read")), Depends(require_project_access("viewer"))])
async def list_members(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    return await projects_core.list_members(session, project_id)


@router.get("/{project_id}/members/candidates", response_model=list[UserCandidateOut],
            dependencies=[Depends(RequirePermission("projects:read")), Depends(require_project_access("admin"))])
async def member_candidates(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = None,
) -> list[User]:
    return await projects_core.search_member_candidates(session, project_id, q)


@router.post("/{project_id}/members", status_code=201, response_model=ProjectMemberOut,
             dependencies=[Depends(RequirePermission("projects:write")), Depends(require_project_access("admin"))])
async def add_member(
    project_id: int,
    body: ProjectMemberCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    return await projects_core.add_member(session, project_id, body.user_id, body.project_role)


@router.patch("/{project_id}/members/{member_id}", response_model=ProjectMemberOut,
              dependencies=[Depends(RequirePermission("projects:write")), Depends(require_project_access("admin"))])
async def update_member(
    project_id: int,
    member_id: int,
    body: ProjectMemberUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    return await projects_core.update_member_role(session, project_id, member_id, body.project_role)


@router.delete("/{project_id}/members/{member_id}", status_code=204,
               dependencies=[Depends(RequirePermission("projects:write")), Depends(require_project_access("admin"))])
async def remove_member(
    project_id: int,
    member_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await projects_core.remove_member(session, project_id, member_id)


def _connected_repo_row(
    source_module: str, repo_id: int, full_name: str, default_branch: str,
    has_delivery_mechanism: bool, sp: SonarProject | None, created_at,
) -> dict[str, Any]:
    return {
        "source_module": source_module,
        "repository_id": repo_id,
        "full_name": full_name,
        "default_branch": default_branch,
        # A repo needs both the delivery mechanism (App installation /
        # webhook) and the per-repository toggle on to actually auto-analyze.
        "auto_analyze_on_push": has_delivery_mechanism and (sp is None or sp.auto_analyze_enabled),
        # Exposed separately from the combined flag above so the UI can tell
        # "never turned on" apart from "turned on, but the webhook that would
        # actually deliver the push isn't there" — those look identical
        # folded into one boolean, and only one of them is fixed by flipping
        # the toggle again.
        "webhook_registered": has_delivery_mechanism,
        "sonar_project_id": sp.id if sp else None,
        "language": sp.language if sp else None,
        "auto_analyze_enabled": sp.auto_analyze_enabled if sp else None,
        "auto_analyze_branches": sp.auto_analyze_branches if sp else None,
        "quality_gate_preset": sp.quality_gate_preset if sp else None,
        "created_at": created_at,
    }


@router.get("/{project_id}/repositories",
            dependencies=[Depends(RequirePermission("projects:read")), Depends(require_project_access("viewer"))])
async def list_project_repositories(
    project_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
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

    # App-wide, not per-repository: an installed repo with no App-level
    # webhook (the App Manifest flow was completed without one — an
    # unreachable-from-GitHub WEBHOOK_BASE_URL at the time makes this the
    # default, silently) will never actually receive a push event, no
    # matter how "installed" it looks. Checked once, not per row.
    github_has_webhook = (await get_github_app_config(session, settings)).has_webhook()

    out: list[dict] = []
    github_repos = (
        await session.execute(select(GitHubRepository).where(GitHubRepository.project_id == project_id))
    ).scalars().all()
    for r in github_repos:
        sp = sonar_by_github.get(r.id)
        out.append(_connected_repo_row(
            "github", r.id, r.full_name, r.default_branch,
            r.installation_id is not None and github_has_webhook, sp, r.created_at,
        ))

    gitlab_repos = (
        await session.execute(select(GitLabRepository).where(GitLabRepository.project_id == project_id))
    ).scalars().all()
    for r in gitlab_repos:
        sp = sonar_by_gitlab.get(r.id)
        out.append(_connected_repo_row(
            "gitlab", r.id, r.full_path, r.default_branch, r.webhook_id is not None, sp, r.created_at,
        ))

    out.sort(key=lambda row: row["full_name"])
    return out


@router.get("/{project_id}/health",
            dependencies=[Depends(RequirePermission("projects:read")), Depends(require_project_access("viewer"))])
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
