"""Project + Integration core logic.

Vendor-agnostic on purpose: this module knows how to create a project and
attach an integration row to it, but nothing about what a GitHub repository
or a Sonar project key look like. ``module_key`` is validated against the
registry in :mod:`.integrations`, not against a hardcoded list.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration, Project
from .integrations import is_registered


async def create_project(session: AsyncSession, name: str) -> Project:
    project = Project(name=name)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def get_project(session: AsyncSession, project_id: int) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def list_projects(session: AsyncSession) -> list[Project]:
    rows = (await session.execute(select(Project).order_by(Project.name))).scalars().all()
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
