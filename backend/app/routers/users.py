"""User administration endpoints (admin-only)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.access_control import ACTIONS, MODE_UNRESTRICTED, explain
from ..core.security import hash_password
from ..dependencies import RequirePermission, get_current_user, get_session
from ..models import Role, RoleAccessRule, User
from ..schemas.role import EffectiveAccessOut, RoleAccessBreakdown, RuleMatchOut
from ..schemas.user import UserCreate, UserOut, UserUpdate
from ..services.audit import log_action

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


def _user_detail(user: User) -> str:
    roles = ", ".join(sorted(r.name for r in user.roles)) or "no roles"
    return f"{user.username} ({'active' if user.is_active else 'inactive'}; {roles})"


@router.get("", response_model=list[UserOut])
async def list_users(session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(select(User).order_by(User.id))
    return list(result.scalars().all())


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
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
    await log_action(session, actor.username, "create", "user", user.id, _user_detail(user))
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
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
    await log_action(session, actor.username, "update", "user", user.id, _user_detail(user))
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
):
    user = await _load_user_with_roles(session, user_id)
    username = user.username
    await session.delete(user)
    await session.commit()
    await log_action(session, actor.username, "delete", "user", user_id, username)


@router.get("/{user_id}/effective-access")
async def effective_access(
    user_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[str, Query(min_length=1, max_length=255)],
    image: Annotated[str, Query(min_length=1, max_length=255)],
) -> EffectiveAccessOut:
    """Answer "why can (or can't) this person reach that image", role by role.

    Effective access is a union across roles, so when a grant is unexpected the
    only useful question is *which* role produced it. That is what this returns.
    """
    user = await session.scalar(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    rules_by_role: dict[int, list[RoleAccessRule]] = {}
    if user.roles:
        rows = (await session.execute(
            select(RoleAccessRule).where(RoleAccessRule.role_id.in_([r.id for r in user.roles]))
        )).scalars().all()
        for rule in rows:
            rules_by_role.setdefault(rule.role_id, []).append(rule)

    unrestricted = False
    union: set[str] = set()
    breakdown: list[RoleAccessBreakdown] = []
    matched: list[RuleMatchOut] = []

    for role in user.roles:
        applied, allowed = explain(rules_by_role.get(role.id, []), repo, image)
        # No rule speaking to this repository puts the role on its mode fallback.
        role_open = not applied and role.access_mode == MODE_UNRESTRICTED
        if role_open:
            unrestricted = True
        role_actions = list(ACTIONS) if role_open else [a for a in ACTIONS if a in allowed]
        union.update(role_actions)
        rule_matches = [RuleMatchOut(**asdict(match)) for match in applied]
        matched.extend(rule_matches)
        breakdown.append(RoleAccessBreakdown(
            role_id=role.id,
            role_name=role.name,
            access_mode=role.access_mode,
            allowed_actions=role_actions,
            matched_rules=rule_matches,
        ))

    return EffectiveAccessOut(
        repo=repo,
        image=image,
        unrestricted=unrestricted,
        allowed_actions=[a for a in ACTIONS if a in union],
        matched_rules=matched,
        by_role=breakdown,
    )
