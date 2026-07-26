"""Blobstore Management (Feature C).

Wraps the Nexus REST blobstore API so the dashboard can list, create (File or
S3) and delete blobstores. The backend authenticates to Nexus with its service
account, so the browser never handles Nexus credentials.

Nexus endpoints used:
  * ``GET    /service/rest/v1/blobstores``              — list
  * ``POST   /service/rest/v1/blobstores/file``         — create File blobstore
  * ``POST   /service/rest/v1/blobstores/s3``           — create S3 blobstore
  * ``DELETE /service/rest/v1/blobstores/{name}``       — delete
  * ``GET    /service/rest/v1/blobstores/{name}/quota-status`` — quota/space
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..core.nexus_client import NexusClient
from ..dependencies import RequirePermission
from ..state import app_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/blobstores", tags=["blobstores"])


async def _nexus(request: Request) -> NexusClient:
    client = app_state(request).nexus
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not initialised")
    return client


def _explain_nexus_error(exc: Exception) -> str:
    """Turn an httpx/connection error into a human-friendly message."""
    return f"Failed to contact Nexus: {exc}"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get("", dependencies=[Depends(RequirePermission("blobstores:read"))])
async def list_blobstores(request: Request) -> list[dict[str, Any]]:
    """List blobstores with type, state, blob count and total size.

    Enriches the base list with per-store quota/space where Nexus exposes it.
    """
    nexus = await _nexus(request)
    try:
        resp = await nexus.client.get("/service/rest/v1/blobstores")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list blobstores failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _explain_nexus_error(exc))
    stores = resp.json() or []

    # Enrich with quota status (free/used) when available. Best-effort — a
    # failure here must not break the list.
    for s in stores:
        name = s.get("name")
        if not name:
            continue
        try:
            q = await nexus.client.get(f"/service/rest/v1/blobstores/{name}/quota-status")
            if q.status_code == 200:
                s["quota"] = q.json()
        except Exception:  # noqa: BLE001
            pass
    return stores


# ---------------------------------------------------------------------------
# Create — File
# ---------------------------------------------------------------------------
class SoftQuota(BaseModel):
    type: str = Field(..., description="'spaceRemainingQuota' or 'spaceUsedQuota'")
    limit: int = Field(..., ge=0, description="Limit in BYTES")


class FileBlobstoreCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    path: str = Field(..., min_length=1, max_length=1024,
                      description="Absolute path or path relative to the Nexus data dir, e.g. 'my-store'")
    soft_quota: SoftQuota | None = None


@router.post("/file", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("blobstores:write"))])
async def create_file_blobstore(request: Request, body: FileBlobstoreCreate) -> dict[str, Any]:
    """Create a File-type blobstore in Nexus."""
    nexus = await _nexus(request)
    payload: dict[str, Any] = {"name": body.name, "path": body.path}
    if body.soft_quota is not None:
        payload["softQuota"] = {"type": body.soft_quota.type, "limit": body.soft_quota.limit}
    try:
        resp = await nexus.client.post("/service/rest/v1/blobstores/file", json=payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _explain_nexus_error(exc))
    return _handle_create_response(resp, body.name)


# ---------------------------------------------------------------------------
# Create — S3
# ---------------------------------------------------------------------------
class S3BlobstoreCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    bucket: str = Field(..., min_length=1, max_length=255)
    region: str = Field(default="us-east-1", max_length=64)
    prefix: str = Field(default="", max_length=255)
    expiration: int = Field(default=3, description="Days before incomplete uploads expire (-1 to disable)")
    access_key_id: str = Field(default="", max_length=255)
    secret_access_key: str = Field(default="", max_length=255)
    endpoint: str = Field(default="", max_length=512, description="Custom S3 endpoint (MinIO etc.), blank for AWS")
    soft_quota: SoftQuota | None = None


@router.post("/s3", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("blobstores:write"))])
async def create_s3_blobstore(request: Request, body: S3BlobstoreCreate) -> dict[str, Any]:
    """Create an S3-type blobstore in Nexus (AWS or S3-compatible endpoint)."""
    nexus = await _nexus(request)
    bucket_cfg: dict[str, Any] = {
        "region": body.region,
        "name": body.bucket,
        "prefix": body.prefix,
        "expiration": body.expiration,
    }
    bucket_security: dict[str, Any] = {}
    if body.access_key_id:
        bucket_security["accessKeyId"] = body.access_key_id
    if body.secret_access_key:
        bucket_security["secretAccessKey"] = body.secret_access_key
    advanced: dict[str, Any] = {}
    if body.endpoint:
        advanced["endpoint"] = body.endpoint
        advanced["forcePathStyle"] = True

    payload: dict[str, Any] = {
        "name": body.name,
        "bucketConfiguration": {
            "bucket": bucket_cfg,
            **({"bucketSecurity": bucket_security} if bucket_security else {}),
            **({"advancedBucketConnection": advanced} if advanced else {}),
        },
    }
    if body.soft_quota is not None:
        payload["softQuota"] = {"type": body.soft_quota.type, "limit": body.soft_quota.limit}
    try:
        resp = await nexus.client.post("/service/rest/v1/blobstores/s3", json=payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _explain_nexus_error(exc))
    return _handle_create_response(resp, body.name)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("blobstores:write"))])
async def delete_blobstore(request: Request, name: str):
    """Delete a blobstore. Nexus refuses if a repository still uses it (409)."""
    nexus = await _nexus(request)
    try:
        resp = await nexus.client.delete(f"/service/rest/v1/blobstores/{name}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _explain_nexus_error(exc))
    if resp.status_code in (200, 204):
        return
    if resp.status_code == 404:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Blobstore '{name}' not found")
    if resp.status_code in (400, 409):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot delete '{name}': it is still in use by a repository. "
            "Delete or repoint those repositories first.",
        )
    raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                        f"Nexus returned HTTP {resp.status_code}: {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Shared create-response handling
# ---------------------------------------------------------------------------
def _handle_create_response(resp, name: str) -> dict[str, Any]:
    if resp.status_code in (200, 201, 204):
        return {"ok": True, "name": name}
    if resp.status_code == 400:
        # Nexus returns a list of {id, message} validation errors.
        try:
            errs = resp.json()
            detail = "; ".join(e.get("message", str(e)) for e in errs) if isinstance(errs, list) else str(errs)
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Nexus rejected the blobstore: {detail}")
    if resp.status_code == 401:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Nexus rejected the service credentials (401).")
    raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                        f"Nexus returned HTTP {resp.status_code}: {resp.text[:200]}")
