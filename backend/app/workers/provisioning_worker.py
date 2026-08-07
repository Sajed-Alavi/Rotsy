"""``provision_repository`` job: background Sonar provisioning for one repo.

Used by bulk-connect (``routers/github.py``/``routers/gitlab.py`` bulk
endpoints) so connecting hundreds or thousands of repositories to a Project
doesn't block one HTTP request doing hundreds of sequential network calls
(language detection, Sonar project creation, first analysis kickoff) —
each repository gets its own job on the existing queue instead. Single-repo
connect flows still call ``auto_provision_and_analyze`` inline directly, since
one repo's worth of work is fast enough not to need this.

Wraps the exact same :func:`app.modules.sonar.provisioning.auto_provision_and_analyze`
used by the inline path — one implementation, just two ways to schedule it.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ..core.source_provider import RepoRef
from ..db.session import get_session_factory
from ..modules.sonar.provisioning import auto_provision_and_analyze
from .analysis_worker import _build_provider, _settings_and_cache

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., Awaitable[None]]


async def handle_provision_repository(job, progress: ProgressCallback) -> dict:
    payload = job.payload
    project_id = payload["project_id"]
    source_module = payload["source_module"]
    credential_ref = payload["credential_ref"]
    repo_ref = RepoRef(
        external_id=payload["repo_external_id"], name=payload["repo_name"],
        default_branch=payload["default_branch"], private=payload.get("private", True),
    )
    github_repository_id = payload.get("github_repository_id")
    gitlab_repository_id = payload.get("gitlab_repository_id")

    settings, cache = _settings_and_cache()
    factory = get_session_factory()

    await progress(10, f"provisioning {repo_ref.external_id}")
    async with factory() as session:
        provider = await _build_provider(source_module, session, settings, cache)
        await auto_provision_and_analyze(
            session, cache, settings, project_id, provider, credential_ref, repo_ref, source_module,
            github_repository_id=github_repository_id, gitlab_repository_id=gitlab_repository_id,
        )
    await progress(100, f"done — {repo_ref.external_id}")
    return {"repo_external_id": repo_ref.external_id}
