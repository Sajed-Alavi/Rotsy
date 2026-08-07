"""GitLab implementation of :class:`app.core.source_provider.SourceProvider`.

Unlike the GitHub App (one instance-wide installation token, minted on
demand), a GitLab connection is a plain PAT stored — encrypted — directly on
each :class:`~app.models.gitlab.GitLabRepository` row. So ``credential_ref``
here is always a ``GitLabRepository.id`` (as a string): every method looks
the row up, decrypts its token, and talks to that row's own ``gitlab_url``.
This is also why the provider takes a session *factory* rather than a live
session — each call opens its own short-lived session rather than assuming
one is held open for the provider's whole lifetime, matching how the rest of
the codebase scopes sessions per-operation rather than per-object.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Callable
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...core.config_store import decrypt_password
from ...core.source_provider import RepoRef, WebhookHandle
from ...models import GitLabRepository

logger = logging.getLogger(__name__)

_HEADERS_ACCEPT = {"Accept": "application/json"}


class GitLabProviderError(Exception):
    pass


class GitLabProvider:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _repo_row(self, credential_ref: str) -> GitLabRepository:
        async with self._session_factory() as session:
            row = await session.get(GitLabRepository, int(credential_ref))
        if row is None:
            raise GitLabProviderError(f"GitLabRepository {credential_ref} not found")
        return row

    async def _resolve(self, credential_ref: str) -> tuple[GitLabRepository, str]:
        row = await self._repo_row(credential_ref)
        token = decrypt_password(row.encrypted_token, get_settings())
        return row, token

    async def list_repositories_for_connection(self, connection_id: str) -> list[RepoRef]:
        """Not part of the :class:`SourceProvider` protocol (a GitLab
        connection, unlike a GitHub installation, isn't itself a repository
        scope you clone from) — used by ``routers/gitlab.py`` to discover
        repositories under a user-level connection's token."""
        from ...models import GitLabConnection

        async with self._session_factory() as session:
            conn = await session.get(GitLabConnection, int(connection_id))
        if conn is None:
            raise GitLabProviderError(f"GitLabConnection {connection_id} not found")
        token = decrypt_password(conn.encrypted_token, get_settings())

        repos: list[RepoRef] = []
        page = 1
        async with httpx.AsyncClient(base_url=conn.gitlab_url, timeout=15.0) as client:
            while True:
                resp = await client.get(
                    "/api/v4/projects",
                    params={"membership": "true", "per_page": 100, "page": page, "simple": "true"},
                    headers={**_HEADERS_ACCEPT, "PRIVATE-TOKEN": token},
                )
                if resp.status_code >= 400:
                    raise GitLabProviderError(f"Failed to list projects: {resp.status_code} {resp.text[:300]}")
                data = resp.json()
                if not data:
                    break
                for proj in data:
                    repos.append(RepoRef(
                        external_id=str(proj["id"]),  # GitLab numeric project id — full_path resolved separately
                        name=proj["name"],
                        default_branch=proj.get("default_branch") or "main",
                        private=proj.get("visibility") != "public",
                    ))
                if len(data) < 100:
                    break
                page += 1
        return repos

    async def list_repositories(self, credential_ref: str) -> list[RepoRef]:
        # Per-repository credential model: there is nothing to "discover"
        # from a single repository's own token beyond itself.
        row, token = await self._resolve(credential_ref)
        async with httpx.AsyncClient(base_url=row.gitlab_url, timeout=15.0) as client:
            resp = await client.get(
                f"/api/v4/projects/{row.gitlab_project_id}",
                headers={**_HEADERS_ACCEPT, "PRIVATE-TOKEN": token},
            )
        if resp.status_code >= 400:
            raise GitLabProviderError(f"Failed to fetch project: {resp.status_code} {resp.text[:300]}")
        proj = resp.json()
        return [RepoRef(external_id=row.full_path, name=proj["name"],
                         default_branch=proj.get("default_branch") or "main",
                         private=proj.get("visibility") != "public")]

    async def register_webhook(self, credential_ref: str, repo: RepoRef, callback_url: str, secret: str) -> WebhookHandle:
        row, token = await self._resolve(credential_ref)
        async with httpx.AsyncClient(base_url=row.gitlab_url, timeout=15.0) as client:
            resp = await client.post(
                f"/api/v4/projects/{row.gitlab_project_id}/hooks",
                headers={**_HEADERS_ACCEPT, "PRIVATE-TOKEN": token},
                json={"url": callback_url, "token": secret, "push_events": True, "enable_ssl_verification": True},
            )
        if resp.status_code >= 400:
            raise GitLabProviderError(f"Failed to register webhook: {resp.status_code} {resp.text[:300]}")
        return WebhookHandle(external_id=str(resp.json()["id"]))

    async def fetch_source(self, credential_ref: str, repo: RepoRef, ref: str, dest_dir: str) -> str:
        row, token = await self._resolve(credential_ref)
        host = urlparse(row.gitlab_url).netloc
        clone_url = f"{urlparse(row.gitlab_url).scheme}://oauth2:{quote(token, safe='')}@{host}/{row.full_path}.git"
        cmd = ["git", "clone", "--depth", "1", "--branch", ref, "--single-branch", clone_url, dest_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
        if result.returncode != 0:
            safe_stderr = result.stderr.replace(token, "***")
            raise GitLabProviderError(f"git clone failed for {row.full_path}@{ref}: {safe_stderr[:500]}")
        return dest_dir

    async def get_latest_commit_sha(self, credential_ref: str, repo: RepoRef, ref: str) -> str:
        row, token = await self._resolve(credential_ref)
        async with httpx.AsyncClient(base_url=row.gitlab_url, timeout=15.0) as client:
            resp = await client.get(
                f"/api/v4/projects/{row.gitlab_project_id}/repository/commits/{quote(ref, safe='')}",
                headers={**_HEADERS_ACCEPT, "PRIVATE-TOKEN": token},
            )
        if resp.status_code >= 400:
            raise GitLabProviderError(
                f"Failed to resolve the latest commit on {row.full_path}@{ref}: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json()["id"]

    async def get_repository_languages(self, credential_ref: str, repo: RepoRef) -> dict[str, float]:
        row, token = await self._resolve(credential_ref)
        async with httpx.AsyncClient(base_url=row.gitlab_url, timeout=15.0) as client:
            resp = await client.get(
                f"/api/v4/projects/{row.gitlab_project_id}/languages",
                headers={**_HEADERS_ACCEPT, "PRIVATE-TOKEN": token},
            )
        if resp.status_code >= 400:
            raise GitLabProviderError(f"Failed to fetch languages: {resp.status_code} {resp.text[:300]}")
        return {k: float(v) for k, v in resp.json().items()}

    async def report_status(self, credential_ref: str, repo: RepoRef, sha: str, state: str, description: str, target_url: str) -> None:
        row, token = await self._resolve(credential_ref)
        # GitLab's commit-status states are a stricter enum than GitHub's;
        # map ours onto theirs rather than passing through unchanged.
        gitlab_state = {"success": "success", "failure": "failed", "error": "failed", "pending": "pending"}.get(state, "failed")
        async with httpx.AsyncClient(base_url=row.gitlab_url, timeout=15.0) as client:
            resp = await client.post(
                f"/api/v4/projects/{row.gitlab_project_id}/statuses/{sha}",
                headers={**_HEADERS_ACCEPT, "PRIVATE-TOKEN": token},
                json={"state": gitlab_state, "description": description[:255], "target_url": target_url or None,
                      "context": "rotsy/analysis"},
            )
        if resp.status_code >= 400:
            logger.warning("Failed to report commit status for %s@%s: %s %s",
                            row.full_path, sha, resp.status_code, resp.text[:300])
