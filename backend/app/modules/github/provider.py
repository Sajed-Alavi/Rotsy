"""GitHub implementation of :class:`app.core.source_provider.SourceProvider`.

``credential_ref`` is a GitHub installation id (stored as a string on the
``Integration`` row) for repos connected via the App — **or an empty
string** for a repository connected by URL with no App installation at all
(see ``routers/github.py:connect_public_repository``). The empty-string case
means every call here falls back to anonymous, unauthenticated access:
cloning works for any public repo, but ``register_webhook`` refuses (GitHub
has no reason to send Rotsy events for a repo it isn't installed on) and
``report_status`` is a no-op (posting a commit status requires write access
Rotsy doesn't have). Automatic push-triggered analysis is therefore only
possible for App-connected repos — this is a GitHub permission-model fact,
not a Rotsy limitation.
"""

from __future__ import annotations

import logging

import httpx

from ...core.cache import Cache
from ...core.config_store import GitHubAppConfig
from ...core.source_provider import RepoRef, WebhookHandle
from ..nexus.base import exec_scanner
from .auth import get_installation_token

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_HEADERS_BASE = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class GitHubProviderError(Exception):
    pass


class GitHubProvider:
    def __init__(self, app_config: GitHubAppConfig, cache: Cache) -> None:
        self._app_config = app_config
        self._cache = cache

    async def _token(self, credential_ref: str) -> str | None:
        """``None`` for a public, installation-less repository — callers use
        this to send requests unauthenticated instead of failing outright."""
        if not credential_ref:
            return None
        return await get_installation_token(
            self._app_config.app_id, self._app_config.private_key, self._cache, int(credential_ref)
        )

    async def _headers(self, credential_ref: str) -> dict[str, str]:
        token = await self._token(credential_ref)
        return {**_HEADERS_BASE, "Authorization": f"Bearer {token}"} if token else dict(_HEADERS_BASE)

    async def list_repositories(self, credential_ref: str) -> list[RepoRef]:
        headers = await self._headers(credential_ref)
        repos: list[RepoRef] = []
        url = f"{_GITHUB_API}/installation/repositories?per_page=100"
        async with httpx.AsyncClient(timeout=15.0) as client:
            while url:
                resp = await client.get(url, headers=headers)
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

    async def get_public_repository(self, full_name: str) -> RepoRef:
        """Look up a repository anonymously by ``owner/name`` and confirm it
        is actually public — used only by the "connect by URL" flow, before
        any ``GitHubRepository`` row exists to derive a ``credential_ref`` from."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_GITHUB_API}/repos/{full_name}", headers=_HEADERS_BASE)
        if resp.status_code == 404:
            raise GitHubProviderError(f"{full_name} was not found, or is private/inaccessible without authentication.")
        if resp.status_code >= 400:
            raise GitHubProviderError(f"Failed to look up {full_name}: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        if data.get("private"):
            raise GitHubProviderError(
                f"{full_name} is private. Connect it through Settings -> Integrations -> GitHub "
                "(install the App on it) instead of by URL."
            )
        return RepoRef(external_id=data["full_name"], name=data["name"],
                        default_branch=data.get("default_branch", "main"), private=False)

    async def register_webhook(self, credential_ref: str, repo: RepoRef, callback_url: str, secret: str) -> WebhookHandle:
        if not credential_ref:
            raise GitHubProviderError(
                f"{repo.external_id} has no GitHub App installation, so GitHub has no reason to send Rotsy "
                "events for it. Automatic push-triggered analysis isn't possible for a repository connected "
                "by URL — use Run Analysis manually, or install the App on it instead."
            )
        # GitHub Apps receive events for every repo they're installed on
        # through the App's single webhook endpoint (configured once, at App
        # creation) — there is no per-repository webhook to create. This
        # exists so the module still satisfies the SourceProvider contract
        # symmetrically with GitLab, which does need one per repo.
        del callback_url, secret
        return WebhookHandle(external_id=f"app-level:{repo.external_id}")

    async def fetch_source(self, credential_ref: str, repo: RepoRef, ref: str, dest_dir: str) -> str:
        token = await self._token(credential_ref)
        clone_url = (f"https://x-access-token:{token}@github.com/{repo.external_id}.git" if token
                     else f"https://github.com/{repo.external_id}.git")
        cmd = ["git", "clone", "--depth", "1", "--branch", ref, "--single-branch", clone_url, dest_dir]
        # Async subprocess, not subprocess.run: this runs on the same event
        # loop as every other job's progress reporting and every request
        # this worker process serves — a blocking clone would stall all of
        # it for as long as the clone takes, not just this job.
        returncode, _stdout, stderr = await exec_scanner(cmd, env={}, timeout=300.0)
        if returncode != 0:
            # Strip the token from any echoed command in stderr, if there was one.
            safe_stderr = stderr.replace(token, "***") if token else stderr
            raise GitHubProviderError(f"git clone failed for {repo.external_id}@{ref}: {safe_stderr[:500]}")
        return dest_dir

    async def list_branches(self, credential_ref: str, repo: RepoRef) -> list[str]:
        headers = await self._headers(credential_ref)
        branches: list[str] = []
        url = f"{_GITHUB_API}/repos/{repo.external_id}/branches?per_page=100"
        async with httpx.AsyncClient(timeout=15.0) as client:
            while url:
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400:
                    raise GitHubProviderError(f"Failed to list branches for {repo.external_id}: {resp.status_code} {resp.text[:300]}")
                branches.extend(b["name"] for b in resp.json())
                url = resp.links.get("next", {}).get("url")
        return branches

    async def get_latest_commit_sha(self, credential_ref: str, repo: RepoRef, ref: str) -> str:
        """The current HEAD sha of ``ref`` — used by the manual "Run Analysis"
        trigger, which has a branch to analyze but (unlike a push webhook) no
        commit sha handed to it by the event."""
        headers = await self._headers(credential_ref)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_GITHUB_API}/repos/{repo.external_id}/commits/{ref}", headers=headers)
        if resp.status_code >= 400:
            raise GitHubProviderError(
                f"Failed to resolve the latest commit on {repo.external_id}@{ref}: "
                f"{resp.status_code} {resp.text[:300]}"
            )
        return resp.json()["sha"]

    async def get_repository_languages(self, credential_ref: str, repo: RepoRef) -> dict[str, float]:
        headers = await self._headers(credential_ref)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_GITHUB_API}/repos/{repo.external_id}/languages", headers=headers)
        if resp.status_code >= 400:
            raise GitHubProviderError(
                f"Failed to fetch languages for {repo.external_id}: {resp.status_code} {resp.text[:300]}"
            )
        return {k: float(v) for k, v in resp.json().items()}

    async def report_status(self, credential_ref: str, repo: RepoRef, sha: str, state: str, description: str, target_url: str) -> None:
        if not credential_ref:
            logger.info("Skipping commit status update for %s@%s — connected by URL, no GitHub App installation.",
                        repo.external_id, sha)
            return
        headers = await self._headers(credential_ref)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_GITHUB_API}/repos/{repo.external_id}/statuses/{sha}",
                headers=headers,
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
