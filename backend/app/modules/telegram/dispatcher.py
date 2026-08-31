"""Routes an incoming Telegram update to the right handler.

Every handler re-derives authorization from the same functions the web app
uses — ``core.project_access``/``core.projects`` for Project membership,
plus ``dependencies.user_permissions`` for the global RBAC side — never a
bot-specific reimplementation of a permission decision. Two existing router
functions (``routers.projects.list_project_repositories``,
``routers.sonar.run_repository_analysis``) are called directly as plain
Python functions rather than duplicating their orchestration logic; both
take only plain parameters (no ``Request``), so this is safe, but it means
this module — unusually for ``modules/`` — reaches into ``routers/``. Both
are imported lazily inside the functions that use them rather than at
module scope, so importing this dispatcher never risks a circular import
with whatever router eventually imports it. The project's own access check
for the run-analysis case is verified here explicitly, since that
endpoint's own dependency only checks the global ``projects:write``
permission, not project membership.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...core import project_access
from ...core import projects as projects_core
from ...core.health import compute_health_score
from ...dependencies import user_permissions
from ...models import Project, SonarProject, TelegramLink, User
from ...state import AppState
from . import keyboards as kb
from .auth import linked_user
from .callback_data import Callback, CallbackDataError, build, parse
from .client import TelegramClient, escape_html as _esc

logger = logging.getLogger(__name__)


def _require_read(user: User) -> None:
    if "projects:read" not in user_permissions(user):
        raise HTTPException(403, "Your account can't view projects (missing the projects:read permission).")


async def _require_project_admin_write(session: AsyncSession, user: User, project_id: int):
    """A membership row's own ``admin`` role isn't enough on its own — same
    double gate ``routers/projects.py``'s member-management endpoints
    enforce: the global ``projects:write`` permission is also required."""
    _require_read(user)
    membership = await project_access.assert_project_access(session, user, project_id, "admin")
    if "projects:write" not in user_permissions(user):
        raise HTTPException(
            403,
            "You're a Project admin here, but your account also needs the global "
            "'projects:write' permission — ask your Rotsy administrator to grant it.",
        )
    return membership


async def _projects_list(session: AsyncSession, user: User, page: int) -> tuple[str, dict]:
    _require_read(user)
    page = max(0, page)
    all_projects = await projects_core.list_projects(session, user)
    start, end = page * kb.PAGE_SIZE, page * kb.PAGE_SIZE + kb.PAGE_SIZE
    page_items = all_projects[start:end]
    has_more = len(all_projects) > end

    rows: list[dict[str, Any]] = []
    for p in page_items:
        membership = await project_access.get_membership(session, p.id, user.id)
        rows.append({"id": p.id, "name": p.name, "role": membership.project_role if membership else None})

    if not rows:
        # Rebuilds the whole menu, so it has to carry the admin flag the same
        # way _handle_start does — otherwise an admin on an install with no
        # projects yet taps "My Projects" and the Admin button vanishes.
        return "You have no project access yet. Ask your Rotsy administrator.", kb.main_menu(
            "system:execute" in user_permissions(user)
        )
    text = "<b>Your Projects</b>\nTap one to see more."
    return text, kb.projects_list(rows, page, has_more)


async def _project_detail(session: AsyncSession, user: User, project_id: int) -> tuple[str, dict]:
    _require_read(user)
    membership = await project_access.assert_project_access(session, user, project_id, "viewer")
    project = await projects_core.get_project(session, project_id)
    health = await compute_health_score(session, project_id)
    role = membership.project_role if membership else "admin*"

    is_admin = project_access.is_global_admin(user) or (
        membership is not None and project_access.meets(membership.project_role, "admin")
    )
    can_manage_members = is_admin and "projects:write" in user_permissions(user)

    lines = [f"<b>{_esc(project.name)}</b>", f"Your role: {role}"]
    if health.has_data:
        lines.append(f"Health: {health.score}/100")
    else:
        lines.append("Health: no data yet")
    if health.factors:
        lines.append("• " + "\n• ".join(health.factors[:3]))
    return "\n".join(lines), kb.project_detail(project_id, can_manage_members, can_view_repos=True)


async def _members_list(session: AsyncSession, user: User, project_id: int) -> tuple[str, dict]:
    _require_read(user)
    membership = await project_access.assert_project_access(session, user, project_id, "viewer")
    is_admin = project_access.is_global_admin(user) or (
        membership is not None and project_access.meets(membership.project_role, "admin")
    )
    can_manage = is_admin and "projects:write" in user_permissions(user)

    members = await projects_core.list_members(session, project_id)
    text = "<b>Members</b>" if members else "No members yet."
    return text, kb.members_list(project_id, members, 0, False, can_manage)


async def _candidates_list(session: AsyncSession, user: User, project_id: int, page: int) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    page = max(0, page)
    offset = page * kb.PAGE_SIZE
    results = await projects_core.search_member_candidates(session, project_id, None, offset=offset, limit=kb.PAGE_SIZE + 1)
    has_more = len(results) > kb.PAGE_SIZE
    candidates = [{"id": u.id, "username": u.username} for u in results[: kb.PAGE_SIZE]]
    text = "Pick a user to add:" if candidates else "No more users to add."
    return text, kb.candidates_list(project_id, candidates, page, has_more)


async def _pick_candidate(session: AsyncSession, user: User, project_id: int, target_user_id: int) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    target = await session.get(User, target_user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    back = build("mc", project_id, 0)
    return f"Add <b>{_esc(target.username)}</b> as:", kb.role_picker("mr", project_id, target_user_id, back)


async def _confirm_add(session: AsyncSession, user: User, project_id: int, target_user_id: int, role: str) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    result = await projects_core.add_member(session, project_id, target_user_id, role)
    return f"✅ Added {_esc(result['username'])} as {result['project_role']}.", kb.back_only(build("mm", project_id))


async def _member_actions(session: AsyncSession, user: User, project_id: int, member_id: int) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    return "Choose an action:", kb.member_actions(project_id, member_id)


async def _role_picker_for_existing(session: AsyncSession, user: User, project_id: int, member_id: int) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    back = build("me", project_id, member_id)
    return "New role:", kb.role_picker("mu", project_id, member_id, back)


async def _change_role(session: AsyncSession, user: User, project_id: int, member_id: int, role: str) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    result = await projects_core.update_member_role(session, project_id, member_id, role)
    return f"✅ {_esc(result['username'])} is now {result['project_role']}.", kb.back_only(build("mm", project_id))


async def _confirm_remove_prompt(session: AsyncSession, user: User, project_id: int, member_id: int) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    return "Remove this member from the project?", kb.confirm_remove(project_id, member_id)


async def _do_remove(session: AsyncSession, user: User, project_id: int, member_id: int) -> tuple[str, dict]:
    await _require_project_admin_write(session, user, project_id)
    await projects_core.remove_member(session, project_id, member_id)
    return "✅ Member removed.", kb.back_only(build("mm", project_id))


async def _repos_list(
    session: AsyncSession, settings: Settings, user: User, project_id: int, page: int,
) -> tuple[str, dict]:
    _require_read(user)
    page = max(0, page)
    membership = await project_access.assert_project_access(session, user, project_id, "viewer")
    can_run = (
        project_access.is_global_admin(user)
        or (membership is not None and project_access.meets(membership.project_role, "member"))
    ) and "projects:write" in user_permissions(user)

    # Imported lazily (rather than at module scope) because it lives in
    # routers/ — see this module's docstring — and a module-scope import
    # here would make the first routers/ module that imports this dispatcher
    # a circular import.
    from ...routers.projects import list_project_repositories

    all_repos = await list_project_repositories(project_id, session, settings)
    start, end = page * kb.PAGE_SIZE, page * kb.PAGE_SIZE + kb.PAGE_SIZE
    page_items = all_repos[start:end]
    has_more = len(all_repos) > end
    text = "<b>Repositories</b>" if page_items else "No repositories connected yet."
    return text, kb.repos_list(project_id, page_items, page, has_more, can_run)


async def _run_analysis(
    session: AsyncSession, settings: Settings, app_state_obj: AppState, user: User, sonar_project_id: int,
) -> tuple[str, dict]:
    _require_read(user)
    if "projects:write" not in user_permissions(user):
        raise HTTPException(403, "Your account needs the global 'projects:write' permission to run analysis.")
    sonar_project = await session.get(SonarProject, sonar_project_id)
    if sonar_project is None:
        raise HTTPException(404, "This repository's analysis record no longer exists.")
    # run_repository_analysis's own dependency only checks the global
    # projects:write permission, not project membership — verified here
    # explicitly so the bot never triggers analysis on a project the user
    # isn't actually a member of.
    await project_access.assert_project_access(session, user, sonar_project.project_id, "member")

    # Imported lazily for the same circular-import reason as
    # list_project_repositories above.
    from ...routers.sonar import run_repository_analysis

    result = await run_repository_analysis(sonar_project_id, session, settings, app_state_obj)
    job_id = str(result.get("job_id", "?"))[:8]
    return f"▶ Analysis queued (job {job_id}).", kb.back_only(build("pr", sonar_project.project_id, 0))


def _require_admin(user: User) -> None:
    if "system:execute" not in user_permissions(user):
        raise HTTPException(403, "Your account needs the global 'system:execute' permission for the admin panel.")


async def _admin_home(session: AsyncSession, settings: Settings, app_state_obj: AppState, user: User) -> tuple[str, dict]:
    """A condensed, read-mostly mirror of a few Settings/Dashboard cards —
    deliberately small (link count, job queue depth, scanner DB freshness,
    project/user counts) rather than reproducing the web admin surface."""
    _require_admin(user)

    link_count = len((await session.execute(select(TelegramLink))).scalars().all())

    running = pending = 0
    if app_state_obj.cache is not None:
        from ...core.jobs import JobQueue
        for job in await JobQueue(app_state_obj.cache).list_recent(limit=200):
            if job.status == "running":
                running += 1
            elif job.status == "pending":
                pending += 1

    from ...modules.nexus import db as scanner_db
    from ...services.scanner_config import get_enabled_scanners
    enabled = await get_enabled_scanners(settings, session)
    # readiness() is synchronous and genuinely slow: it walks the multi-GB
    # scanner cache trees and shells out to the grype binary via a blocking
    # subprocess.run with a 30s timeout. Called directly it would stall the
    # single event loop this poll loop shares with the whole HTTP server, so
    # it goes to a worker thread. It also computes status() internally —
    # hence no separate status() call here to filter names with, since
    # readiness() already reports an unknown scanner as not-ready itself.
    ready = await asyncio.to_thread(scanner_db.readiness, list(enabled))
    scanner_lines = []
    for name in enabled:
        info = ready.get(name)
        state = "✅ ready" if info and info.ready else ("⚠ stale" if info and info.stale else "❌ missing")
        scanner_lines.append(f"  {_esc(name)}: {state}")

    project_count = (await session.execute(select(func.count()).select_from(Project))).scalar_one()
    user_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()

    lines = [
        "⚙️ <b>Admin Panel</b>",
        "",
        f"🤖 Bot: {link_count} linked account(s)",
        f"📊 Jobs: {running} running, {pending} pending",
        "🛡 Scanner DBs:",
        *scanner_lines,
        f"📁 Projects: {project_count}   👤 Users: {user_count}",
    ]
    return "\n".join(lines), kb.admin_home()


async def _admin_links_list(session: AsyncSession, user: User, page: int) -> tuple[str, dict]:
    _require_admin(user)
    page = max(0, page)
    rows = (
        await session.execute(
            select(TelegramLink, User)
            .join(User, User.id == TelegramLink.user_id)
            .order_by(User.username)
        )
    ).all()
    start, end = page * kb.PAGE_SIZE, page * kb.PAGE_SIZE + kb.PAGE_SIZE
    page_rows = rows[start:end]
    has_more = len(rows) > end
    links = [{"id": link.id, "username": u.username} for link, u in page_rows]
    text = "<b>Linked Accounts</b>" if links else "No accounts linked yet."
    return text, kb.admin_links_list(links, page, has_more)


async def _admin_unlink_prompt(session: AsyncSession, user: User, link_id: int) -> tuple[str, dict]:
    _require_admin(user)
    link = await session.get(TelegramLink, link_id)
    if link is None:
        raise HTTPException(404, "That link no longer exists.")
    target = await session.get(User, link.user_id)
    name = _esc(target.username) if target is not None else f"user #{link.user_id}"
    return f"Unlink {name} (chat {link.chat_id}) from Telegram?", kb.admin_confirm_unlink(link_id)


async def _admin_unlink_confirmed(session: AsyncSession, user: User, link_id: int) -> tuple[str, dict]:
    _require_admin(user)
    link = await session.get(TelegramLink, link_id)
    if link is not None:
        await session.delete(link)
        await session.commit()
    return "✅ Unlinked.", kb.back_only(build("adl", 0))


async def _main_menu(user: User) -> tuple[str, dict]:
    """Re-render the top-level menu. Deliberately requires nothing beyond
    being linked: it is what every section's outermost "Back" returns to, and
    gating it on any one permission would strand whoever lacks that
    permission inside a section they were allowed to open."""
    is_admin = "system:execute" in user_permissions(user)
    return f"Welcome back, <b>{_esc(user.username)}</b>.", kb.main_menu(is_admin)


async def _dispatch(
    session: AsyncSession, settings: Settings, app_state_obj: AppState, user: User, cb: Callback,
) -> tuple[str, dict]:
    action = cb.action
    if action == "pl":
        return await _projects_list(session, user, cb.int_arg(0))
    if action == "p":
        return await _project_detail(session, user, cb.int_arg(0))
    if action == "mm":
        return await _members_list(session, user, cb.int_arg(0))
    if action == "mc":
        return await _candidates_list(session, user, cb.int_arg(0), cb.int_arg(1))
    if action == "ma":
        return await _pick_candidate(session, user, cb.int_arg(0), cb.int_arg(1))
    if action == "mr":
        return await _confirm_add(session, user, cb.int_arg(0), cb.int_arg(1), cb.role_arg(2))
    if action == "me":
        return await _member_actions(session, user, cb.int_arg(0), cb.int_arg(1))
    if action == "mu_pick":
        return await _role_picker_for_existing(session, user, cb.int_arg(0), cb.int_arg(1))
    if action == "mu":
        return await _change_role(session, user, cb.int_arg(0), cb.int_arg(1), cb.role_arg(2))
    if action == "md":
        return await _confirm_remove_prompt(session, user, cb.int_arg(0), cb.int_arg(1))
    if action == "mdc":
        return await _do_remove(session, user, cb.int_arg(0), cb.int_arg(1))
    if action == "pr":
        return await _repos_list(session, settings, user, cb.int_arg(0), cb.int_arg(1))
    if action == "ra":
        return await _run_analysis(session, settings, app_state_obj, user, cb.int_arg(0))
    if action == "mn":
        return await _main_menu(user)
    if action == "ad":
        return await _admin_home(session, settings, app_state_obj, user)
    if action == "adl":
        return await _admin_links_list(session, user, cb.int_arg(0))
    if action == "adu":
        return await _admin_unlink_prompt(session, user, cb.int_arg(0))
    if action == "aduc":
        return await _admin_unlink_confirmed(session, user, cb.int_arg(0))
    if action == "back":
        # "back"'s own argument is itself an encoded callback_data string
        # (it may contain further colons, e.g. "back:mu:42:5:a"), so it's
        # rejoined rather than treated as a single arg.
        return await _dispatch(session, settings, app_state_obj, user, parse(":".join(cb.args)))
    raise CallbackDataError(f"unknown action {action!r}")


async def _handle_start(session: AsyncSession, client: TelegramClient, chat_id: int, chat_type: str) -> None:
    if chat_type != "private":
        # Never respond in a group/channel, and in particular never hand out
        # its chat id there: TelegramLink.chat_id is the only identifier
        # account access rests on (see models/telegram.py), so it must
        # always name exactly one person. A group's id is shared by every
        # member of that group — handing it out would let anyone who is
        # ever in the group act as whichever Rotsy account an admin later
        # links it to.
        return
    user = await linked_user(session, chat_id)
    if user is None:
        await client.send_message(
            chat_id,
            "You're not linked to a Rotsy account yet.\n\n"
            f"Your chat ID is:\n<code>{chat_id}</code>\n\n"
            "Give this to your Rotsy administrator to link your account "
            "(Settings → Integrations → Telegram).",
        )
        return
    is_admin = "system:execute" in user_permissions(user)
    await client.send_message(
        chat_id, f"Welcome back, <b>{_esc(user.username)}</b>.", reply_markup=kb.main_menu(is_admin),
    )


async def _handle_callback(
    session: AsyncSession, settings: Settings, app_state_obj: AppState, client: TelegramClient, cq: dict,
) -> None:
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    cq_id = cq["id"]
    data = cq.get("data") or ""

    if data == "noop":
        await client.answer_callback_query(cq_id)
        return

    user = await linked_user(session, chat_id)
    if user is None:
        await client.answer_callback_query(cq_id, "Not linked. Contact your Rotsy administrator.")
        return

    try:
        cb = parse(data)
        text, markup = await _dispatch(session, settings, app_state_obj, user, cb)
    except HTTPException as exc:
        text, markup = f"⚠ {exc.detail}", kb.back_only(build("pl", 0))
    except CallbackDataError:
        text, markup = "⚠ That button is no longer valid.", kb.back_only(build("pl", 0))
    except Exception:  # noqa: BLE001 - e.g. a tampered/out-of-range arg reaching the DB; must still answer the callback so Telegram doesn't leave the button spinning
        logger.exception("Unhandled error dispatching callback %r for chat %s", data, chat_id)
        text, markup = "⚠ Something went wrong. Please try again.", kb.back_only(build("pl", 0))

    await client.answer_callback_query(cq_id)
    try:
        await client.edit_message(chat_id, message_id, text, reply_markup=markup)
    except Exception:  # noqa: BLE001 - e.g. "message is not modified"; not worth failing the update over
        logger.debug("edit_message failed for chat %s, message %s", chat_id, message_id, exc_info=True)


async def handle_update(
    session: AsyncSession, settings: Settings, app_state_obj: AppState, client: TelegramClient, update: dict,
) -> None:
    """Single entry point the poll loop calls once per Telegram update."""
    if "callback_query" in update:
        await _handle_callback(session, settings, app_state_obj, client, update["callback_query"])
        return
    message = update.get("message")
    if message is not None:
        # No free-text commands beyond /start — any other text gets the same
        # response (the menu, or the not-linked notice), rather than being
        # silently dropped, which would look broken to someone who just
        # typed something reasonable like "hi" or "/projects".
        await _handle_start(session, client, message["chat"]["id"], message["chat"].get("type", ""))
