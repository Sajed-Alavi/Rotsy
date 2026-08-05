"""GitHub implementation of :class:`app.core.source_provider.SourceProvider`.

``credential_ref`` for this provider is always a GitHub installation id
(stored as a string on the ``Integration`` row) — App auth has no per-project
secret to hold, unlike a Sonar token or a GitLab PAT, so there is nothing else
to reference.
"""

from __future__ import annotations

import logging
import subprocess

import httpx

from ...config import Settings
from ...core.cache import Cache
from ...core.source_provider import RepoRef, WebhookHandle
from .auth import get_installation_token

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_HEADERS_BASE = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class GitHubProviderError(Exception):
    pass


class GitHubProvider:
    def __init__(self, settings: Settings, cache: Cache) -> None:
        self._settings = settings
        self._cache = cache

    async def _token(self, credential_ref: str) -> str:
        return await get_installation_token(self._settings, self._cache, int(credential_ref))

    async def list_repositories(self, credential_ref: str) -> list[RepoRef]:
        token = await self._token(credential_ref)
        repos: list[RepoRef] = []
        url = f"{_GITHUB_API}/installation/repositories?per_page=100"
        async with httpx.AsyncClient(timeout=15.0) as client:
            while url:
                resp = await client.get(url, headers={**_HEADERS_BASE, "Authorization": f"Bearer {token}"})
                if resp.status_code >= 400:
                    raise GitHubProviderError(f"Failed to list repositories: {resp.status_code} {resp.text[:300]}")
                data = resp.json()
                for repo in data.get("repositories", []):
                    repos.append(RepoRef(
                        external_id=repo["full_name"],
                        name=repo["name"],
                        default_branch=repo.get("default_branch", "main"),
                        private=repo.get("private", True),
                    ))
                url = resp.links.get("next", {}).get("url")
        return repos

    async def register_webhook(self, credential_ref: str, repo: RepoRef, callback_url: str, secret: str) -> WebhookHandle:
        # GitHub Apps receive events for every repo they're installed on
        # through the App's single webhook endpoint (configured once, at App
        # creation) — there is no per-repository webhook to create. This
        # exists so the module still satisfies the SourceProvider contract
        # symmetrically with GitLab, which does need one per repo.
        del credential_ref, callback_url, secret
        return WebhookHandle(external_id=f"app-level:{repo.external_id}")

    async def fetch_source(self, credential_ref: str, repo: RepoRef, ref: str, dest_dir: str) -> str:
        token = await self._token(credential_ref)
        clone_url = f"https://x-access-token:{token}@github.com/{repo.external_id}.git"
        cmd = ["git", "clone", "--depth", "1", "--branch", ref, "--single-branch", clone_url, dest_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
        if result.returncode != 0:
            # Strip the URL from any echoed command in stderr — it embeds the token.
            safe_stderr = result.stderr.replace(token, "***")
            raise GitHubProviderError(f"git clone failed for {repo.external_id}@{ref}: {safe_stderr[:500]}")
        return dest_dir

    async def report_status(self, credential_ref: str, repo: RepoRef, sha: str, state: str, description: str, target_url: str) -> None:
        token = await self._token(credential_ref)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_GITHUB_API}/repos/{repo.external_id}/statuses/{sha}",
                headers={**_HEADERS_BASE, "Authorization": f"Bearer {token}"},
                json={
                    "state": state,  # "pending" | "success" | "failure" | "error"
                    "description": description[:140],
                    "target_url": target_url,
                    "context": "rotsy/analysis",
                },
            )
        if resp.status_code >= 400:
            logger.warning("Failed to report commit status for %s@%s: %s %s",
                            repo.external_id, sha, resp.status_code, resp.text[:300])
