"""Repository management + asset browsing.

Implements:
  * ``GET /repositories``                      — list repos (cached briefly).
  * ``GET /repositories/{name}/assets``        — paginated asset list.
  * ``GET /repositories/{name}/assets/{path:path}/download`` — proxy download
    (the backend authenticates to Nexus with its service creds and streams the
    bytes to the browser, so the user never needs Nexus credentials).
  * ``POST /repositories`` / invalidate-cache / rebuild-index — stubs (Feature F).
"""

from __future__ import annotations

import logging
import posixpath
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.image_scope import allowed_image_patterns, image_visible
from ..core.nexus_client import NexusClient
from ..dependencies import RequirePermission, get_current_user, get_session
from ..models import User
from ..services import images, nexus_security
from ..services.storage_analyzer import parse_asset_path
from ..state import app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repositories", tags=["repositories"])

_REPOS_CACHE_KEY = "nexus:repositories"
_REPO_LIST_TTL = 30


async def _nexus(request: Request) -> NexusClient:
    client = app_state(request).nexus
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not initialised")
    return client


@router.get("", dependencies=[Depends(RequirePermission("repositories:read"))])
async def list_repositories(
    request: Request,
    format_filter: Annotated[str | None, Query(alias="format", description="Filter by repository format, e.g. 'docker'")] = None,
    refresh: Annotated[bool, Query(description="Bypass/refresh the cache")] = False,
) -> list[dict[str, Any]]:
    """List Nexus repositories, optionally filtered by format."""
    cache = app_state(request).cache
    if refresh:
        await cache.delete(_REPOS_CACHE_KEY)
        await cache.delete("nexus:all-repos")
    cached = await cache.get_json(_REPOS_CACHE_KEY)
    if cached is not None:
        repos = cached
    else:
        nexus = await _nexus(request)
        try:
            resp = await nexus.client.get("/service/rest/v1/repositories")
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list repositories: %s", exc)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to contact Nexus")
        repos = resp.json()
        await cache.set_json(_REPOS_CACHE_KEY, repos, ttl=_REPO_LIST_TTL)
    if format_filter:
        repos = [r for r in repos if r.get("format") == format_filter]
    return repos


