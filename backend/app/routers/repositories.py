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
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..core.nexus_client import NexusClient
from ..dependencies import RequirePermission
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


@router.get("/{name}/assets", dependencies=[Depends(RequirePermission("repositories:read"))])
async def list_assets(
    request: Request,
    name: str,
    continuation_token: Annotated[str | None, Query(alias="continuationToken")] = None,
) -> dict[str, Any]:
    """Paginated asset list for a repository.

    Returns Nexus' shape (``items`` + ``continuationToken``) enriched with the
    fields the UI needs: path, downloadUrl, fileSize, contentType, uploader,
    timestamps, checksums. The frontend uses ``continuationToken`` to load more.
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
    return resp.json()


@router.get(
    "/{name}/assets/download",
    dependencies=[Depends(RequirePermission("repositories:read"))],
)
async def download_asset(
    request: Request,
    name: str,
    path: Annotated[str, Query(description="Asset path within the repository, e.g. '/foo/bar.jar'")] = "",
) -> StreamingResponse:
    """Stream an asset from Nexus to the browser (authenticated proxy).

    The browser never sees Nexus credentials — the backend fetches with its
    service account and forwards the bytes. ``Content-Disposition`` is set so
    the browser offers a real filename.
    """
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Query parameter 'path' is required.")
    nexus = await _nexus(request)
    # Nexus serves assets at /repository/{repo}{path}. The path is already
    # absolute (starts with /), so we just join.
    url = f"/repository/{name}{path}"
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
# Repository create / delete (Feature F) — real Nexus REST API
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field  # noqa: E402


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
        return {"ok": True, "name": body.name, "format": body.format, "type": body.type}
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
