"""Role, permission and access-rule administration (admin-only).

Permissions decide *what* a role may do; access rules decide *where* — which
repositories and images those actions reach. See :mod:`app.core.access_control`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.access_control import ACTIONS, MODE_UNRESTRICTED, explain, format_actions
from ..core.permissions import PERMISSIONS
from ..dependencies import RequirePermission, get_current_user, get_session
from ..models import Permission, Role, RoleAccessRule, User
from ..schemas.role import (
    AccessRuleCreate,
    AccessRuleOut,
    AccessRuleTest,
    AccessRuleUpdate,
    AccessTestResult,
    PermissionOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    RuleMatchOut,
)
from ..services.audit import log_action

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


@router.get("/actions")
async def list_actions() -> list[str]:
    """The actions an access rule can grant, so the UI need not hardcode them."""
    return list(ACTIONS)


@router.get("", response_model=list[RoleOut])
async def list_roles(session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(select(Role).order_by(Role.id))
    return list(result.scalars().all())


async def _resolve_permissions(session: AsyncSession, keys: list[str]) -> list[Permission]:
    if not keys:
        return []
    valid = {key for key, _ in PERMISSIONS}
    unknown = set(keys) - valid
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown permission keys: {sorted(unknown)}")
    result = await session.execute(select(Permission).where(Permission.key.in_(keys)))
    return list(result.scalars().all())


#: The role that must always be able to reach everything. Scoping it is a
#: one-way door: administrators would have no in-app path back.
ADMIN_ROLE = "admin"


async def _get_role_or_404(session: AsyncSession, role_id: int) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found.")
    return role


def _reject_if_admin(role: Role) -> None:
    """Refuse to narrow the admin role.

    ``access_mode`` alone is not the only lever: any rule matching a repository
    takes a role off its unrestricted fallback for that repository, so a single
    ``deny`` rule on ``admin`` would lock every administrator out of it with no
    way to undo the change through the app. Scope a custom role instead.
    """
    if role.is_system and role.name == ADMIN_ROLE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The 'admin' role cannot be scoped or carry access rules — doing so could "
            "lock every administrator out. Create a scoped custom role instead.",
        )


def _role_detail(role: Role) -> str:
    return f"{role.name} ({role.access_mode}, {len(role.permissions)} permissions)"


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
):
    existing = await session.scalar(select(Role).where(Role.name == body.name))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Role name already exists.")

    role = Role(
        name=body.name,
        description=body.description,
        is_system=False,
        access_mode=body.access_mode,
    )
    role.permissions = await _resolve_permissions(session, body.permission_keys)
    session.add(role)
    await session.commit()
    await session.refresh(role)
    await log_action(session, user.username, "create", "role", role.id, _role_detail(role))
    return role


@router.patch("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: int,
    body: RoleUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
):
    role = await _get_role_or_404(session, role_id)

    if body.name is not None:
        clash = await session.scalar(select(Role).where(Role.name == body.name, Role.id != role.id))
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Role name already exists.")
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permission_keys is not None:
        role.permissions = await _resolve_permissions(session, body.permission_keys)
    if body.access_mode is not None and body.access_mode != role.access_mode:
        _reject_if_admin(role)
        role.access_mode = body.access_mode

    await session.commit()
    await session.refresh(role)
    await log_action(session, user.username, "update", "role", role.id, _role_detail(role))
    return role


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
):
    role = await _get_role_or_404(session, role_id)
    if role.is_system:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "System roles cannot be deleted.")
    name = role.name
    await session.delete(role)
    await session.commit()
    await log_action(session, user.username, "delete", "role", role_id, name)


# ---------------------------------------------------------------------------
# Access rules — see app.core.access_control
# ---------------------------------------------------------------------------
async def _get_rule_or_404(session: AsyncSession, role_id: int, rule_id: int) -> RoleAccessRule:
    rule = await session.get(RoleAccessRule, rule_id)
    if rule is None or rule.role_id != role_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access rule not found.")
    return rule


async def _reject_duplicate(
    session: AsyncSession,
    role_id: int,
    effect: str,
    repo_pattern: str,
    image_pattern: str,
    *,
    exclude_id: int | None = None,
) -> None:
    """Turn the (role, effect, repo, image) unique key into a 409, not a 500."""
    stmt = select(RoleAccessRule).where(
        RoleAccessRule.role_id == role_id,
        RoleAccessRule.effect == effect,
        RoleAccessRule.repo_pattern == repo_pattern,
        RoleAccessRule.image_pattern == image_pattern,
    )
    if exclude_id is not None:
        stmt = stmt.where(RoleAccessRule.id != exclude_id)
    if await session.scalar(stmt) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This role already has a rule for that effect and pattern pair — edit it instead.",
        )


async def _role_rules(session: AsyncSession, role_id: int) -> list[RoleAccessRule]:
    result = await session.execute(
        select(RoleAccessRule)
        .where(RoleAccessRule.role_id == role_id)
        .order_by(RoleAccessRule.repo_pattern, RoleAccessRule.image_pattern, RoleAccessRule.id)
    )
    return list(result.scalars().all())


def _rule_detail(role: Role, rule: RoleAccessRule) -> str:
    return f"{role.name}: {rule.effect} [{rule.actions}] on {rule.repo_pattern}/{rule.image_pattern}"


@router.get("/{role_id}/access-rules", response_model=list[AccessRuleOut])
async def list_access_rules(role_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    await _get_role_or_404(session, role_id)
    return await _role_rules(session, role_id)


@router.post(
    "/{role_id}/access-rules", response_model=AccessRuleOut, status_code=status.HTTP_201_CREATED
)
async def create_access_rule(
    role_id: int,
    body: AccessRuleCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
):
    role = await _get_role_or_404(session, role_id)
    _reject_if_admin(role)
    await _reject_duplicate(session, role_id, body.effect, body.repo_pattern, body.image_pattern)

    rule = RoleAccessRule(
        role_id=role_id,
        effect=body.effect,
        repo_pattern=body.repo_pattern,
        image_pattern=body.image_pattern,
        actions=format_actions(body.actions),
        description=body.description,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await log_action(
        session, user.username, "create", "access_rule", rule.id, _rule_detail(role, rule)
    )
    return rule


@router.patch("/{role_id}/access-rules/{rule_id}", response_model=AccessRuleOut)
async def update_access_rule(
    role_id: int,
    rule_id: int,
    body: AccessRuleUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
):
    role = await _get_role_or_404(session, role_id)
    rule = await _get_rule_or_404(session, role_id, rule_id)

    await _reject_duplicate(
        session,
        role_id,
        rule.effect if body.effect is None else body.effect,
        rule.repo_pattern if body.repo_pattern is None else body.repo_pattern,
        rule.image_pattern if body.image_pattern is None else body.image_pattern,
        exclude_id=rule.id,
    )

    if body.effect is not None:
        rule.effect = body.effect
    if body.repo_pattern is not None:
        rule.repo_pattern = body.repo_pattern
    if body.image_pattern is not None:
        rule.image_pattern = body.image_pattern
    if body.actions is not None:
        rule.actions = format_actions(body.actions)
    if body.description is not None:
        rule.description = body.description

    await session.commit()
    await session.refresh(rule)
    await log_action(
        session, user.username, "update", "access_rule", rule.id, _rule_detail(role, rule)
    )
    return rule


@router.delete("/{role_id}/access-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_access_rule(
    role_id: int,
    rule_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
):
    role = await _get_role_or_404(session, role_id)
    rule = await _get_rule_or_404(session, role_id, rule_id)
    detail = _rule_detail(role, rule)
    await session.delete(rule)
    await session.commit()
    await log_action(session, user.username, "delete", "access_rule", rule_id, detail)


@router.post("/{role_id}/access-rules/test", response_model=AccessTestResult)
async def test_access_rules(
    role_id: int,
    body: AccessRuleTest,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Show what this role's rules do to one concrete repository/image pair.

    Wildcards are only safe to write when you can see their blast radius before
    saving; this backs the rule tester in the admin UI.
    """
    role = await _get_role_or_404(session, role_id)
    applied, allowed = explain(await _role_rules(session, role_id), body.repo, body.image)

    # A rule matching the repository is what takes a role off its access_mode
    # fallback. With none, an unrestricted role still allows everything.
    unrestricted = not applied and role.access_mode == MODE_UNRESTRICTED

    return AccessTestResult(
        repo=body.repo,
        image=body.image,
        unrestricted=unrestricted,
        allowed_actions=list(ACTIONS) if unrestricted else [a for a in ACTIONS if a in allowed],
        matched_rules=[RuleMatchOut(**asdict(match)) for match in applied],
    )
