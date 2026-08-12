"""Project + Integration core logic.

Vendor-agnostic on purpose: this module knows how to create a project and
attach an integration row to it, but nothing about what a GitHub repository
or a Sonar project key look like. ``module_key`` is validated against the
registry in :mod:`.integrations`, not against a hardcoded list.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration, Project, ProjectMember, User
from .integrations import is_registered
from .project_access import accessible_project_ids, get_membership


async def create_project(session: AsyncSession, name: str, creator: User) -> Project:
    project = Project(name=name)
    session.add(project)
    await session.flush()  # assigns project.id without ending the transaction
    session.add(ProjectMember(project_id=project.id, user_id=creator.id, project_role="admin"))
    await session.commit()
    await session.refresh(project)
    return project


async def get_project(session: AsyncSession, project_id: int) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def list_projects(session: AsyncSession, user: User) -> list[Project]:
    ids = await accessible_project_ids(session, user)
    stmt = select(Project).order_by(Project.name)
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(Project.id.in_(ids))
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def delete_project(session: AsyncSession, project_id: int) -> None:
    project = await get_project(session, project_id)
    await session.delete(project)
    await session.commit()


async def connect_integration(
    session: AsyncSession,
    project_id: int,
    module_key: str,
    kind: str,
    config: dict,
    credential_ref: str | None,
) -> Integration:
    await get_project(session, project_id)  # 404s if missing

    if not is_registered(module_key):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown module {module_key!r} — no module has registered this key",
        )

    existing = (
        await session.execute(
            select(Integration).where(
                Integration.project_id == project_id,
                Integration.module_key == module_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Project already has a {module_key!r} integration",
        )

    integration = Integration(
        project_id=project_id,
        module_key=module_key,
        kind=kind,
        config=config,
        credential_ref=credential_ref,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


async def list_integrations(session: AsyncSession, project_id: int) -> list[Integration]:
    await get_project(session, project_id)  # 404s if missing
    rows = (
        await session.execute(select(Integration).where(Integration.project_id == project_id))
    ).scalars().all()
    return list(rows)


# --- project membership ------------------------------------------------------
def _member_row(member: ProjectMember, user: User) -> dict:
    return {
        "id": member.id,
        "project_id": member.project_id,
        "user_id": member.user_id,
        "username": user.username,
        "email": user.email,
        "project_role": member.project_role,
        "created_at": member.created_at,
    }


async def list_members(session: AsyncSession, project_id: int) -> list[dict]:
    await get_project(session, project_id)  # 404s if missing
    rows = (
        await session.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(User.username)
        )
    ).all()
    return [_member_row(member, user) for member, user in rows]


async def search_member_candidates(session: AsyncSession, project_id: int, q: str | None) -> list[User]:
    """Users addable to a project — a lighter query than the global user list,
    so a project-admin can grant access without holding ``users:manage``."""
    await get_project(session, project_id)  # 404s if missing
    stmt = select(User).where(User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.email.ilike(like)))
    stmt = stmt.order_by(User.username).limit(20)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def _get_member_or_404(session: AsyncSession, project_id: int, member_id: int) -> ProjectMember:
    member = await session.get(ProjectMember, member_id)
    if member is None or member.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return member


async def _assert_not_last_admin(session: AsyncSession, project_id: int, exclude_member_id: int) -> None:
    """Refuse an operation that would leave a project with zero admins — the
    project-scoped equivalent of the global admin role being pinned
    unrestricted: someone has to always be able to manage the project."""
    remaining = (
        await session.execute(
            select(func.count()).select_from(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.project_role == "admin",
                ProjectMember.id != exclude_member_id,
            )
        )
    ).scalar_one()
    if remaining == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot remove the last admin of a project.")


async def add_member(session: AsyncSession, project_id: int, user_id: int, project_role: str) -> dict:
    await get_project(session, project_id)  # 404s if missing
    target_user = await session.get(User, user_id)
    if target_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if await get_membership(session, project_id, user_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member of this project")

    member = ProjectMember(project_id=project_id, user_id=user_id, project_role=project_role)
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return _member_row(member, target_user)


async def update_member_role(session: AsyncSession, project_id: int, member_id: int, project_role: str) -> dict:
    member = await _get_member_or_404(session, project_id, member_id)
    if member.project_role == "admin" and project_role != "admin":
        await _assert_not_last_admin(session, project_id, exclude_member_id=member.id)
    member.project_role = project_role
    await session.commit()
    await session.refresh(member)
    user = await session.get(User, member.user_id)
    return _member_row(member, user)


async def remove_member(session: AsyncSession, project_id: int, member_id: int) -> None:
    member = await _get_member_or_404(session, project_id, member_id)
    if member.project_role == "admin":
        await _assert_not_last_admin(session, project_id, exclude_member_id=member.id)
    await session.delete(member)
    await session.commit()
