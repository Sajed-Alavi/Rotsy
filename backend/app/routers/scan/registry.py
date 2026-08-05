"""Registry discovery diagnostics."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ...dependencies import RequirePermission
from ...modules.nexus import registry as registry_discovery
from ._common import require_backend

router = APIRouter()


@router.get("/registry", dependencies=[Depends(RequirePermission("scan:read"))])
async def registry_map(
    request: Request,
    refresh: Annotated[bool, Query(description="Re-interrogate Nexus instead of using the cache")] = False,
    check: Annotated[bool, Query(description="Also probe each endpoint's /v2/ API")] = False,
) -> dict[str, Any]:
    """Show the Docker registry endpoint discovered for each repository.

    This replaces the hand-entered registry URL. ``unresolved`` explains any
    Docker repository whose endpoint could not be derived — almost always a
    repository with no connector port configured in Nexus, or a service account
    without repository-admin read privileges.
    """
    nexus, cache = require_backend(request)
    result = await registry_discovery.discover(nexus, cache, refresh=refresh)
    payload = result.to_json()
    if check:
        for name, found in result.registries.items():
            payload["registries"][name]["probe"] = await registry_discovery.probe(nexus, found)
    return payload
