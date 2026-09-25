"""Authorization decisions, in one place.

Rotsy authorizes on two independent axes, and almost every Project-scoped
action needs both:

* a **global permission key** (``projects:read``, ``projects:write``,
  ``system:execute``) — *what* a user may do anywhere, from their roles;
* **Project membership** (viewer / member / admin) — *which* Projects they
  may do it to. See :mod:`app.core.project_access`.

Routers expressed that pair by listing two dependencies
(``RequirePermission(...)`` plus ``require_project_access(...)``), which works
only when the Project id is a path parameter and only as long as every new
route remembers both halves. Neither held: ``POST
/modules/sonar/repositories/{sonar_project_id}/run-analysis`` takes its
Project from a row rather than the URL, so it could not use
``require_project_access`` and shipped with the global check alone — letting
any holder of ``projects:write`` trigger analysis on a Project they were not
a member of. The Telegram bot, needing the same rules with no FastAPI
dependency chain available, then re-implemented the pair by hand.

So the decisions live here as named functions instead: one definition per
action, callable from a router, from the bot's dispatcher, or from a service.
They raise :class:`~fastapi.HTTPException` because every current caller turns
a denial into an HTTP-shaped response (the bot renders ``exc.detail`` into a
chat message), and inventing a parallel exception type would only add a
translation layer with no new information in it.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ProjectMember, User
from . import project_access


def _assert_permission(user: User, key: str) -> None:
    from ..dependencies import user_permissions

    if key not in user_permissions(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Your account is missing the '{key}' permission."
        )


async def assert_project_permission(
    session: AsyncSession, user: User, project_id: int, *, permission: str, project_role: str,
) -> ProjectMember | None:
    """The generic form of the pair: hold ``permission`` globally **and** be a
    member of ``project_id`` at ``project_role`` or above.

    Global admins bypass the membership half (not the permission half) — the
    same carve-out :func:`app.core.project_access.assert_project_access`
    already makes, for the same reason: an administrator locked out of a
    Project would have no way back in through the app.
    """
    _assert_permission(user, permission)
    return await project_access.assert_project_access(session, user, project_id, project_role)


# --- named decisions -------------------------------------------------------
#
# Each mirrors the dependency pair its HTTP route already declared, so moving
# a caller onto one of these preserves the existing semantics exactly.


async def assert_can_view_project(session: AsyncSession, user: User, project_id: int):
    return await assert_project_permission(
        session, user, project_id, permission="projects:read", project_role="viewer",
    )


async def assert_can_manage_project(session: AsyncSession, user: User, project_id: int):
    """Connect/disconnect integrations and repositories — ``member`` and above."""
    return await assert_project_permission(
        session, user, project_id, permission="projects:write", project_role="member",
    )


async def assert_can_manage_members(session: AsyncSession, user: User, project_id: int):
    """Add, re-role and remove members — Project ``admin`` *and* the global
    ``projects:write`` permission. Being a Project admin is deliberately not
    sufficient on its own; this is the double gate ``routers/projects.py``'s
    member endpoints have always enforced."""
    return await assert_project_permission(
        session, user, project_id, permission="projects:write", project_role="admin",
    )


async def assert_can_run_analysis(session: AsyncSession, user: User, project_id: int):
    """Trigger an analysis run on a Project's repository.

    ``member`` matches what ``POST /modules/sonar/projects/{id}/run-analysis``
    already required via ``require_project_access("member")``; the
    by-repository route now resolves its Project id from the ``SonarProject``
    row and calls this, so the two routes cannot drift apart again.
    """
    return await assert_project_permission(
        session, user, project_id, permission="projects:write", project_role="member",
    )
