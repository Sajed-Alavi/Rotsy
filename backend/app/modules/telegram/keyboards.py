"""Inline-keyboard builders — pure functions, no I/O.

Each returns a Telegram ``{"inline_keyboard": [[...]]}`` dict. Paged lists
follow the same shape: a row of item buttons (one per line, so the label has
room), then a nav row with "Prev"/"Next" where applicable, then a "Back"
row. ``PAGE_SIZE`` matches ``core/projects.py::search_member_candidates``'s
existing cap, reused rather than inventing a different page size per list.
"""

from __future__ import annotations

from .callback_data import ROLE_CODES, build

PAGE_SIZE = 20

_ROLE_LABELS = {"viewer": "Viewer", "member": "Member", "admin": "Admin"}


def _button(text: str, callback_data: str) -> dict:
    return {"text": text, "callback_data": callback_data}


def _rows(*rows: list[dict]) -> dict:
    return {"inline_keyboard": [r for r in rows if r]}


def _nav_row(base_action: str, project_id: int, page: int, has_more: bool) -> list[dict]:
    row = []
    if page > 0:
        row.append(_button("◀ Prev", build(base_action, project_id, page - 1)))
    if has_more:
        row.append(_button("Next ▶", build(base_action, project_id, page + 1)))
    return row


def main_menu() -> dict:
    return _rows([_button("📁 My Projects", build("pl", 0))])


def projects_list(projects: list[dict], page: int, has_more: bool) -> dict:
    """``projects``: dicts with ``id``, ``name``, ``role`` (role may be
    ``None`` for the global-admin bypass — shown as "admin*")."""
    item_rows = [
        [_button(f"{p['name']} — {p['role'] or 'admin*'}", build("p", p["id"]))]
        for p in projects
    ]
    nav = []
    if page > 0:
        nav.append(_button("◀ Prev", build("pl", page - 1)))
    if has_more:
        nav.append(_button("Next ▶", build("pl", page + 1)))
    return _rows(*item_rows, nav)


def project_detail(project_id: int, can_manage_members: bool, can_view_repos: bool) -> dict:
    rows = []
    if can_manage_members:
        rows.append([_button("👥 Members", build("mm", project_id))])
    if can_view_repos:
        rows.append([_button("📦 Repositories", build("pr", project_id, 0))])
    rows.append([_button("⬅ Back", build("back", build("pl", 0)))])
    return _rows(*rows)


def members_list(project_id: int, members: list[dict], page: int, has_more: bool, can_add: bool) -> dict:
    """``members``: dicts with ``id`` (ProjectMember.id), ``username``, ``project_role``."""
    item_rows = [
        [_button(f"{m['username']} ({m['project_role']})", build("me", project_id, m["id"]))]
        for m in members
    ]
    del page, has_more  # a Project's own membership list isn't paged today — kept as params for a uniform call site
    rows = list(item_rows)
    if can_add:
        rows.append([_button("+ Add member", build("mc", project_id, 0))])
    rows.append([_button("⬅ Back", build("back", build("p", project_id)))])
    return _rows(*rows)


def candidates_list(project_id: int, candidates: list[dict], page: int, has_more: bool) -> dict:
    """``candidates``: dicts with ``id`` (User.id), ``username``."""
    item_rows = [
        [_button(c["username"], build("ma", project_id, c["id"]))]
        for c in candidates
    ]
    nav = _nav_row("mc", project_id, page, has_more)
    back = [_button("⬅ Back", build("back", build("mm", project_id)))]
    return _rows(*item_rows, nav, back)


def role_picker(prefix: str, project_id: int, target_id: int, back_action: str) -> dict:
    """``prefix`` is ``"mr"`` (confirm-add) or ``"mu"`` (change-role);
    ``target_id`` is a User.id for ``mr`` or a ProjectMember.id for ``mu``."""
    rows = [
        [_button(_ROLE_LABELS[role], build(prefix, project_id, target_id, code))]
        for code, role in ROLE_CODES.items()
    ]
    rows.append([_button("⬅ Back", build("back", back_action))])
    return _rows(*rows)


def member_actions(project_id: int, member_id: int) -> dict:
    return _rows(
        [_button("Change role", build("mu_pick", project_id, member_id))],
        [_button("🗑 Remove", build("md", project_id, member_id))],
        [_button("⬅ Back", build("back", build("mm", project_id)))],
    )


def confirm_remove(project_id: int, member_id: int) -> dict:
    return _rows(
        [_button("✅ Yes, remove", build("mdc", project_id, member_id))],
        [_button("Cancel", build("back", build("me", project_id, member_id)))],
    )


def repos_list(project_id: int, repos: list[dict], page: int, has_more: bool, can_run: bool) -> dict:
    """``repos``: dicts with ``full_name`` and ``sonar_project_id`` (may be
    ``None`` — not yet connected to analysis, so no Run Analysis button for
    that row either way). ``can_run`` gates the button on the viewer's own
    role/permissions (checked by the caller) — a viewer sees the same list
    but every row renders as plain, non-actionable text."""
    item_rows = []
    for r in repos:
        if can_run and r.get("sonar_project_id"):
            item_rows.append([_button(f"▶ Run Analysis — {r['full_name']}", build("ra", r["sonar_project_id"]))])
        else:
            item_rows.append([_button(f"· {r['full_name']}", build("noop"))])
    nav = _nav_row("pr", project_id, page, has_more)
    back = [_button("⬅ Back", build("back", build("p", project_id)))]
    return _rows(*item_rows, nav, back)


def back_only(back_action: str) -> dict:
    return _rows([_button("⬅ Back", build("back", back_action))])