@router.get("/{name}/images", dependencies=[Depends(RequirePermission("repositories:read"))])
async def list_repository_images(
    request: Request,
    name: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """List a repository's contents as images and tags rather than raw blobs.

    A Docker repository's asset listing is mostly layer blobs
    (``v2/myapp/blobs/sha256:…``), which is not a useful view of what the
    repository holds. This returns the image → tag structure with each tag's
    push time, size and component id (the handle needed to delete it).

    Images outside the caller's image-scope patterns for this repo (if any
    of their roles are scoped here) are omitted entirely.
    """
    nexus = await _nexus(request)
    try:
        result = await images.list_images(nexus, name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list images for %s: %s", name, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to list images: {exc}")

    patterns = await allowed_image_patterns(session, user, name)
    if patterns is None:
        return result
    return [img for img in result if image_visible(patterns, img["name"])]


class ComponentDelete(BaseModel):
    """Component ids to delete, as returned by ``GET /{name}/images``."""

    component_ids: list[str] = Field(..., min_length=1, max_length=500)
    compact: bool = Field(
        default=False,
        description="Also trigger the Nexus 'Compact blob store' task so the disk space is "
                    "reclaimed. Without it the tags disappear but the blobs stay on disk.",
    )


@router.post("/{name}/images/delete", dependencies=[Depends(RequirePermission("repositories:write"))])
async def delete_repository_images(
    request: Request,
    name: str,
    body: ComponentDelete,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Delete specific image tags, reporting the outcome of each one.

    Returns ``deleted`` and ``failed`` (each failure carrying its reason) rather
    than a single status code, so deleting one of four tags cannot look like a
    success when Nexus rejected it.

    Each component id's owning image is resolved server-side (never trust a
    client-supplied image name for a security check) and checked against the
    caller's image-scope patterns for this repo before anything is deleted.
    """
    nexus = await _nexus(request)

    patterns = await allowed_image_patterns(session, user, name)
    if patterns is not None:
        current_images = await images.list_images(nexus, name)
        owner_by_component_id = {
            tag["component_id"]: img["name"]
            for img in current_images
            for tag in img["tags"]
        }
        denied = [
            cid for cid in body.component_ids
            if cid not in owner_by_component_id or not image_visible(patterns, owner_by_component_id[cid])
        ]
        if denied:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Not permitted to delete {len(denied)} of the requested component(s) — outside your image scope for '{name}'.",
            )

    result = await images.delete_components(nexus, body.component_ids)
    if body.compact and result["deleted_count"]:
        result["compact"] = await images.trigger_compact(nexus)
    if result["deleted_count"]:
        # The repo list cache carries sizes/counts; drop it so the UI is honest.
        cache = app_state(request).cache
        if cache is not None:
            await cache.delete(_REPOS_CACHE_KEY, "nexus:all-repos")
    return result


@router.get("/{name}/assets", dependencies=[Depends(RequirePermission("repositories:read"))])
async def list_assets(
    request: Request,
    name: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    continuation_token: Annotated[str | None, Query(alias="continuationToken")] = None,
) -> dict[str, Any]:
    """Paginated asset list for a repository.

    Returns Nexus' shape (``items`` + ``continuationToken``) enriched with the
    fields the UI needs: path, downloadUrl, fileSize, contentType, uploader,
    timestamps, checksums. The frontend uses ``continuationToken`` to load more.

    Items outside the caller's image-scope patterns for this repo (if any of
    their roles are scoped here) are omitted, same as ``GET /{name}/images``.
    """
    nexus = await _nexus(request)
    params: dict[str, Any] = {"repository": name}
    if continuation_token:
        params["continuationToken"] = continuation_token
    try:
        resp = await nexus.client.get("/service/rest/v1/assets", params=params)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list assets for %s: %s", name, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to list assets")
    result = resp.json()

    patterns = await allowed_image_patterns(session, user, name)
    if patterns is not None:
        try:
            fmt_resp = await nexus.client.get(f"/service/rest/v1/repositories/{name}")
            fmt_resp.raise_for_status()
            fmt = (fmt_resp.json() or {}).get("format", "")
        except Exception:  # noqa: BLE001
            fmt = ""
        items = result.get("items") or []
        result["items"] = [
            item for item in items
            if image_visible(patterns, _image_name_from_asset_path(item.get("path", ""), fmt))
        ]
    return result


def _image_name_from_asset_path(path: str, fmt: str) -> str:
    """Best-effort image-name extraction from a raw asset path, for scoping.

    Docker paths look like ``v2/<image>/manifests/<tag>`` or
    ``v2/<image>/blobs/sha256:…``; everything else reuses the same heuristic
    :func:`storage_analyzer.parse_asset_path` already uses for the storage
    analyzer's generic-format grouping. This is inherently best-effort — raw
    asset paths don't cleanly expose "which image" the way the
    components/images API does (see the RBAC plan's documented boundary).
    """
    stripped = path.strip("/")
    if fmt == "docker" and stripped.startswith("v2/"):
        rest = stripped[len("v2/"):]
        for marker in ("/manifests/", "/blobs/"):
            if marker in rest:
                return rest.split(marker, 1)[0]
        return rest
    return parse_asset_path(path, fmt)[0]


def _validated_repository_path(name: str, path: str) -> str:
    """Resolve ``/repository/{name}{path}`` and reject any escape from that prefix.

    ``path`` is caller-supplied. httpx normalizes ``..`` segments when merging a
    relative URL against the client's ``base_url``, so an unvalidated path can
    reach arbitrary Nexus REST endpoints (e.g. ``/service/rest/v1/security/users``)
    under the backend's privileged service account rather than the intended
    repository. Normalizing here and checking containment closes that off before
    any request reaches Nexus.
    """
    prefix = f"/repository/{name}/"
    normalized = posixpath.normpath(f"/repository/{name}{path}")
    if not (normalized + "/").startswith(prefix):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid asset path.")
    return normalized


@router.get(
    "/{name}/assets/download",
    dependencies=[Depends(RequirePermission("repositories:read"))],
)
async def download_asset(
    request: Request,
    name: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    path: Annotated[str, Query(description="Asset path within the repository, e.g. '/foo/bar.jar'")] = "",
) -> StreamingResponse:
    """Stream an asset from Nexus to the browser (authenticated proxy).

    The browser never sees Nexus credentials — the backend fetches with its
    service account and forwards the bytes. ``Content-Disposition`` is set so
    the browser offers a real filename.
    """
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Query parameter 'path' is required.")
    # Validate before any Nexus call is made with this repo/path pair.
    validated_url = _validated_repository_path(name, path)
    nexus = await _nexus(request)

    patterns = await allowed_image_patterns(session, user, name)
    if patterns is not None:
        try:
            fmt_resp = await nexus.client.get(f"/service/rest/v1/repositories/{name}")
            fmt_resp.raise_for_status()
            fmt = (fmt_resp.json() or {}).get("format", "")
        except Exception:  # noqa: BLE001
            fmt = ""
        image_name = _image_name_from_asset_path(path, fmt)
        if not image_visible(patterns, image_name):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"'{image_name}' is outside your image scope for '{name}'.")
    # Nexus serves assets at /repository/{repo}{path}. Validated above so the
    # normalized path cannot escape this repository's prefix.
    url = validated_url
    # httpx async streaming: forward to the client chunk-by-chunk.
    upstream = await nexus.client.send(
        nexus.client.build_request("GET", url),
        stream=True,
    )
    if upstream.status_code >= 400:
        body = await upstream.aread()
        await upstream.aclose()
        raise HTTPException(upstream.status_code, f"Nexus returned error: {body[:200]}")

    # Derive a friendly filename from the last path segment.
    filename = path.rstrip("/").rsplit("/", 1)[-1] or "download"

    async def chunked():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    headers = {
        "Content-Disposition": f'attachment; filename="{quote(filename)}"',
    }
    # Forward useful headers from Nexus when present.
    for h in ("content-length", "content-type", "etag", "last-modified"):
        v = upstream.headers.get(h)
        if v:
            headers[h] = v

    return StreamingResponse(chunked(), media_type=upstream.headers.get("content-type", "application/octet-stream"), headers=headers)


# ---------------------------------------------------------------------------
# Repository create / delete — real Nexus REST API
# ---------------------------------------------------------------------------
class RepoCreate(BaseModel):
    """Create a repository.

    Nexus has a per-(format, type) endpoint, e.g.
    ``POST /service/rest/v1/repositories/docker/hosted``. We build the endpoint
    from ``format`` + ``type`` and assemble the minimal valid payload for each
    type. Extra format-specific knobs are accepted via ``extra`` and merged in.
    """
    name: str = Field(..., min_length=1, max_length=255)
    format: str = Field(..., description="docker | maven2 | npm | raw | pypi | nuget | ...")
    type: str = Field(..., description="hosted | proxy | group")
    blob_store: str = Field(default="default", max_length=255)
    online: bool = True
    anonymous_access: bool = Field(default=False, description="Grant repository-view (browse+read) to nx-anonymous role")

    # hosted
    write_policy: str = Field(default="ALLOW", description="ALLOW | ALLOW_ONCE | DENY (hosted)")
    # proxy
    remote_url: str = Field(default="", description="Upstream URL (proxy)")
    # group
    members: list[str] = Field(default_factory=list, description="Member repo names (group)")
    # docker-specific
    docker_http_port: int | None = Field(default=None, description="Docker connector HTTP port")
    docker_https_port: int | None = Field(default=None, description="Docker connector HTTPS port")
    docker_force_basic_auth: bool = True
    docker_v1_enabled: bool = False

    # Escape hatch for any additional Nexus fields (merged into the payload).
    extra: dict[str, Any] = Field(default_factory=dict)


def _build_repo_payload(body: RepoCreate) -> dict[str, Any]:
    """Assemble the Nexus create payload for the given format/type."""
    storage: dict[str, Any] = {"blobStoreName": body.blob_store, "strictContentTypeValidation": True}
    payload: dict[str, Any] = {"name": body.name, "online": body.online, "storage": storage}

    t = body.type.lower()
    if t == "hosted":
        storage["writePolicy"] = body.write_policy
    elif t == "proxy":
        if not body.remote_url:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "proxy repositories require remote_url")
        payload["proxy"] = {"remoteUrl": body.remote_url, "contentMaxAge": 1440, "metadataMaxAge": 1440}
        payload["negativeCache"] = {"enabled": True, "timeToLive": 1440}
        payload["httpClient"] = {"blocked": False, "autoBlock": True}
    elif t == "group":
        if not body.members:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "group repositories require at least one member")
        payload["group"] = {"memberNames": body.members}
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown repository type '{body.type}'")

    # Docker connector block (applies to hosted/proxy/group docker repos).
    if body.format.lower() == "docker":
        payload["docker"] = {
            "v1Enabled": body.docker_v1_enabled,
            "forceBasicAuth": body.docker_force_basic_auth,
            "httpPort": body.docker_http_port,
            "httpsPort": body.docker_https_port,
        }

    # Merge caller-supplied extras last so they can override.
    if body.extra:
        payload.update(body.extra)
    return payload


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("repositories:write"))])
async def create_repository(request: Request, body: RepoCreate) -> dict[str, Any]:
    """Create a hosted/proxy/group repository for the given format."""
    nexus = await _nexus(request)
    endpoint = f"/service/rest/v1/repositories/{body.format.lower()}/{body.type.lower()}"
    payload = _build_repo_payload(body)
    try:
        resp = await nexus.client.post(endpoint, json=payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("create repo failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to contact Nexus: {exc}")

    if resp.status_code in (200, 201, 204):
        # Bust the repo-list cache so the new repo shows up immediately.
        cache = app_state(request).cache
        if cache is not None:
            await cache.delete(_REPOS_CACHE_KEY)
        result: dict[str, Any] = {"ok": True, "name": body.name, "format": body.format, "type": body.type}
        if body.anonymous_access:
            try:
                await nexus_security.grant_anonymous_access(nexus, body.name, body.format)
            except Exception as exc:  # noqa: BLE001
                logger.warning("anonymous-access grant failed for %s: %s", body.name, exc)
                result["warning"] = f"repository created, but anonymous access grant failed: {exc}"
        return result
    if resp.status_code == 400:
        try:
            errs = resp.json()
            detail = "; ".join(e.get("message", str(e)) for e in errs) if isinstance(errs, list) else str(errs)
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Nexus rejected the repository: {detail}")
    if resp.status_code == 404:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Nexus has no endpoint for format '{body.format}' type '{body.type}'.")
    raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                        f"Nexus returned HTTP {resp.status_code}: {resp.text[:200]}")


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("repositories:write"))])
async def delete_repository(request: Request, name: str):
    """Delete a repository by name."""
    nexus = await _nexus(request)
    try:
        resp = await nexus.client.delete(f"/service/rest/v1/repositories/{name}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to contact Nexus: {exc}")
    if resp.status_code in (200, 204):
        cache = app_state(request).cache
        if cache is not None:
            await cache.delete(_REPOS_CACHE_KEY)
        return
    if resp.status_code == 404:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository '{name}' not found")
    raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                        f"Nexus returned HTTP {resp.status_code}: {resp.text[:200]}")


@router.post("/{name}/invalidate-cache", status_code=status.HTTP_501_NOT_IMPLEMENTED,
             dependencies=[Depends(RequirePermission("repositories:write"))])
async def invalidate_cache(name: str) -> dict[str, str]:
    """TODO Feature F: invalidate a proxy repository's cache."""
    return {"status": "not_implemented", "feature": "Feature F — Invalidate cache"}


@router.post("/{name}/rebuild-index", status_code=status.HTTP_501_NOT_IMPLEMENTED,
             dependencies=[Depends(RequirePermission("repositories:write"))])
async def rebuild_index(name: str) -> dict[str, str]:
    """TODO Feature F: rebuild a repository's search index."""
    return {"status": "not_implemented", "feature": "Feature F — Rebuild index"}
