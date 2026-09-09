"""Triggering an analysis run, independent of who asked.

Resolving which provider and credential back a ``SonarProject``, finding the
branch's current HEAD, and enqueueing ``clone_and_analyze`` is orchestration,
not HTTP — but it lived in ``routers/sonar.py``, which made the router the
only place that knew how to start an analysis. When the Telegram bot needed
the same thing it had to import the *router function* and call it as a plain
Python function, so ``modules/`` depended on ``routers/`` — backwards
against this project's layering (see AGENTS.md), and close enough to a cycle
that the import had to be deferred to function scope to avoid one.

The logic lives here instead. The router and the bot are now two thin callers
of one implementation, and the authorization check travels *with* the
operation rather than being re-declared (or forgotten) per entry point.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import policy
from ..core.config_store import get_github_app_config
from ..core.jobs import JobQueue
from ..core.source_provider import RepoRef
from ..db.session import get_session_factory
from ..models import GitHubInstallation, GitHubRepository, GitLabRepository, SonarProject, User
from ..modules.github.provider import GitHubProvider
from ..modules.gitlab.provider import GitLabProvider
from ..state import AppState

logger = logging.getLogger(__name__)

SONAR_PROJECT_NOT_FOUND = "Sonar project not found"


async def resolve_repo_by_ids(
    session: AsyncSession, settings: Settings, state: AppState,
    github_repository_id: int | None, gitlab_repository_id: int | None,
):
    """Provider, credential and :class:`RepoRef` for a repository identified
    by its ``(github_repository_id, gitlab_repository_id)`` pair, plus the
    Rotsy Project it is mapped to.

    Identified by ids rather than an existing ``SonarProject`` because a
    repository picked from the global Code Quality section may not have one
    yet — that is what provisioning uses.
    """
    if github_repository_id:
        github_repo = await session.get(GitHubRepository, github_repository_id)
        if github_repo is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Connected GitHub repository is missing")
        # installation_id is NULL for a repo connected by URL (public, no App
        # installation — see connect_public_repository) — expected, not an
        # error; credential_ref="" tells GitHubProvider to act anonymously.
        installation = (
            await session.get(GitHubInstallation, github_repo.installation_id)
            if github_repo.installation_id else None
        )
        if github_repo.installation_id and installation is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "GitHub installation for this repository is missing")
        if state.cache is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")
        github_app_config = await get_github_app_config(session, settings)
        provider = GitHubProvider(github_app_config, state.cache)
        repo_ref = RepoRef(external_id=github_repo.full_name, name=github_repo.full_name.rsplit("/", 1)[-1],
                            default_branch=github_repo.default_branch, private=installation is not None)
        credential_ref = str(installation.installation_id) if installation else ""
        return "github", provider, credential_ref, repo_ref, (github_repo.id, None), github_repo.project_id

    if gitlab_repository_id:
        gitlab_repo = await session.get(GitLabRepository, gitlab_repository_id)
        if gitlab_repo is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Connected GitLab repository is missing")
        provider = GitLabProvider(get_session_factory())
        repo_ref = RepoRef(external_id=gitlab_repo.full_path, name=gitlab_repo.full_path.rsplit("/", 1)[-1],
                            default_branch=gitlab_repo.default_branch, private=True)
        return "gitlab", provider, str(gitlab_repo.id), repo_ref, (None, gitlab_repo.id), gitlab_repo.project_id

    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                         "Repository is missing both a GitHub and GitLab id")


async def resolve_repo(
    session: AsyncSession, settings: Settings, state: AppState, sonar_project: SonarProject,
):
    """:func:`resolve_repo_by_ids` for an existing ``SonarProject``, dropping
    the project id the caller already has."""
    (source_module, provider, credential_ref, repo_ref, repo_ids, _project_id) = await resolve_repo_by_ids(
        session, settings, state, sonar_project.github_repository_id, sonar_project.gitlab_repository_id,
    )
    return source_module, provider, credential_ref, repo_ref, repo_ids


async def run_repository_analysis(
    session: AsyncSession, settings: Settings, state: AppState, user: User,
    sonar_project_id: int, ref: str | None = None,
) -> dict:
    """Authorize, resolve the branch's current HEAD, and enqueue the same
    ``clone_and_analyze`` job a push would.

    ``user`` is required rather than optional: this enqueues real work against
    a specific Project, and every caller has an authenticated principal. The
    membership check happens here — with the operation — so a new entry point
    cannot reach the job queue without it.
    """
    sonar_project = await session.get(SonarProject, sonar_project_id)
    if sonar_project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SONAR_PROJECT_NOT_FOUND)
    await policy.assert_can_run_analysis(session, user, sonar_project.project_id)
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue is not available")

    source_module, provider, credential_ref, repo_ref, repo_ids = await resolve_repo(
        session, settings, state, sonar_project,
    )
    branch = ref or repo_ref.default_branch

    try:
        sha = await provider.get_latest_commit_sha(credential_ref, repo_ref, branch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to resolve latest commit for manual analysis on sonar project %s: %s",
                        sonar_project_id, exc)
        # Every provider call already raises a specific, actionable error
        # (GitHubProviderError/GitHubAuthError/GitLabProviderError — "App not
        # configured", "401 Bad credentials", "branch not found", ...).
        # Replacing it with one generic "verify the connection" sentence
        # threw away exactly the detail needed to fix it. Fall back to the
        # generic message only for an exception with no useful text of its
        # own (e.g. a bare httpx.ConnectTimeout).
        detail = str(exc).strip() or (
            f"Unable to reach {source_module.title()} to find the latest commit on {branch!r}. "
            "Verify the connection."
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail) from exc

    job_id = await JobQueue(state.cache).enqueue(
        "clone_and_analyze",
        {
            "project_id": sonar_project.project_id,
            "source_module": source_module,
            "credential_ref": credential_ref,
            "repo_external_id": repo_ref.external_id,
            "repo_name": repo_ref.name,
            "default_branch": repo_ref.default_branch,
            "ref": branch,
            "sha": sha,
            "trigger": "manual",
            "github_repository_id": repo_ids[0],
            "gitlab_repository_id": repo_ids[1],
        },
    )
    return {"job_id": job_id, "commit_sha": sha, "ref": branch}
