"""GitHub App connect flow, repository discovery/mapping, webhook ingress.

The App itself is provisioned via GitHub's **App Manifest flow**
(``manifest_form``/``manifest_callback`` below): clicking "Connect to GitHub"
in Settings submits a manifest to GitHub, which creates a real GitHub App
under the operator's account/org and redirects back with credentials Rotsy
saves automatically — no App is created by hand, no env vars are edited, no
values are copy-pasted between GitHub and Rotsy. ``GITHUB_APP_ID`` /
``GITHUB_APP_PRIVATE_KEY`` / ``GITHUB_WEBHOOK_SECRET`` env vars still work as
a bootstrap default (see :mod:`app.core.config_store`), for an App created
outside this flow, but they are not part of the normal path.

Thin HTTP layer otherwise: auth/token logic lives in
``modules/github/auth.py``, discovery in ``modules/github/provider.py``,
signature/event parsing in ``modules/github/webhooks.py``. This router
translates HTTP <-> those calls and persists
``GitHubInstallation``/``GitHubRepository`` rows.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import projects as projects_core
from ..core.cache import Cache
from ..core.config_store import GitHubAppConfig, get_github_app_config, save_github_app_config
from ..core.jobs import JobQueue
from ..core.outbound import OutboundURLError, validate_outbound_url
from ..core.source_provider import RepoRef
from ..dependencies import RequirePermission, get_session, get_settings
from ..models import GitHubInstallation, GitHubRepository, Integration, SonarProject
from ..modules.github.auth import GitHubAuthError, get_installation_token, install_url
from ..modules.github.provider import GitHubProvider, GitHubProviderError
from ..modules.github.webhooks import normalize_event, verify_signature
from ..modules.sonar.provisioning import auto_provision_and_analyze
from ..state import app_state, AppState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modules/github", tags=["github"])

_MANIFEST_STATE_KEY = "github:manifest_state:{state}"
_MANIFEST_STATE_TTL_SECONDS = 600


class RepoOut(BaseModel):
    id: int
    full_name: str
    default_branch: str
    project_id: int | None


class MapRepoBody(BaseModel):
    project_id: int


class PublicRepoConnect(BaseModel):
    full_name: str  # "owner/repo"
    project_id: int


class BulkMapBody(BaseModel):
    project_id: int
    repo_ids: list[int] = Field(..., min_length=1, max_length=2000)


class BulkPublicConnect(BaseModel):
    project_id: int
    full_names: list[str] = Field(..., min_length=1, max_length=2000)


class InstallationOut(BaseModel):
    id: int
    installation_id: int
    account_login: str


@router.get("/installations",
            dependencies=[Depends(RequirePermission("projects:read"))])
async def list_installations(session: Annotated[AsyncSession, Depends(get_session)]) -> list[InstallationOut]:
    rows = (await session.execute(select(GitHubInstallation))).scalars().all()
    return [InstallationOut(id=r.id, installation_id=r.installation_id, account_login=r.account_login) for r in rows]


@router.get("/repositories",
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


@router.get("/repositories/{repo_id}/branches", dependencies=[Depends(RequirePermission("projects:read"))])
async def list_repository_branches(
    repo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict:
    """Every branch on a discovered repository — for the Code Quality
    branch picker. No persisted branch cache exists (``GitHubRepository``
    only stores ``default_branch``), so this always calls GitHub live."""
    repo = await session.get(GitHubRepository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")

    installation = await session.get(GitHubInstallation, repo.installation_id) if repo.installation_id else None
    credential_ref = str(installation.installation_id) if installation else ""
    app_config = await get_github_app_config(session, settings)
    provider = GitHubProvider(app_config, state.cache)
    repo_ref = RepoRef(external_id=repo.full_name, name=repo.full_name.rsplit("/", 1)[-1],
                        default_branch=repo.default_branch, private=installation is not None)
    try:
        branches = await provider.list_branches(credential_ref, repo_ref)
    except GitHubProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"branches": branches, "default_branch": repo.default_branch}


@router.get("/status", dependencies=[Depends(RequirePermission("projects:read"))])
async def github_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Connection status for the Settings -> Integrations -> GitHub card.

    "Configured" means an App's credentials are on file (however they got
    there — the manifest flow or an env bootstrap default); "connected"
    means at least one installation has completed the GitHub-side install
    flow — App configuration alone doesn't prove GitHub can reach Rotsy.
    """
    cfg = await get_github_app_config(session, settings)
    installations_count = 0
    if cfg.is_configured():
        installations_count = await session.scalar(
            select(func.count()).select_from(GitHubInstallation)
        ) or 0
    return {
        "configured": cfg.is_configured(),
        "connected": installations_count > 0,
        "installations_count": installations_count,
        "app_slug": cfg.app_slug or None,
        "has_webhook": cfg.has_webhook(),
    }


