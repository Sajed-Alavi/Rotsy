"""GitHub App connect flow, repository discovery/mapping, webhook ingress.

Thin HTTP layer: auth/token logic lives in ``modules/github/auth.py``,
discovery in ``modules/github/provider.py``, signature/event parsing in
``modules/github/webhooks.py``. This router only translates HTTP <-> those
calls and persists ``GitHubInstallation``/``GitHubRepository`` rows.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..core.cache import Cache
from ..core.jobs import JobQueue
from ..dependencies import RequirePermission, get_session, get_settings
from ..models import GitHubInstallation, GitHubRepository, Integration
from ..modules.github.auth import GitHubAuthError, get_installation_token, install_url
from ..modules.github.provider import GitHubProvider
from ..modules.github.webhooks import normalize_event, verify_signature
from ..state import app_state, AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modules/github", tags=["github"])


class RepoOut(BaseModel):
    id: int
    full_name: str
    default_branch: str
    project_id: int | None


class MapRepoBody(BaseModel):
    project_id: int


class InstallationOut(BaseModel):
    id: int
    installation_id: int
    account_login: str


@router.get("/installations", response_model=list[InstallationOut],
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_installations(session: Annotated[AsyncSession, Depends(get_session)]) -> list[InstallationOut]:
    rows = (await session.execute(select(GitHubInstallation))).scalars().all()
    return [InstallationOut(id=r.id, installation_id=r.installation_id, account_login=r.account_login) for r in rows]


@router.get("/repositories", response_model=list[RepoOut],
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_repositories(
    session: Annotated[AsyncSession, Depends(get_session)],
    unmapped: bool = False,
) -> list[RepoOut]:
    """All discovered repositories across every installation — the pool a
    project's "connect a repository" step picks from. ``unmapped=true``
    narrows to ones not yet attached to any Project."""
    stmt = select(GitHubRepository)
    if unmapped:
        stmt = stmt.where(GitHubRepository.project_id.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    return [RepoOut(id=r.id, full_name=r.full_name, default_branch=r.default_branch, project_id=r.project_id)
            for r in rows]


@router.get("/status", dependencies=[Depends(RequirePermission("projects:read"))])
async def github_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Connection status for the Settings -> Integrations -> GitHub card.

    "Configured" means the App credentials are present; "connected" means at
    least one installation has completed the GitHub-side install flow —
    App configuration alone doesn't prove GitHub can reach Rotsy.
    """
    configured = bool(settings.GITHUB_APP_ID and settings.GITHUB_APP_PRIVATE_KEY and settings.GITHUB_WEBHOOK_SECRET)
    installations_count = 0
    if configured:
        installations_count = await session.scalar(
            select(func.count()).select_from(GitHubInstallation)
        ) or 0
    return {
        "configured": configured,
        "connected": installations_count > 0,
        "installations_count": installations_count,
        "app_slug": settings.GITHUB_APP_SLUG or None,
    }


