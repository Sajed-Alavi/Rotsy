"""Image-level access scoping (wildcard) on top of the flat RBAC permission model.

The base RBAC system (:mod:`app.dependencies`) only ever answers "does this
user hold permission key X anywhere" — it has no notion of *which* repo or
image a request targets. This module adds that dimension as an **additive
restriction** layered on top, not a replacement:

A role's access to per-image data in a repo is unrestricted unless the role
has one or more :class:`~app.models.RoleImageScope` rows for that repo, in
which case access is limited to images matching at least one of that role's
patterns. A user's overall access is the **union across their held roles**
(mirrors how effective *permissions* are already a union across roles in
``_load_user_permissions``) — one unrestricted role is enough to grant full
access, consistent with RBAC "most permissive role wins" semantics.

A role with zero scope rows for a repo only counts as "unrestricted" when its
``Role.image_scope_unrestricted`` flag is true (the default, preserving
pre-existing behavior for admin/operator/viewer). A role explicitly flipped to
``image_scope_unrestricted=False`` never contributes blanket access on its
own — it only contributes what its own scope rows grant — so an admin can
stop a baseline role (e.g. a "viewer" everyone holds) from silently
overriding another, explicitly scoped role the same user holds.
"""

from __future__ import annotations

import fnmatch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RoleImageScope, User


async def allowed_image_patterns(session: AsyncSession, user: User, repo: str) -> list[str] | None:
    """Patterns restricting ``user`` for ``repo``, or ``None`` if unrestricted.

    ``None`` means: at least one of the user's held roles has no scope rows
    for this repo *and* is marked ``image_scope_unrestricted`` — so that role
    alone grants full access to it.
    """
    roles = user.roles
    if not roles:
        return []  # no roles at all -> nothing visible (shouldn't normally happen)

    role_ids = [r.id for r in roles]
    unrestricted_by_id = {r.id: r.image_scope_unrestricted for r in roles}

    rows = (await session.execute(
        select(RoleImageScope).where(RoleImageScope.role_id.in_(role_ids), RoleImageScope.repo == repo)
    )).scalars().all()
    scoped_role_ids = {r.role_id for r in rows}

    # Any held role with no scope rows for this repo AND still allowed to be
    # "unrestricted" grants full access outright.
    for role_id in role_ids:
        if role_id not in scoped_role_ids and unrestricted_by_id[role_id]:
            return None

    return [r.pattern for r in rows]


def image_visible(patterns: list[str] | None, image_name: str) -> bool:
    """Whether ``image_name`` is visible under ``patterns`` (see above)."""
    if patterns is None:
        return True
    return any(fnmatch.fnmatchcase(image_name, p) for p in patterns)
