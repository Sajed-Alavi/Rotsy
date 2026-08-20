"""Telegram bot integration tests.

Three groups: ``callback_data`` (pure encode/parse, no DB), ``auth.linked_user``
(chat -> live user resolution, including the deactivated-user cutoff), and the
dispatcher's authorization decisions on the two write-ish paths that don't
already have an equivalent HTTP-layer test — project-admin-write gating and
the run-analysis project-membership check that this module's own docstring
says the HTTP endpoint is missing.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.core.projects import create_project
from app.models import Permission, Project, ProjectMember, Role, SonarProject, TelegramLink, User
from app.modules.telegram import dispatcher
from app.modules.telegram.auth import linked_user
from app.modules.telegram.callback_data import Callback, CallbackDataError, build, parse
from app.routers.telegram import LinkCreate

# --- callback_data ------------------------------------------------------


def test_parse_splits_action_and_args():
    cb = parse("mu:5:12:a")
    assert cb.action == "mu"
    assert cb.args == ("5", "12", "a")


@pytest.mark.parametrize("data", ["", None])
def test_parse_rejects_empty(data):
    with pytest.raises(CallbackDataError):
        parse(data)


def test_build_round_trips_with_parse():
    encoded = build("mm", 5)
    assert encoded == "mm:5"
    assert parse(encoded) == Callback(action="mm", args=("5",))


def test_build_rejects_data_over_64_bytes():
    with pytest.raises(CallbackDataError):
        build("pl", "x" * 64)


def test_int_arg_rejects_non_integer():
    cb = parse("p:not-a-number")
    with pytest.raises(CallbackDataError):
        cb.int_arg(0)


def test_int_arg_rejects_missing_index():
    cb = parse("pl")
    with pytest.raises(CallbackDataError):
        cb.int_arg(0)


def test_int_arg_accepts_negative_numbers():
    # callback_data is attacker-controlled (a tampered client can send any
    # byte string); parsing must not reject a negative page on its own —
    # callers are responsible for clamping it before using it as an offset.
    cb = parse("mc:5:-1")
    assert cb.int_arg(1) == -1


@pytest.mark.parametrize(("code", "role"), [("v", "viewer"), ("m", "member"), ("a", "admin")])
def test_role_arg_maps_known_codes(code, role):
    cb = parse(f"mr:1:2:{code}")
    assert cb.role_arg(2) == role


def test_role_arg_rejects_unknown_code():
    cb = parse("mr:1:2:z")
    with pytest.raises(CallbackDataError):
        cb.role_arg(2)


# --- routers.telegram.LinkCreate -----------------------------------------


def test_link_create_accepts_positive_chat_id():
    link = LinkCreate(user_id=1, chat_id=123456789)
    assert link.chat_id == 123456789


@pytest.mark.parametrize("chat_id", [0, -1, -1001234567890])
def test_link_create_rejects_non_positive_chat_id(chat_id):
    # Private-chat ids are always positive (they equal the Telegram user's
    # own id); group/supergroup/channel ids are always negative or zero.
    # TelegramLink.chat_id is the sole identifier account access rests on,
    # so a group id must never be linkable.
    with pytest.raises(ValidationError):
        LinkCreate(user_id=1, chat_id=chat_id)


# --- modules.telegram.auth.linked_user -----------------------------------


async def _permission(db_session, key: str) -> Permission:
    existing = await db_session.scalar(select(Permission).where(Permission.key == key))
    if existing is not None:
        return existing
    perm = Permission(key=key, description=key)
    db_session.add(perm)
    await db_session.flush()
    return perm


async def _make_user(db_session, *, active: bool = True, permissions: list[str] = ()) -> User:
    role = Role(name=f"role-{id(object())}", access_mode="unrestricted")
    db_session.add(role)
    for key in permissions:
        role.permissions.append(await _permission(db_session, key))
    user = User(
        username=f"u{id(object())}", email=f"u{id(object())}@example.com",
        password_hash="x", is_active=active, roles=[role],
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_linked_user_returns_none_for_unlinked_chat(db_session):
    assert await linked_user(db_session, 999) is None


async def test_linked_user_returns_none_for_deactivated_user(db_session):
    user = await _make_user(db_session, active=False)
    db_session.add(TelegramLink(user_id=user.id, chat_id=111, linked_by="admin"))
    await db_session.commit()

    assert await linked_user(db_session, 111) is None


async def test_linked_user_returns_active_user_with_permissions_loaded(db_session):
    user = await _make_user(db_session, permissions=["projects:read"])
    db_session.add(TelegramLink(user_id=user.id, chat_id=222, linked_by="admin"))
    await db_session.commit()

    resolved = await linked_user(db_session, 222)
    assert resolved is not None
    assert resolved.id == user.id
    assert "projects:read" in dispatcher.user_permissions(resolved)


# --- dispatcher authorization ---------------------------------------------


async def test_project_viewer_cannot_remove_a_member(db_session):
    """A project viewer (no admin-level membership) tapping the confirmed-
    remove action must be rejected before any row is touched — mirrors the
    same double gate routers/projects.py's member-management endpoints
    enforce."""
    owner = await _make_user(db_session, permissions=["projects:read", "projects:write"])
    project = await create_project(db_session, "Acme", owner)
    viewer = await _make_user(db_session, permissions=["projects:read", "projects:write"])
    db_session.add(ProjectMember(project_id=project.id, user_id=viewer.id, project_role="viewer"))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await dispatcher._do_remove(db_session, viewer, project.id, member_id=1)
    assert exc_info.value.status_code == 403


async def test_project_admin_without_global_projects_write_cannot_manage_members(db_session):
    """Project-level admin alone isn't enough — the global projects:write
    permission is also required, same as the web app."""
    owner = await _make_user(db_session, permissions=["projects:read", "projects:write"])
    project = await create_project(db_session, "Acme", owner)
    admin_no_write = await _make_user(db_session, permissions=["projects:read"])
    db_session.add(ProjectMember(project_id=project.id, user_id=admin_no_write.id, project_role="admin"))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await dispatcher._member_actions(db_session, admin_no_write, project.id, member_id=1)
    assert exc_info.value.status_code == 403


async def test_run_analysis_requires_global_projects_write(db_session):
    owner = await _make_user(db_session, permissions=["projects:read", "projects:write"])
    project = await create_project(db_session, "Acme", owner)
    sonar_project = SonarProject(
        project_id=project.id, sonar_project_key="acme-repo", language="python",
    )
    db_session.add(sonar_project)
    await db_session.commit()
    await db_session.refresh(sonar_project)

    no_write_user = await _make_user(db_session, permissions=["projects:read"])

    with pytest.raises(HTTPException) as exc_info:
        await dispatcher._run_analysis(db_session, None, None, no_write_user, sonar_project.id)
    assert exc_info.value.status_code == 403


async def test_run_analysis_requires_membership_on_the_projects_own_project(db_session):
    """The endpoint _run_analysis eventually calls only checks the global
    projects:write permission, not project membership — this module's own
    docstring calls that out, and verifies it explicitly here instead. A
    user with projects:write globally but no membership on this particular
    project must still be rejected."""
    owner = await _make_user(db_session, permissions=["projects:read", "projects:write"])
    project = await create_project(db_session, "Acme", owner)
    sonar_project = SonarProject(
        project_id=project.id, sonar_project_key="acme-repo-2", language="python",
    )
    db_session.add(sonar_project)
    await db_session.commit()
    await db_session.refresh(sonar_project)

    outsider = await _make_user(db_session, permissions=["projects:read", "projects:write"])

    with pytest.raises(HTTPException) as exc_info:
        await dispatcher._run_analysis(db_session, None, None, outsider, sonar_project.id)
    assert exc_info.value.status_code == 403