@router.get("/install-url", dependencies=[Depends(RequirePermission("projects:read"))])
async def get_install_url(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    try:
        return {"url": install_url(settings)}
    except GitHubAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/callback", dependencies=[Depends(RequirePermission("projects:write"))])
async def install_callback(
    installation_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[AppState, Depends(app_state)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """GitHub redirects here after the operator installs the App.

    GitHub's own redirect only ever carries ``installation_id`` (plus
    ``setup_action``) — there is no way to thread a specific Project through
    a plain App-install link. So installation is project-independent: one
    installation can back repositories that end up mapped to many different
    Projects. The per-project ``github`` Integration row is created lazily,
    the first time a repository from this installation is actually mapped
    to a Project (see :func:`map_repository`), not here.
    """
    existing = await session.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    )
    if existing is not None:
        return {"installation_id": existing.installation_id, "account_login": existing.account_login}

    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")

    try:
        account_login = await _resolve_account_login(settings, state.cache, installation_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to resolve account login for installation %s", installation_id)
        account_login = ""

    row = GitHubInstallation(installation_id=installation_id, account_login=account_login)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"installation_id": row.installation_id, "account_login": row.account_login}


async def _resolve_account_login(settings: Settings, cache: Cache, installation_id: int) -> str:
    token = await get_installation_token(settings, cache, installation_id)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://api.github.com/installation/repositories?per_page=1",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    if resp.status_code >= 400:
        return ""
    repos = resp.json().get("repositories", [])
    return repos[0]["owner"]["login"] if repos else ""


@router.post("/installations/{installation_id}/sync", response_model=list[RepoOut],
             dependencies=[Depends(RequirePermission("projects:write"))])
async def sync_repositories(
    installation_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[AppState, Depends(app_state)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[RepoOut]:
    """Discover (or refresh) the repositories this installation can see."""
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")

    installation = await session.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    )
    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Installation not found")

    provider = GitHubProvider(settings, state.cache)
    repos = await provider.list_repositories(str(installation_id))

    existing = {
        row.full_name: row
        for row in (
            await session.execute(
                select(GitHubRepository).where(GitHubRepository.installation_id == installation.id)
            )
        ).scalars()
    }
    for repo in repos:
        if repo.external_id in existing:
            existing[repo.external_id].default_branch = repo.default_branch
        else:
            session.add(GitHubRepository(
                installation_id=installation.id,
                full_name=repo.external_id,
                default_branch=repo.default_branch,
            ))
    await session.commit()

    rows = (
        await session.execute(
            select(GitHubRepository).where(GitHubRepository.installation_id == installation.id)
        )
    ).scalars().all()
    return [RepoOut(id=r.id, full_name=r.full_name, default_branch=r.default_branch, project_id=r.project_id)
            for r in rows]


@router.post("/repositories/{repo_id}/map", response_model=RepoOut,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def map_repository(
    repo_id: int,
    body: MapRepoBody,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RepoOut:
    repo = await session.get(GitHubRepository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    await projects_core.get_project(session, body.project_id)  # 404s if missing

    installation = await session.get(GitHubInstallation, repo.installation_id)
    repo.project_id = body.project_id

    # First repository mapped to this project connects the project-scoped
    # "github" Integration row — installation itself is project-independent
    # (see models/github.py), so this is the earliest point a Project can
    # correctly be said to "have" a GitHub integration.
    existing_integration = await session.scalar(
        select(Integration).where(Integration.project_id == body.project_id, Integration.module_key == "github")
    )
    if existing_integration is None:
        await projects_core.connect_integration(
            session, body.project_id, "github", "source", config={},
            credential_ref=str(installation.installation_id) if installation else None,
        )

    await session.commit()
    await session.refresh(repo)
    return RepoOut(id=repo.id, full_name=repo.full_name, default_branch=repo.default_branch, project_id=repo.project_id)


@router.post("/webhooks", status_code=status.HTTP_202_ACCEPTED, include_in_schema=True)
async def github_webhook(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict[str, Any]:
    """Receive a GitHub App webhook delivery.

    Authenticated by ``X-Hub-Signature-256`` against ``GITHUB_WEBHOOK_SECRET``
    — not a user session, GitHub calls this machine-to-machine. Always 202s
    once the signature checks out, including for event types we don't act on
    (same policy as the Nexus webhook receiver: a receiver that errors on
    uninteresting events gets disabled by the sender).
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(settings.GITHUB_WEBHOOK_SECRET, body, signature):
        delivery_id = request.headers.get("X-GitHub-Delivery", "?")
        logger.warning("Rejected a GitHub webhook delivery with a bad or missing signature (delivery %s)", delivery_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    payload = await request.json()

    if state.cache is not None and delivery_id:
        dedupe_key = f"github:delivery:{delivery_id}"
        if await state.cache.get_json(dedupe_key):
            return {"status": "duplicate"}
        await state.cache.set_json(dedupe_key, True, ttl=86400)

    event = normalize_event(event_type, payload)
    if event is None:
        return {"status": "ignored"}

    repo = await session.scalar(
        select(GitHubRepository).where(GitHubRepository.full_name == event.repo_full_name)
    )
    if repo is None or repo.project_id is None:
        # Discovered-but-unmapped, or not discovered yet — nothing to analyze
        # against. Not an error: the operator hasn't mapped this repo yet.
        return {"status": "unmapped"}

    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue cache not initialised")

    # Idempotency: JobQueue.enqueue() has no dedupe of its own, and GitHub's
    # at-least-once delivery (plus a possible retry after our 202 but before
    # the sender sees it) can otherwise double-analyze the same commit.
    analysis_key = f"github:analyzed:{repo.id}:{event.sha}"
    if await state.cache.get_json(analysis_key):
        return {"status": "duplicate"}
    await state.cache.set_json(analysis_key, True, ttl=3600)

    queue = JobQueue(state.cache)
    await queue.enqueue(
        "clone_and_analyze",
        {
            "project_id": repo.project_id,
            "github_repository_id": repo.id,
            "repo_full_name": event.repo_full_name,
            "ref": event.ref,
            "sha": event.sha,
        },
    )
    return {"status": "queued"}