# ---------------------------------------------------------------------------
# App Manifest flow — "Connect to GitHub" creates the App itself, no manual
# App creation or credential copy-pasting.
# ---------------------------------------------------------------------------
@router.get("/manifest-form", dependencies=[Depends(RequirePermission("projects:write"))])
async def manifest_form(
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict[str, Any]:
    """The manifest + CSRF state the frontend submits (as an HTML form POST,
    per GitHub's manifest flow) to ``https://github.com/settings/apps/new``.

    GitHub then prompts the operator to name/confirm creating the App under
    an account or org they control, and redirects to ``manifest_callback``
    with a one-time ``code`` this backend exchanges for real credentials.
    """
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")

    base = settings.FRONTEND_ORIGIN.rstrip("/")
    # Distinct from `base`: webhook_base is what GitHub's servers need to
    # reach, which is not necessarily the same address a human's browser
    # uses (see Settings.webhook_base_url). Only the webhook delivery URL
    # below uses it — redirect_url/setup_url/url further down are browser
    # navigations (the operator's own browser hitting its own localhost
    # works fine) and were never actually the problem.
    webhook_base = settings.webhook_base_url

    # So on an unreachable origin (typical local dev, or WEBHOOK_BASE_URL
    # left unset with a non-public FRONTEND_ORIGIN), the App is still
    # created — just without a webhook — rather than blocking the whole
    # flow. The trade-off: automatic push-triggered analysis needs a real
    # webhook, so it won't work until GitHub's servers can actually reach
    # this backend; everything else (install, discover repos, clone, Sonar
    # provisioning, manual "Run Analysis") works immediately.
    webhook_reachable = True
    try:
        validate_outbound_url(f"{webhook_base}/api/modules/github/webhooks", settings)
    except OutboundURLError:
        webhook_reachable = False

    csrf_state = secrets.token_hex(16)
    await state.cache.set_json(_MANIFEST_STATE_KEY.format(state=csrf_state), True, ttl=_MANIFEST_STATE_TTL_SECONDS)

    host = urlparse(base).hostname or "rotsy"
    manifest: dict[str, Any] = {
        "name": f"Rotsy ({host})",
        "url": base,
        # Two distinct redirects GitHub uses at two distinct steps: this one
        # fires right after the App itself is created (carries the manifest
        # conversion `code`); `setup_url` below fires after the *installation*
        # step (carries `installation_id`) — without setup_url, GitHub never
        # tells Rotsy an installation happened and GitHubInstallation rows
        # would never get created.
        "redirect_url": f"{base}/api/modules/github/manifest-callback",
        "setup_url": f"{base}/api/modules/github/callback",
        "setup_on_update": True,
        "public": False,
        "default_permissions": {"contents": "read", "statuses": "write", "metadata": "read"},
    }
    if webhook_reachable:
        manifest["hook_attributes"] = {"url": f"{webhook_base}/api/modules/github/webhooks"}
        # "pull_requests: read" is required to subscribe to the pull_request
        # event at all — GitHub rejects the event without it.
        manifest["default_permissions"]["pull_requests"] = "read"
        manifest["default_events"] = ["push", "pull_request"]

    return {
        "target_url": "https://github.com/settings/apps/new",
        "manifest": manifest,
        "state": csrf_state,
        "webhook_included": webhook_reachable,
        "warning": None if webhook_reachable else (
            f"FRONTEND_ORIGIN ({base}) isn't publicly reachable, so this App is being created without a "
            "webhook. Repository connection, cloning, and Sonar analysis all still work — use Run Analysis "
            "manually. Automatic analysis on push needs a public FRONTEND_ORIGIN (see Settings for a tunnel "
            "setup note) — reconnect afterward to add the webhook."
        ),
    }


@router.get("/manifest-callback", include_in_schema=True)
async def manifest_callback(
    code: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
    request: Request,
) -> RedirectResponse:
    """GitHub redirects here (a real browser navigation, not an API call)
    after the operator confirms App creation. Exchanges ``code`` for the
    App's id/private key/webhook secret and saves them — the one manual step
    left is the operator clicking through GitHub's own confirmation page,
    which GitHub requires and Rotsy cannot skip.
    """
    base = settings.FRONTEND_ORIGIN.rstrip("/")
    csrf_state = request.query_params.get("state")
    if csrf_state:
        key = _MANIFEST_STATE_KEY.format(state=csrf_state)
        valid = state.cache is not None and await state.cache.get_json(key)
        if not valid:
            return RedirectResponse(f"{base}/settings/integrations?github_error=invalid_state")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"https://api.github.com/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json"},
        )
    if resp.status_code >= 400:
        logger.warning("GitHub manifest conversion failed: %s %s", resp.status_code, resp.text[:300])
        return RedirectResponse(f"{base}/settings/integrations?github_error=conversion_failed")

    data = resp.json()
    await save_github_app_config(
        session, settings,
        app_id=str(data["id"]), app_slug=data["slug"],
        private_key=data["pem"], webhook_secret=data.get("webhook_secret", ""),
    )
    return RedirectResponse(f"{base}/settings/integrations?github_connected=1")


