"""The repositories connected to a Project, as one uniform list.

GitHub and GitLab repositories live in separate tables with different column
names, and whether a push actually reaches Rotsy is decided differently for
each (an App-level webhook for GitHub, a per-repository one for GitLab). This
flattens both into one row shape so callers do not repeat that per-provider
knowledge.

Extracted from ``routers/projects.py`` because the Telegram bot needs the
same list and was reaching into the router to get it, inverting this
project's layering (see ``services/analysis.py`` for the same story).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..core.config_store import get_github_app_config
from ..models import GitHubRepository, GitLabRepository, SonarProject


def connected_repo_row(
    source_module: str, repo_id: int, full_name: str, default_branch: str,
    has_delivery_mechanism: bool, sp: SonarProject | None, created_at,
) -> dict[str, Any]:
    return {
        "source_module": source_module,
        "repository_id": repo_id,
        "full_name": full_name,
        "default_branch": default_branch,
        # A repo needs both the delivery mechanism (App installation /
        # webhook) and the per-repository toggle on to actually auto-analyze.
        "auto_analyze_on_push": has_delivery_mechanism and (sp is None or sp.auto_analyze_enabled),
        # Exposed separately from the combined flag above so the UI can tell
        # "never turned on" apart from "turned on, but the webhook that would
        # actually deliver the push isn't there" — those look identical
        # folded into one boolean, and only one of them is fixed by flipping
        # the toggle again.
        "webhook_registered": has_delivery_mechanism,
        "sonar_project_id": sp.id if sp else None,
        "language": sp.language if sp else None,
        "auto_analyze_enabled": sp.auto_analyze_enabled if sp else None,
        "auto_analyze_branches": sp.auto_analyze_branches if sp else None,
        "quality_gate_preset": sp.quality_gate_preset if sp else None,
        "created_at": created_at,
    }


async def list_connected_repositories(
    session: AsyncSession, settings: Settings, project_id: int,
) -> list[dict]:
    """Every repository connected to this Project — a Project is a grouping,
    so this can be one repo or a thousand, each independently analyzed and
    potentially in a different language."""
    await projects_core.get_project(session, project_id)  # 404s if missing

    sonar_by_github: dict[int, SonarProject] = {}
    sonar_by_gitlab: dict[int, SonarProject] = {}
    for sp in (await session.execute(select(SonarProject).where(SonarProject.project_id == project_id))).scalars():
        if sp.github_repository_id:
            sonar_by_github[sp.github_repository_id] = sp
        if sp.gitlab_repository_id:
            sonar_by_gitlab[sp.gitlab_repository_id] = sp

    # App-wide, not per-repository: an installed repo with no App-level
    # webhook (the App Manifest flow was completed without one — an
    # unreachable-from-GitHub WEBHOOK_BASE_URL at the time makes this the
    # default, silently) will never actually receive a push event, no
    # matter how "installed" it looks. Checked once, not per row.
    github_has_webhook = (await get_github_app_config(session, settings)).has_webhook()

    out: list[dict] = []
    github_repos = (
        await session.execute(select(GitHubRepository).where(GitHubRepository.project_id == project_id))
    ).scalars().all()
    for r in github_repos:
        sp = sonar_by_github.get(r.id)
        out.append(connected_repo_row(
            "github", r.id, r.full_name, r.default_branch,
            r.installation_id is not None and github_has_webhook, sp, r.created_at,
        ))

    gitlab_repos = (
        await session.execute(select(GitLabRepository).where(GitLabRepository.project_id == project_id))
    ).scalars().all()
    for r in gitlab_repos:
        sp = sonar_by_gitlab.get(r.id)
        out.append(connected_repo_row(
            "gitlab", r.id, r.full_path, r.default_branch, r.webhook_id is not None, sp, r.created_at,
        ))

    out.sort(key=lambda row: row["full_name"])
    return out
