"""Authorization enforced at the HTTP boundary.

Every other test in this suite calls services and helpers directly. That
cannot see a route whose ``dependencies=[...]`` list is missing a check — the
service is correct, the endpoint in front of it is not, and the test passes.
This module drives the real app so the route's own declared dependencies run.

The pattern under test is the pair the whole app uses: a global permission key
(``RequirePermission``) answering *what* a user may do, and Project membership
answering *which* Projects they may do it to. Both must hold. A route enforcing
only the first grants every holder of ``projects:write`` access to every
Project in the system, which is precisely the hole per-project membership was
introduced to close.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.projects import create_project
from app.models import (
    GitHubRepository,
    Permission,
    ProjectMember,
    Role,
    SonarProject,
    User,
)


async def _permission(session, key: str) -> Permission:
    from sqlalchemy import select

    existing = await session.scalar(select(Permission).where(Permission.key == key))
    if existing is not None:
        return existing
    perm = Permission(key=key, description=key)
    session.add(perm)
    await session.flush()
    return perm


async def _user(session, *, permissions: tuple[str, ...] = (), admin_role: bool = False) -> User:
    role = Role(name=("admin" if admin_role else f"role-{id(object())}"), access_mode="unrestricted")
    session.add(role)
    for key in permissions:
        role.permissions.append(await _permission(session, key))
    user = User(
        username=f"u{id(object())}", email=f"u{id(object())}@example.com",
        password_hash="x", is_active=True, roles=[role],
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    # Set *after* the refresh, which would otherwise re-read the column from
    # SQLite as a naive datetime — Postgres returns an aware one, and
    # get_current_user's idle-timeout arithmetic needs to subtract from it.
    # The session's identity map hands this same instance back to the request,
    # so the aware value is what the dependency sees.
    user.last_seen_at = datetime.now(timezone.utc)
    user._effective_permissions = sorted(set(permissions))  # type: ignore[attr-defined]
    return user


async def _sonar_project(session, project_id: int, *, key: str) -> SonarProject:
    """A SonarProject needs a repository row to resolve a provider from, so the
    run-analysis endpoint gets far enough to be judged on authorization rather
    than failing earlier for an unrelated reason."""
    repo = GitHubRepository(
        installation_id=None, project_id=project_id,
        full_name=f"acme/{key}", default_branch="main",
    )
    session.add(repo)
    await session.flush()
    sonar_project = SonarProject(
        project_id=project_id, github_repository_id=repo.id,
        sonar_project_key=key, language="python",
    )
    session.add(sonar_project)
    await session.commit()
    await session.refresh(sonar_project)
    return sonar_project


# --- the baseline: authentication still gates everything -------------------


async def test_unauthenticated_request_is_rejected(api):
    resp = await api.client.get("/api/projects")
    assert resp.status_code == 401


async def test_real_login_still_works(api, db_session):
    """The one test that exercises the actual cookie path rather than the
    ``authenticate_as`` override, so the override can never quietly mask a
    broken login."""
    from app.core.security import hash_password

    user = await _user(db_session, permissions=("projects:read",))
    user.password_hash = hash_password("a-strong-test-only-password-123")
    await db_session.commit()

    resp = await api.login(user.username, "a-strong-test-only-password-123")
    assert resp.status_code == 200
    assert "access_token" in resp.cookies

    listed = await api.client.get("/api/projects")
    assert listed.status_code == 200


async def test_login_rejects_a_bad_password(api, db_session):
    from app.core.security import hash_password

    user = await _user(db_session, permissions=("projects:read",))
    user.password_hash = hash_password("a-strong-test-only-password-123")
    await db_session.commit()

    resp = await api.login(user.username, "wrong-password")
    assert resp.status_code == 401


# --- the global permission half --------------------------------------------


async def test_missing_global_permission_is_403(api, db_session):
    user = await _user(db_session, permissions=())
    api.authenticate_as(user)

    resp = await api.client.get("/api/projects")
    assert resp.status_code == 403


# --- the project-membership half -------------------------------------------


async def test_non_member_cannot_read_a_project(api, db_session):
    owner = await _user(db_session, permissions=("projects:read", "projects:write"))
    project = await create_project(db_session, "Acme", owner)
    outsider = await _user(db_session, permissions=("projects:read",))
    api.authenticate_as(outsider)

    resp = await api.client.get(f"/api/projects/{project.id}")
    assert resp.status_code == 403


async def test_member_can_read_their_own_project(api, db_session):
    owner = await _user(db_session, permissions=("projects:read", "projects:write"))
    project = await create_project(db_session, "Acme", owner)
    api.authenticate_as(owner)

    resp = await api.client.get(f"/api/projects/{project.id}")
    assert resp.status_code == 200


async def test_project_viewer_cannot_add_a_member(api, db_session):
    owner = await _user(db_session, permissions=("projects:read", "projects:write"))
    project = await create_project(db_session, "Acme", owner)
    viewer = await _user(db_session, permissions=("projects:read", "projects:write"))
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, project_role="viewer"))
    await db_session.commit()
    api.authenticate_as(viewer)

    resp = await api.client.post(
        f"/api/projects/{project.id}/members", json={"user_id": owner.id, "project_role": "viewer"},
    )
    assert resp.status_code == 403


# --- run-analysis: the route this module was written for --------------------
#
# /projects/{id}/run-analysis carries require_project_access("member"); its
# sibling /repositories/{sonar_project_id}/run-analysis reaches the same job
# queue and enqueues the same clone_and_analyze job, but takes its project from
# a row rather than the URL — and so was gated on the global permission alone.


async def test_run_analysis_by_project_rejects_a_non_member(api, db_session):
    owner = await _user(db_session, permissions=("projects:read", "projects:write"))
    project = await create_project(db_session, "Acme", owner)
    outsider = await _user(db_session, permissions=("projects:read", "projects:write"))
    api.authenticate_as(outsider)

    resp = await api.client.post(f"/api/modules/sonar/projects/{project.id}/run-analysis")
    assert resp.status_code == 403


async def test_run_analysis_by_repository_rejects_a_non_member(api, db_session):
    """A user with global ``projects:write`` but no membership on the Project
    that owns this repository must not be able to trigger analysis on it."""
    owner = await _user(db_session, permissions=("projects:read", "projects:write"))
    project = await create_project(db_session, "Acme", owner)
    sonar_project = await _sonar_project(db_session, project.id, key="acme-a")

    outsider = await _user(db_session, permissions=("projects:read", "projects:write"))
    api.authenticate_as(outsider)

    resp = await api.client.post(
        f"/api/modules/sonar/repositories/{sonar_project.id}/run-analysis",
    )
    assert resp.status_code == 403


async def test_run_analysis_by_repository_allows_a_member_through(api, db_session):
    """The mirror of the test above: a real member must still get past
    authorization. Without a job queue in tests the handler then answers 503,
    which is the proof it was allowed through rather than rejected."""
    owner = await _user(db_session, permissions=("projects:read", "projects:write"))
    project = await create_project(db_session, "Acme", owner)
    sonar_project = await _sonar_project(db_session, project.id, key="acme-b")
    api.authenticate_as(owner)

    resp = await api.client.post(
        f"/api/modules/sonar/repositories/{sonar_project.id}/run-analysis",
    )
    assert resp.status_code != 403
    assert resp.status_code == 503


@pytest.mark.parametrize("project_role", ["viewer"])
async def test_run_analysis_by_repository_requires_at_least_member(api, db_session, project_role):
    """Membership alone is not enough — the Project-scoped role must be at
    least ``member``, matching what the by-project route already required."""
    owner = await _user(db_session, permissions=("projects:read", "projects:write"))
    project = await create_project(db_session, "Acme", owner)
    sonar_project = await _sonar_project(db_session, project.id, key="acme-c")

    viewer = await _user(db_session, permissions=("projects:read", "projects:write"))
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, project_role=project_role))
    await db_session.commit()
    api.authenticate_as(viewer)

    resp = await api.client.post(
        f"/api/modules/sonar/repositories/{sonar_project.id}/run-analysis",
    )
    assert resp.status_code == 403
