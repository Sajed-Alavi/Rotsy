"""GitLab connection management (user-level PAT and repository-level PAT),
repository discovery/mapping, and webhook ingress.

Thin HTTP layer: token storage/decryption lives in ``core.config_store``'s
Fernet helpers, discovery/clone/webhook calls in
``modules/gitlab/provider.py``, signature verification in
``modules/gitlab/webhooks.py``. Mirrors ``routers/github.py``'s shape so the
two source integrations read the same way, with one structural difference:
GitLab has no App-level webhook, so each repository gets its own webhook
(and the receiver URL carries the repository id to know which secret to
check against).
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..core.config_store import decrypt_password, encrypt_password
from ..core.jobs import JobQueue
from ..core.source_provider import RepoRef
from ..db.session import get_session_factory
from ..dependencies import RequirePermission, get_session, get_settings
from ..models import GitLabConnection, GitLabRepository, Integration, SonarProject
from ..modules.gitlab.provider import GitLabProvider, GitLabProviderError
from ..modules.gitlab.webhooks import normalize_push_event, verify_token
from ..modules.sonar.provisioning import auto_provision_and_analyze
from ..state import app_state, AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modules/gitlab", tags=["gitlab"])

_UNREACHABLE_MESSAGE = "Unable to reach GitLab. Verify the server URL, token, and network connectivity."
_REPOSITORY_NOT_FOUND = "Repository not found"


class _StrippedTokenMixin(BaseModel):
    """Copy-pasting a token from a GitLab page routinely drags in a
    trailing newline or space (triple-click select, some browsers' "Copy"
    button); GitLab rejects that as an invalid token with an opaque 401,
    which looks identical to actually typing the wrong token. Strip it at
    the boundary so that class of mistake can't happen."""
    @field_validator("token", mode="before", check_fields=False)
    @classmethod
    def _strip_token(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v


class ConnectionCreate(_StrippedTokenMixin):
    gitlab_url: str = Field(..., min_length=1, max_length=512)
    token: str = Field(..., min_length=1, max_length=256)


class ConnectionOut(BaseModel):
    id: int
    gitlab_url: str
    account_username: str


class RepoOut(BaseModel):
    id: int
    full_path: str
    default_branch: str
    project_id: int | None
    connection_id: int | None
    # False here (unmapped, or mapped but registration failed) means push
    # will not trigger analysis for this repository — _try_register_webhook
    # logs that and moves on rather than raising, so this is the only place
    # that failure is visible unless the UI surfaces it.
    webhook_registered: bool = False


class MapRepoBody(BaseModel):
    project_id: int


class RepositoryConnect(_StrippedTokenMixin):
    """Repository-level mode: one PAT for exactly one repository, managed
    independently of any user-level connection."""
    gitlab_url: str = Field(..., min_length=1, max_length=512)
    full_path: str = Field(..., min_length=1, max_length=255, description='e.g. "group/project"')
    token: str = Field(..., min_length=1, max_length=256)


async def _fetch_project(gitlab_url: str, token: str, path_or_id: str) -> dict:
    async with httpx.AsyncClient(base_url=gitlab_url, timeout=15.0) as client:
        resp = await client.get(
            f"/api/v4/projects/{quote(path_or_id, safe='')}",
            headers={"PRIVATE-TOKEN": token, "Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise GitLabProviderError(f"{resp.status_code} {resp.text[:300]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Status (Settings -> Integrations -> GitLab card)
# ---------------------------------------------------------------------------
@router.get("/status", dependencies=[Depends(RequirePermission("projects:read"))])
async def gitlab_status(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    connections = (await session.execute(select(GitLabConnection))).scalars().all()
    return {
        "configured": len(connections) > 0,
        "connections": [{"id": c.id, "gitlab_url": c.gitlab_url, "account_username": c.account_username}
                         for c in connections],
        "connected": len(connections) > 0,
    }


# ---------------------------------------------------------------------------
# User-level connection (one PAT -> discover many repositories)
# ---------------------------------------------------------------------------
@router.post("/connections", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def create_connection(
    body: ConnectionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionOut:
    gitlab_url = body.gitlab_url.rstrip("/")
    # http:// is deliberately accepted, not just https:// — self-managed GitLab on
    # a trusted internal network (the common case this module is built for) is
    # routinely plain HTTP; this validates URL *format*, not transport security.
    if not gitlab_url.startswith(("http://", "https://")):  # NOSONAR
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "GitLab URL must start with http:// or https://")

    try:
        async with httpx.AsyncClient(base_url=gitlab_url, timeout=15.0) as client:
            resp = await client.get("/api/v4/user", headers={"PRIVATE-TOKEN": body.token})
        if resp.status_code >= 400:
            raise GitLabProviderError(f"{resp.status_code} {resp.text[:300]}")
        username = resp.json().get("username", "")
    except (httpx.HTTPError, GitLabProviderError) as exc:
        logger.warning("GitLab connection test failed for %s: %s", gitlab_url, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _UNREACHABLE_MESSAGE) from exc

    row = GitLabConnection(gitlab_url=gitlab_url, account_username=username,
                            encrypted_token=encrypt_password(body.token, settings))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ConnectionOut(id=row.id, gitlab_url=row.gitlab_url, account_username=row.account_username)


@router.get("/connections",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_connections(session: Annotated[AsyncSession, Depends(get_session)]) -> list[ConnectionOut]:
    rows = (await session.execute(select(GitLabConnection))).scalars().all()
    return [ConnectionOut(id=r.id, gitlab_url=r.gitlab_url, account_username=r.account_username) for r in rows]


@router.post("/connections/{connection_id}/sync",
             dependencies=[Depends(RequirePermission("projects:write"))])
async def sync_repositories(
    connection_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[RepoOut]:
    """Discover (or refresh) the repositories this connection's token can see."""
    connection = await session.get(GitLabConnection, connection_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")

    provider = GitLabProvider(get_session_factory())
    try:
        repos = await provider.list_repositories_for_connection(str(connection_id))
    except GitLabProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _UNREACHABLE_MESSAGE) from exc

    token = decrypt_password(connection.encrypted_token, settings)
    existing = {
        row.gitlab_project_id: row
        for row in (
            await session.execute(select(GitLabRepository).where(GitLabRepository.connection_id == connection_id))
        ).scalars()
    }
    for repo in repos:
        gitlab_project_id = int(repo.external_id)
        if gitlab_project_id in existing:
            # Each GitLabRepository carries its own encrypted *copy* of the
            # connection's token (see the model docstring), written once at
            # discovery. Refreshing only default_branch here left that copy
            # to silently rot the moment the connection's token was rotated —
            # every later clone/webhook/status-update call for the repo kept
            # authenticating with the dead copy and failing, with nothing to
            # tell the operator why or to fix it short of the per-repository
            # "reconnect" endpoint. Re-sync is exactly the moment we already
            # have a known-good, freshly re-verified token in hand (the
            # list_repositories_for_connection call above would have raised
            # if it weren't) — there's no reason not to propagate it.
            existing[gitlab_project_id].default_branch = repo.default_branch
            existing[gitlab_project_id].encrypted_token = encrypt_password(token, settings)
            continue
        # full_path isn't in the /projects list response's RepoRef mapping
        # (external_id there is the numeric id) — resolve it once per new repo.
        try:
            detail = await _fetch_project(connection.gitlab_url, token, str(gitlab_project_id))
        except GitLabProviderError:
            continue
        session.add(GitLabRepository(
            connection_id=connection_id,
            gitlab_url=connection.gitlab_url,
            gitlab_project_id=gitlab_project_id,
            full_path=detail["path_with_namespace"],
            default_branch=repo.default_branch,
            encrypted_token=encrypt_password(token, settings),
        ))
    await session.commit()

    rows = (
        await session.execute(select(GitLabRepository).where(GitLabRepository.connection_id == connection_id))
    ).scalars().all()
    return [RepoOut(id=r.id, full_path=r.full_path, default_branch=r.default_branch,
                     project_id=r.project_id, connection_id=r.connection_id,
                     webhook_registered=r.webhook_id is not None) for r in rows]


# ---------------------------------------------------------------------------
# Repository-level connection (one PAT for exactly one repository)
# ---------------------------------------------------------------------------
@router.post("/repositories", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def connect_repository(
    body: RepositoryConnect,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RepoOut:
    gitlab_url = body.gitlab_url.rstrip("/")
    # http:// is deliberately accepted, not just https:// — self-managed GitLab on
    # a trusted internal network (the common case this module is built for) is
    # routinely plain HTTP; this validates URL *format*, not transport security.
    if not gitlab_url.startswith(("http://", "https://")):  # NOSONAR
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "GitLab URL must start with http:// or https://")

    try:
        detail = await _fetch_project(gitlab_url, body.token, body.full_path)
    except GitLabProviderError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _UNREACHABLE_MESSAGE) from exc

    existing = await session.scalar(
        select(GitLabRepository).where(GitLabRepository.full_path == detail["path_with_namespace"])
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This repository is already connected")

    row = GitLabRepository(
        connection_id=None,
        gitlab_url=gitlab_url,
        gitlab_project_id=detail["id"],
        full_path=detail["path_with_namespace"],
        default_branch=detail.get("default_branch") or "main",
        encrypted_token=encrypt_password(body.token, settings),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return RepoOut(id=row.id, full_path=row.full_path, default_branch=row.default_branch,
                    project_id=row.project_id, connection_id=row.connection_id,
                    webhook_registered=False)  # not registered until this repo is mapped to a Project


@router.get("/repositories",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_repositories(
    session: Annotated[AsyncSession, Depends(get_session)],
    unmapped: bool = False,
) -> list[RepoOut]:
    stmt = select(GitLabRepository)
    if unmapped:
        stmt = stmt.where(GitLabRepository.project_id.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    return [RepoOut(id=r.id, full_path=r.full_path, default_branch=r.default_branch,
                     project_id=r.project_id, connection_id=r.connection_id,
                     webhook_registered=r.webhook_id is not None) for r in rows]


@router.get("/repositories/{repo_id}/branches", dependencies=[Depends(RequirePermission("projects:read"))])
async def list_repository_branches(
    repo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Every branch on a discovered repository — for the Code Quality
    branch picker. No persisted branch cache exists (``GitLabRepository``
    only stores ``default_branch``), so this always calls GitLab live."""
    repo = await session.get(GitLabRepository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _REPOSITORY_NOT_FOUND)

    provider = GitLabProvider(get_session_factory())
    repo_ref = RepoRef(external_id=repo.full_path, name=repo.full_path.rsplit("/", 1)[-1],
                        default_branch=repo.default_branch, private=True)
    try:
        branches = await provider.list_branches(str(repo.id), repo_ref)
    except GitLabProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"branches": branches, "default_branch": repo.default_branch}


class RepoReconnect(_StrippedTokenMixin):
    token: str = Field(..., min_length=1, max_length=256)


@router.post("/repositories/{repo_id}/reconnect",
             dependencies=[Depends(RequirePermission("projects:write"))])
async def reconnect_repository(
    repo_id: int,
    body: RepoReconnect,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RepoOut:
    """Refresh an existing repository's stored token (and, since GitLab's
    numeric project id isn't stable across a from-scratch reinstall,
    ``gitlab_project_id`` too) without dropping the row — preserves its
    ``project_id`` mapping and any linked ``SonarProject`` (which cascades
    on delete). Validates the new token against the same ``full_path``
    before storing it, so a token for a different project is rejected
    rather than silently pointing this row somewhere else."""
    repo = await session.get(GitLabRepository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _REPOSITORY_NOT_FOUND)
    try:
        detail = await _fetch_project(repo.gitlab_url, body.token, repo.full_path)
    except GitLabProviderError as exc:
        # Never log the token itself — a length + prefix is enough to tell
        # "empty/truncated paste" apart from "correctly-formed but wrong/
        # revoked token" without the log line itself becoming a credential.
        logger.warning("GitLab reconnect for repo %s failed: %s (submitted token: len=%d prefix=%r)",
                        repo_id, exc, len(body.token), body.token[:6])
        message = ("GitLab rejected this token (401 Unauthorized) — it's either wrong, "
                   "expired, or revoked. Generate a fresh one and try again."
                   if str(exc).startswith("401") else _UNREACHABLE_MESSAGE)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message) from exc
    if detail["path_with_namespace"] != repo.full_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                             f"Token resolves to {detail['path_with_namespace']!r}, not {repo.full_path!r}")
    repo.gitlab_project_id = detail["id"]
    repo.encrypted_token = encrypt_password(body.token, settings)
    # A stale webhook_id refers to a webhook on whatever GitLab instance/
    # project existed before — start webhook registration over, not just
    # the credential, whenever the project id actually changed.
    repo.webhook_id = None
    repo.webhook_secret = None
    # Commit the new token before registering the webhook: _try_register_webhook
    # goes through GitLabProvider, which opens its own session and re-reads
    # this row from the database — against an uncommitted transaction it
    # would still see the old (now-invalid) token and 401 on the very
    # request meant to prove the new one works.
    await session.commit()
    await session.refresh(repo)
    await _try_register_webhook(settings, repo)
    await session.commit()
    await session.refresh(repo)
    return RepoOut(id=repo.id, full_path=repo.full_path, default_branch=repo.default_branch,
                    project_id=repo.project_id, connection_id=repo.connection_id,
                    webhook_registered=repo.webhook_id is not None)


async def _try_register_webhook(settings: Settings, repo: GitLabRepository) -> bool:
    """Register ``repo``'s per-repository webhook if it doesn't have one
    yet. Returns ``True`` if a webhook now exists (already did, or just
    registered), ``False`` if registration was attempted and failed —
    logged, not raised, so every caller (initial connect, bulk-map, and the
    explicit retry endpoint below) treats "no webhook yet" as recoverable,
    not fatal. The most common real-world failure: GitLab's own SSRF
    protection rejects a callback URL pointing at ``localhost`` outright
    ("Invalid url given") — see ``Settings.webhook_base_url``.
    """
    if repo.webhook_id is not None:
        return True
    provider = GitLabProvider(get_session_factory())
    repo_ref = RepoRef(external_id=repo.full_path, name=repo.full_path.rsplit("/", 1)[-1],
                        default_branch=repo.default_branch, private=True)
    secret = secrets.token_hex(24)
    callback_url = f"{settings.webhook_base_url}/api/modules/gitlab/webhooks/{repo.id}"
    try:
        handle = await provider.register_webhook(str(repo.id), repo_ref, callback_url, secret)
        repo.webhook_id = int(handle.external_id)
        repo.webhook_secret = secret
        return True
    except GitLabProviderError:
        logger.warning("Failed to register a GitLab webhook for %s; automatic push analysis "
                        "will not trigger until this succeeds.", repo.full_path, exc_info=True)
        return False


@router.post("/repositories/{repo_id}/register-webhook",
             dependencies=[Depends(RequirePermission("projects:write"))])
async def register_webhook(
    repo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Retry webhook registration for a repository whose automatic
    registration failed at connect time (a misconfigured
    ``WEBHOOK_BASE_URL``, GitLab briefly unreachable, ...) — recoverable
    without disconnecting and reconnecting the repository. A no-op (200,
    ``already_registered: true``) if it already has one."""
    repo = await session.get(GitLabRepository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _REPOSITORY_NOT_FOUND)
    already_registered = repo.webhook_id is not None
    ok = await _try_register_webhook(settings, repo)
    await session.commit()
    if not ok:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Failed to register a webhook for {repo.full_path} — verify GitLab can reach "
            f"{settings.webhook_base_url} and, if that address is on a local/private network, "
            f"that GitLab's outbound-request settings allow local-network webhook URLs.",
        )
    return {"webhook_registered": True, "already_registered": already_registered}


@router.post("/repositories/{repo_id}/map",
             dependencies=[Depends(RequirePermission("projects:write"))])
async def map_repository(
    repo_id: int,
    body: MapRepoBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> RepoOut:
    repo = await session.get(GitLabRepository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _REPOSITORY_NOT_FOUND)
    await projects_core.get_project(session, body.project_id)  # 404s if missing

    repo.project_id = body.project_id

    existing_integration = await session.scalar(
        select(Integration).where(Integration.project_id == body.project_id, Integration.module_key == "gitlab")
    )
    if existing_integration is None:
        await projects_core.connect_integration(
            session, body.project_id, "gitlab", "source", config={}, credential_ref=str(repo.connection_id or ""),
        )

    repo_ref = RepoRef(external_id=repo.full_path, name=repo.full_path.rsplit("/", 1)[-1],
                        default_branch=repo.default_branch, private=True)
    provider = GitLabProvider(get_session_factory())

    # Idempotent — a repo being re-mapped (e.g. moved to a different
    # Project) that already has a webhook must not get a second one.
    await _try_register_webhook(settings, repo)

    await session.commit()
    await session.refresh(repo)

    if state.cache is not None:
        await auto_provision_and_analyze(
            session, state.cache, settings, body.project_id, provider, str(repo.id), repo_ref, "gitlab",
            gitlab_repository_id=repo.id,
        )

    return RepoOut(id=repo.id, full_path=repo.full_path, default_branch=repo.default_branch,
                    project_id=repo.project_id, connection_id=repo.connection_id,
                    webhook_registered=repo.webhook_id is not None)


class BulkMapBody(BaseModel):
    project_id: int
    repo_ids: list[int] = Field(..., min_length=1, max_length=2000)


@router.post("/repositories/bulk-map", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def bulk_map_repositories(
    body: BulkMapBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict:
    """Attach many already-discovered repositories (from a connection's
    ``sync``) to a Project in one call. Each mapping (and webhook
    registration) happens immediately; Sonar provisioning + first analysis
    per repository is queued as a background ``provision_repository`` job
    so this request doesn't block on hundreds of sequential network calls.
    """
    await projects_core.get_project(session, body.project_id)  # 404s if missing
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")

    queue = JobQueue(state.cache)
    mapped: list[str] = []
    errors: list[str] = []
    webhook_failures: list[str] = []
    integration_connected = await session.scalar(
        select(Integration).where(Integration.project_id == body.project_id, Integration.module_key == "gitlab")
    ) is not None

    for repo_id in body.repo_ids:
        repo = await session.get(GitLabRepository, repo_id)
        if repo is None:
            errors.append(f"repository {repo_id}: not found")
            continue
        if repo.project_id is not None and repo.project_id != body.project_id:
            errors.append(f"{repo.full_path}: already connected to a different Project")
            continue

        repo.project_id = body.project_id
        mapped.append(repo.full_path)

        if not integration_connected:
            await projects_core.connect_integration(
                session, body.project_id, "gitlab", "source", config={}, credential_ref=str(repo.connection_id or ""),
            )
            integration_connected = True

        if not await _try_register_webhook(settings, repo):
            webhook_failures.append(repo.full_path)

        await queue.enqueue("provision_repository", {
            "project_id": body.project_id,
            "source_module": "gitlab",
            "credential_ref": str(repo.id),
            "repo_external_id": repo.full_path,
            "repo_name": repo.full_path.rsplit("/", 1)[-1],
            "default_branch": repo.default_branch,
            "github_repository_id": None,
            "gitlab_repository_id": repo.id,
        })

    await session.commit()
    return {
        "mapped": len(mapped), "queued": len(mapped), "errors": errors,
        # Mapping and provisioning still succeeded for these — only the
        # webhook did not, silently, per _try_register_webhook's own
        # contract. Surfaced here so "connected" doesn't read as "will
        # analyze on push" when it won't until this is retried (Settings →
        # or the repository's own "register webhook" action).
        "webhook_failures": webhook_failures,
    }


async def _already_seen(cache, key: str, ttl: int) -> bool:
    """True if ``key`` was already recorded (a duplicate analysis trigger);
    otherwise records it and returns False. GitLab, like GitHub, does not
    guarantee exactly-once delivery."""
    if await cache.get_json(key):
        return True
    await cache.set_json(key, True, ttl=ttl)
    return False


async def _resolve_analyzable_repo(session: AsyncSession, repo: GitLabRepository, event) -> str | None:
    """None when the pushed branch should trigger analysis; otherwise a
    rejection status (``"unmapped"``, ``"no_sonar_project"``,
    ``"auto_analyze_disabled"``, ``"branch_not_watched"``) explaining why not
    — none of these are errors, just "nothing to analyze against yet"."""
    if repo.project_id is None:
        return "unmapped"
    sonar_project = await session.scalar(
        select(SonarProject).where(SonarProject.gitlab_repository_id == repo.id)
    )
    if sonar_project is None:
        return "no_sonar_project"
    if not sonar_project.auto_analyze_enabled:
        return "auto_analyze_disabled"
    watched = sonar_project.auto_analyze_branches or [repo.default_branch]
    if event.ref not in watched:
        return "branch_not_watched"
    return None


# ---------------------------------------------------------------------------
# Webhook ingress — one per repository, URL carries the repository id since
# there is no shared instance-level secret to disambiguate by (unlike GitHub).
# ---------------------------------------------------------------------------
@router.post("/webhooks/{repo_id}", status_code=status.HTTP_202_ACCEPTED, include_in_schema=True)
async def gitlab_webhook(
    repo_id: int,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict[str, Any]:
    repo = await session.get(GitLabRepository, repo_id)
    if repo is None or repo.webhook_secret is None:
        # Same "always ack, never leak which ids exist" posture as a bad
        # signature — a 404 here would let a scanner enumerate repository ids.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook")

    token = request.headers.get("X-Gitlab-Token", "")
    if not verify_token(repo.webhook_secret, token):
        logger.warning("Rejected a GitLab webhook delivery with a bad or missing token (repo %s)", repo_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook")

    payload = await request.json()
    event = normalize_push_event(payload)
    if event is None:
        return {"status": "ignored"}

    status_reason = await _resolve_analyzable_repo(session, repo, event)
    if status_reason is not None:
        return {"status": status_reason}

    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue cache not initialised")

    if await _already_seen(state.cache, f"gitlab:analyzed:{repo.id}:{event.sha}", 3600):
        return {"status": "duplicate"}

    queue = JobQueue(state.cache)
    await queue.enqueue("clone_and_analyze", {
        "project_id": repo.project_id,
        "source_module": "gitlab",
        "credential_ref": str(repo.id),
        "repo_external_id": repo.full_path,
        "repo_name": repo.full_path.rsplit("/", 1)[-1],
        "default_branch": repo.default_branch,
        "ref": event.ref,
        "sha": event.sha,
        "trigger": "push",
        "github_repository_id": None,
        "gitlab_repository_id": repo.id,
    })
    return {"status": "queued"}
