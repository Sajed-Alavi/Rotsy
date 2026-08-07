"""Vendor-agnostic contract every Git source module implements.

Core and the analysis worker depend on this Protocol, never on
``modules.github`` or ``modules.gitlab`` directly — see the import-direction
rule in the architecture doc (modules -> core, never core -> modules, never
module -> module). Adding Bitbucket later means writing a class that
satisfies this Protocol; nothing here changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RepoRef:
    """A source repository, identified in a way that's meaningful to its own
    provider (``external_id`` — e.g. a GitHub full name or a GitLab project
    id) and to Rotsy (``default_branch``)."""

    external_id: str
    name: str
    default_branch: str
    private: bool


@dataclass(frozen=True)
class WebhookHandle:
    external_id: str  # provider-assigned webhook id, needed to update/delete it later


class SourceProvider(Protocol):
    """Implemented by ``modules.github.provider.GitHubProvider`` and the
    future ``modules.gitlab.provider.GitLabProvider``. All methods are async
    — every implementation talks to a remote API or shells out to git."""

    async def list_repositories(self, credential_ref: str) -> list[RepoRef]: ...

    async def register_webhook(self, credential_ref: str, repo: RepoRef, callback_url: str, secret: str) -> WebhookHandle: ...

    async def fetch_source(self, credential_ref: str, repo: RepoRef, ref: str, dest_dir: str) -> str:
        """Shallow-clone ``ref`` into ``dest_dir``; return the path checked out to."""
        ...

    async def get_latest_commit_sha(self, credential_ref: str, repo: RepoRef, ref: str) -> str:
        """Current HEAD sha of ``ref`` — used by manual/"on connect" analysis,
        which has a branch to analyze but no commit sha handed to it by an event."""
        ...

    async def get_repository_languages(self, credential_ref: str, repo: RepoRef) -> dict[str, float]:
        """Provider-reported language breakdown (byte count or percentage,
        whichever the provider's API returns — callers only compare relative
        magnitude). Used to auto-pick a Sonar language on first connect
        without cloning just to inspect files."""
        ...

    async def report_status(self, credential_ref: str, repo: RepoRef, sha: str, state: str, description: str, target_url: str) -> None: ...