@router.get("/install-url", dependencies=[Depends(RequirePermission("projects:read"))])
async def get_install_url(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    cfg = await get_github_app_config(session, settings)
    try:
        return {"url": install_url(cfg.app_slug)}
    except GitHubAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/callback", dependencies=[Depends(RequirePermission("projects:write"))])
async def install_callback(
    installation_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[AppState, Depends(app_state)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    """GitHub redirects the browser here (``setup_url`` in the manifest, a
    real page navigation — not an API call) after the operator installs the
    App onto an account/org, a separate step from creating the App itself.

    GitHub's own redirect only ever carries ``installation_id`` (plus
    ``setup_action``) — there is no way to thread a specific Project through
    a plain App-install link. So installation is project-independent: one
    installation can back repositories that end up mapped to many different
    Projects. The per-project ``github`` Integration row is created lazily,
    the first time a repository from this installation is actually mapped
    to a Project (see :func:`map_repository`), not here.
    """
    base = settings.FRONTEND_ORIGIN.rstrip("/")

    existing = await session.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    )
    if existing is not None:
        return RedirectResponse(f"{base}/settings/integrations?github_installed=1")

    if state.cache is None:
        return RedirectResponse(f"{base}/settings/integrations?github_error=cache_unavailable")

    cfg = await get_github_app_config(session, settings)
    try:
        account_login = await _resolve_account_login(cfg, state.cache, installation_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to resolve account login for installation %s", installation_id)
        account_login = ""

    row = GitHubInstallation(installation_id=installation_id, account_login=account_login)
    session.add(row)
    await session.commit()
    return RedirectResponse(f"{base}/settings/integrations?github_installed=1")


async def _resolve_account_login(app_config: GitHubAppConfig, cache: Cache, installation_id: int) -> str:
    token = await get_installation_token(app_config.app_id, app_config.private_key, cache, installation_id)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://api.github.com/installation/repositories?per_page=1",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    if resp.status_code >= 400:
        return ""
    repos = resp.json().get("repositories", [])
    return repos[0]["owner"]["login"] if repos else ""


@router.post("/installations/{installation_id}/sync",
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

    cfg = await get_github_app_config(session, settings)
    provider = GitHubProvider(cfg, state.cache)
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


@router.post("/repositories/{repo_id}/map",
             dependencies=[Depends(RequirePermission("projects:write"))])
async def map_repository(
    repo_id: int,
    body: MapRepoBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> RepoOut:
    repo = await session.get(GitHubRepository, repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    await projects_core.get_project(session, body.project_id)  # 404s if missing

    installation = await session.get(GitHubInstallation, repo.installation_id) if repo.installation_id else None
    credential_ref = str(installation.installation_id) if installation else ""
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
            credential_ref=credential_ref or None,
        )

    await session.commit()
    await session.refresh(repo)

    if state.cache is not None:
        cfg = await get_github_app_config(session, settings)
        provider = GitHubProvider(cfg, state.cache)
        repo_ref = RepoRef(external_id=repo.full_name, name=repo.full_name.rsplit("/", 1)[-1],
                            default_branch=repo.default_branch, private=installation is not None)
        await auto_provision_and_analyze(
            session, state.cache, settings, body.project_id, provider,
            credential_ref, repo_ref, "github", github_repository_id=repo.id,
        )

    return RepoOut(id=repo.id, full_name=repo.full_name, default_branch=repo.default_branch, project_id=repo.project_id)


@router.post("/repositories/bulk-map", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def bulk_map_repositories(
    body: BulkMapBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict:
    """Attach many already-discovered repositories (from
    ``installations/{id}/sync``) to a Project in one call — the "17, 1000
    repositories" case. Each mapping happens immediately (cheap, DB-only);
    the actual Sonar provisioning + first analysis per repository is
    queued as a background ``provision_repository`` job so this request
    doesn't block on hundreds of sequential network calls.
    """
    await projects_core.get_project(session, body.project_id)  # 404s if missing
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")

    queue = JobQueue(state.cache)
    mapped: list[str] = []
    errors: list[str] = []
    integration_connected = await session.scalar(
        select(Integration).where(Integration.project_id == body.project_id, Integration.module_key == "github")
    ) is not None

    for repo_id in body.repo_ids:
        repo = await session.get(GitHubRepository, repo_id)
        if repo is None:
            errors.append(f"repository {repo_id}: not found")
            continue
        if repo.project_id is not None and repo.project_id != body.project_id:
            errors.append(f"{repo.full_name}: already connected to a different Project")
            continue

        installation = await session.get(GitHubInstallation, repo.installation_id) if repo.installation_id else None
        credential_ref = str(installation.installation_id) if installation else ""
        repo.project_id = body.project_id
        mapped.append(repo.full_name)

        if not integration_connected:
            await projects_core.connect_integration(
                session, body.project_id, "github", "source", config={}, credential_ref=credential_ref or None,
            )
            integration_connected = True

        await queue.enqueue("provision_repository", {
            "project_id": body.project_id,
            "source_module": "github",
            "credential_ref": credential_ref,
            "repo_external_id": repo.full_name,
            "repo_name": repo.full_name.rsplit("/", 1)[-1],
            "default_branch": repo.default_branch,
            "github_repository_id": repo.id,
            "gitlab_repository_id": None,
        })

    await session.commit()
    return {"mapped": len(mapped), "queued": len(mapped), "errors": errors}


@router.post("/public-repositories/bulk", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def bulk_connect_public_repositories(
    body: BulkPublicConnect,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> dict:
    """Connect many public repositories by ``owner/repo`` at once.

    Note: GitHub's unauthenticated API rate limit (60 requests/hour) applies
    to every lookup here, since these repos have no App installation to
    authenticate with — a few dozen at a time is realistic, a thousand is
    not. For your own repositories, install the App and use
    ``repositories/bulk-map`` instead, which uses the App's much higher
    installation rate limit.
    """
    await projects_core.get_project(session, body.project_id)  # 404s if missing
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")

    cfg = await get_github_app_config(session, settings)
    provider = GitHubProvider(cfg, state.cache)
    queue = JobQueue(state.cache)
    connected: list[str] = []
    errors: list[str] = []
    integration_connected = await session.scalar(
        select(Integration).where(Integration.project_id == body.project_id, Integration.module_key == "github")
    ) is not None

    for raw_name in body.full_names:
        full_name = raw_name.strip()
        if not full_name:
            continue
        existing = await session.scalar(select(GitHubRepository).where(GitHubRepository.full_name == full_name))
        if existing is not None:
            errors.append(f"{full_name}: already connected")
            continue
        try:
            repo_ref = await provider.get_public_repository(full_name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{full_name}: {exc}")
            continue

        repo = GitHubRepository(installation_id=None, project_id=body.project_id,
                                 full_name=repo_ref.external_id, default_branch=repo_ref.default_branch)
        session.add(repo)
        await session.flush()  # need repo.id for the job payload, without a full commit yet
        connected.append(repo.full_name)

        if not integration_connected:
            await projects_core.connect_integration(
                session, body.project_id, "github", "source", config={"connection": "public_url"}, credential_ref=None,
            )
            integration_connected = True

        await queue.enqueue("provision_repository", {
            "project_id": body.project_id,
            "source_module": "github",
            "credential_ref": "",
            "repo_external_id": repo.full_name,
            "repo_name": repo.full_name.rsplit("/", 1)[-1],
            "default_branch": repo.default_branch,
            "github_repository_id": repo.id,
            "gitlab_repository_id": None,
        })

    await session.commit()
    return {"connected": len(connected), "queued": len(connected), "errors": errors}


@router.post("/public-repositories", status_code=201,
             dependencies=[Depends(RequirePermission("projects:write"))])
async def connect_public_repository(
    body: PublicRepoConnect,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    state: Annotated[AppState, Depends(app_state)],
) -> RepoOut:
    """Connect any public repository by ``owner/name`` — no GitHub App
    installation needed, works for a repo you don't own or administer.

    The trade-off: GitHub only sends push events to repositories the App is
    actually installed on, so there is no webhook for a repo connected this
    way — automatic push-triggered analysis isn't possible, only manual "Run
    Analysis" (see ``routers/sonar.py:run_analysis``). Everything else
    (cloning, Sonar project provisioning, the first analysis run) works the
    same as an App-connected repository.
    """
    await projects_core.get_project(session, body.project_id)  # 404s if missing

    existing = await session.scalar(select(GitHubRepository).where(GitHubRepository.full_name == body.full_name))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.full_name} is already connected")

    cfg = await get_github_app_config(session, settings)
    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache not initialised")
    provider = GitHubProvider(cfg, state.cache)
    try:
        repo_ref = await provider.get_public_repository(body.full_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    repo = GitHubRepository(
        installation_id=None, project_id=body.project_id,
        full_name=repo_ref.external_id, default_branch=repo_ref.default_branch,
    )
    session.add(repo)

    existing_integration = await session.scalar(
        select(Integration).where(Integration.project_id == body.project_id, Integration.module_key == "github")
    )
    if existing_integration is None:
        await projects_core.connect_integration(
            session, body.project_id, "github", "source", config={"connection": "public_url"}, credential_ref=None,
        )

    await session.commit()
    await session.refresh(repo)

    await auto_provision_and_analyze(
        session, state.cache, settings, body.project_id, provider, "", repo_ref, "github",
        github_repository_id=repo.id,
    )
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

    Authenticated by ``X-Hub-Signature-256`` against the App's webhook secret
    (dashboard-managed, see :mod:`app.core.config_store`) — not a user
    session, GitHub calls this machine-to-machine. Always 202s once the
    signature checks out, including for event types we don't act on (same
    policy as the Nexus webhook receiver: a receiver that errors on
    uninteresting events gets disabled by the sender).
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    cfg = await get_github_app_config(session, settings)
    if not verify_signature(cfg.webhook_secret, body, signature):
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

    sonar_project = await session.scalar(
        select(SonarProject).where(SonarProject.github_repository_id == repo.id)
    )
    if sonar_project is None:
        # No Sonar project connected yet — same "nothing to analyze against"
        # reasoning as the unmapped case above, not an error.
        return {"status": "no_sonar_project"}
    if not sonar_project.auto_analyze_enabled:
        return {"status": "auto_analyze_disabled"}
    # Empty list = "default branch only" (see models/sonar.py) — otherwise
    # the push must match one of the explicitly watched branches.
    watched = sonar_project.auto_analyze_branches or [repo.default_branch]
    if event.ref not in watched:
        return {"status": "branch_not_watched"}

    if state.cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue cache not initialised")

    # Idempotency: JobQueue.enqueue() has no dedupe of its own, and GitHub's
    # at-least-once delivery (plus a possible retry after our 202 but before
    # the sender sees it) can otherwise double-analyze the same commit.
    analysis_key = f"github:analyzed:{repo.id}:{event.sha}"
    if await state.cache.get_json(analysis_key):
        return {"status": "duplicate"}
    await state.cache.set_json(analysis_key, True, ttl=3600)

    installation = await session.get(GitHubInstallation, repo.installation_id)
    if installation is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "GitHub installation for this repository is missing")

    queue = JobQueue(state.cache)
    await queue.enqueue(
        "clone_and_analyze",
        {
            "project_id": repo.project_id,
            "source_module": "github",
            "credential_ref": str(installation.installation_id),
            "repo_external_id": repo.full_name,
            "repo_name": repo.full_name.rsplit("/", 1)[-1],
            "default_branch": repo.default_branch,
            "ref": event.ref,
            "sha": event.sha,
            "trigger": event.type,  # "push" or "pull_request"
            "github_repository_id": repo.id,
            "gitlab_repository_id": None,
        },
    )
    return {"status": "queued"}
