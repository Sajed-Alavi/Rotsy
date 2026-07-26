"""Role + permission administration endpoints (admin-only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.permissions import PERMISSIONS
from ..dependencies import RequirePermission, get_session
from ..models import Permission, Role
from ..schemas.role import PermissionOut, RoleCreate, RoleOut, RoleUpdate

router = APIRouter(
    prefix="/roles",
    tags=["roles"],
    dependencies=[Depends(RequirePermission("roles:manage"))],
)


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(session: Annotated[AsyncSession, Depends(get_session)]):
    """List all permissions known to the system (the catalog)."""
    result = await session.execute(select(Permission).order_by(Permission.key))
    return list(result.scalars().all())


@router.get("", response_model=list[RoleOut])
async def list_roles(session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(select(Role).order_by(Role.id))
    return list(result.scalars().all())


async def _resolve_permissions(session: AsyncSession, keys: list[str]) -> list[Permission]:
    if not keys:
        return []
    valid = {k for k, _ in PERMISSIONS}
    unknown = set(keys) - valid
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown permission keys: {sorted(unknown)}")
    result = await session.execute(select(Permission).where(Permission.key.in_(keys)))
    return list(result.scalars().all())


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    existing = await session.scalar(select(Role).where(Role.name == body.name))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Role name already exists.")

    role = Role(name=body.name, description=body.description, is_system=False)
    role.permissions = await _resolve_permissions(session, body.permission_keys)
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


@router.patch("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: int,
    body: RoleUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found.")

    if body.name is not None:
        clash = await session.scalar(select(Role).where(Role.name == body.name, Role.id != role.id))
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Role name already exists.")
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permission_keys is not None:
        role.permissions = await _resolve_permissions(session, body.permission_keys)

    await session.commit()
    await session.refresh(role)
    return role


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found.")
    if role.is_system:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "System roles cannot be deleted.")
    await session.delete(role)
    await session.commit()
