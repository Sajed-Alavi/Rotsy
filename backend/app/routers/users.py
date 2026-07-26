"""User administration endpoints (admin-only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import RequirePermission, get_session
from ..models import Role, User
from ..schemas.user import UserCreate, UserOut, UserUpdate
from ..core.security import hash_password

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(RequirePermission("users:manage"))],
)


async def _load_user_with_roles(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return user


async def _resolve_roles(session: AsyncSession, role_ids: list[int]) -> list[Role]:
    if not role_ids:
        return []
    result = await session.execute(select(Role).where(Role.id.in_(role_ids)))
    roles = list(result.scalars().all())
    missing = set(role_ids) - {r.id for r in roles}
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role ids: {sorted(missing)}")
    return roles


@router.get("", response_model=list[UserOut])
async def list_users(session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(select(User).order_by(User.id))
    return list(result.scalars().all())


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    existing = await session.scalar(select(User).where((User.username == body.username) | (User.email == body.email)))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already exists.")

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        is_active=body.is_active,
    )
    user.roles = await _resolve_roles(session, body.role_ids)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    user = await _load_user_with_roles(session, user_id)

    if body.email is not None:
        clash = await session.scalar(select(User).where(User.email == body.email, User.id != user.id))
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use.")
        user.email = body.email
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.role_ids is not None:
        user.roles = await _resolve_roles(session, body.role_ids)

    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    user = await _load_user_with_roles(session, user_id)
    await session.delete(user)
    await session.commit()
