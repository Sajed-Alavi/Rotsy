"""Per-project access control.

The base RBAC system (:mod:`app.dependencies`) only answers "does this user
hold permission key X *anywhere*" — ``projects:read``/``projects:write`` say
nothing about *which* projects. This module supplies that dimension via
:class:`~app.models.project_member.ProjectMember`.

Three project-scoped roles, in escalating order:

    viewer  — see the project, its integrations, insights, health, repos
    member  — the above, plus connect/disconnect integrations
    admin   — the above, plus manage membership and delete the project

A user with **no** membership row for a project has no access to it at all,
regardless of what global permissions they hold — the same "closed by
default" posture :mod:`app.core.access_control` takes for repositories.

The global ``admin`` role bypasses membership entirely, mirroring why
``Role.access_mode`` pins the seeded admin role ``unrestricted``: an
administrator locked out of a project would have no way back in through the
app.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..dependencies import get_current_user
from ..models import Project, ProjectMember, User

PROJECT_ROLES: tuple[str, ...] = ("viewer", "member", "admin")
_ROLE_RANK: dict[str, int] = {role: i for i, role in enumerate(PROJECT_ROLES)}


def is_valid_project_role(role: str) -> bool:
    return role in _ROLE_RANK


def meets(role: str, minimum: str) -> bool:
    """Whether ``role`` is at least as privileged as ``minimum``."""
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK[minimum]


def is_global_admin(user: User) -> bool:
    """Whether ``user`` holds the seeded, pinned-unrestricted ``admin`` role."""
    return any(role.name == "admin" for role in user.roles)


async def get_membership(session: AsyncSession, project_id: int, user_id: int) -> ProjectMember | None:
    return (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def accessible_project_ids(session: AsyncSession, user: User) -> set[int] | None:
    """Project IDs ``user`` may see, or ``None`` meaning "all" (global admin)."""
    if is_global_admin(user):
        return None
    rows = (
        await session.execute(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id))
    ).scalars().all()
    return set(rows)


async def assert_project_access(
    session: AsyncSession, user: User, project_id: int, min_role: str = "viewer"
) -> ProjectMember | None:
    """403s unless ``user`` is a member of ``project_id`` at ``min_role`` or
    holds the global ``admin`` bypass. Does **not** check the project itself
    exists — callers that need a 404 for a missing project already get one
    from :func:`app.core.projects.get_project`, called alongside this.

    Used both by :func:`require_project_access` (path-param routes) and
    directly by routers where the target project comes from the request body
    instead of the URL (repository-mapping endpoints in ``routers/github.py``,
    ``routers/gitlab.py``, ``routers/sonar.py``) — those still need this check
    or a user could attach someone else's repository to a project they have
    no access to, which would defeat project-scoped access entirely.
    """
    if is_global_admin(user):
        return await get_membership(session, project_id, user.id)

    membership = await get_membership(session, project_id, user.id)
    if membership is None or not meets(membership.project_role, min_role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this project.")
    return membership


def require_project_access(min_role: str = "viewer"):
    """Dependency factory: 404s if the project doesn't exist, 403s if the
    current user isn't a member at ``min_role`` (global admins bypass).

    ``project_id`` is taken straight from the path — FastAPI fills any
    dependency parameter whose name matches a path parameter of the route it
    is used on.
    """
    if not is_valid_project_role(min_role):
        raise ValueError(f"Unknown project role: {min_role!r}")

    async def _dependency(
        project_id: int,
        user: Annotated[User, Depends(get_current_user)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> ProjectMember | None:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        return await assert_project_access(session, user, project_id, min_role)

    return _dependency
