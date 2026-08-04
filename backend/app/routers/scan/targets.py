"""Scan targets: per-repository opt-in to scanning."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.access_control import SCAN, AccessResolver
from ...dependencies import RequirePermission, get_access, get_session
from ...models import ScanTarget
from ...schemas.scan import TargetCreate, TargetOut, TargetUpdate

router = APIRouter()


def _require_repo_wide(access: AccessResolver, repo: str) -> None:
    """Refuse unless the caller may scan every image in ``repo``.

    A scan target is repository-wide configuration: it decides what happens to
    images that do not exist yet. Someone scoped to ``abrisham*`` must not be
    able to switch scanning on for the whole repository.
    """
    if not access.repo(repo).covers_all(SCAN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Configuring scanning for '{repo}' requires access to every image in it.",
        )


@router.get("/targets", response_model=list[TargetOut],
            dependencies=[Depends(RequirePermission("scan:read"))])
async def list_targets(
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    rows = (await session.execute(select(ScanTarget).order_by(ScanTarget.repo))).scalars().all()
    return [row for row in rows if access.repo(row.repo).visible]


@router.post("/targets", response_model=TargetOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("scan:execute"))])
async def create_target(
    body: TargetCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    """Enable scanning for a repository.

    Enabling does **not** scan what is already there: the repository's existing
    images are adopted as a baseline on first observation and left alone. Scan
    them individually if you want them covered.
    """
    _require_repo_wide(access, body.repo)
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
async def update_target(
    target_id: int,
    body: TargetUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    target = await session.get(ScanTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scan target not found")
    _require_repo_wide(access, target.repo)
    fields = body.model_dump(exclude_unset=True)
    # Moving a target to another repository needs authority over the destination
    # too, or it would be a way to enable scanning somewhere you cannot reach.
    if "repo" in fields and fields["repo"] != target.repo:
        _require_repo_wide(access, fields["repo"])
    for key, value in fields.items():
        setattr(target, key, value)
    await session.commit()
    await session.refresh(target)
    return target


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("scan:execute"))])
async def delete_target(
    target_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    target = await session.get(ScanTarget, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scan target not found")
    _require_repo_wide(access, target.repo)
    await session.delete(target)
    await session.commit()
