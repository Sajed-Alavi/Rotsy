"""Prometheus-format metrics endpoint for external monitoring (Zabbix, etc.).

Exposes all internal metrics in the standard Prometheus text format so
external scrapers can poll ``/metrics/export`` and trigger alerts based on
storage thresholds, vulnerability counts, health status, etc.

No auth on this endpoint (Prometheus scrapers typically can't do cookie auth).
Protect at the network/reverse-proxy level in production.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_session
from ..services.metrics_collector import latest_snapshot

logger = logging.getLogger(__name__)
router = APIRouter(tags=["prometheus"])


def _storage_metric_lines(snapshot: list[dict]) -> list[str]:
    lines = [
        "# HELP nexus_repo_total_bytes Total bytes stored per repository",
        "# TYPE nexus_repo_total_bytes gauge",
        "# HELP nexus_repo_asset_count Asset count per repository",
        "# TYPE nexus_repo_asset_count gauge",
    ]
    for r in snapshot:
        repo = r.get("repo", "unknown")
        lines.append(f'nexus_repo_total_bytes{{repo="{repo}"}} {r.get("total_bytes", 0)}')
        lines.append(f'nexus_repo_asset_count{{repo="{repo}"}} {r.get("asset_count", 0)}')
    return lines


async def _nexus_reachable_lines(nexus) -> list[str]:
    try:
        s = await nexus.client.get("/service/rest/v1/status")
        reachable = 1 if s.status_code < 500 else 0
    except Exception:  # noqa: BLE001
        reachable = 0
    return [
        "# HELP nexus_reachable Nexus reachable (1=yes, 0=no)",
        "# TYPE nexus_reachable gauge",
        f"nexus_reachable {reachable}",
    ]


async def _nexus_health_probe_lines(nexus) -> list[str]:
    try:
        hc = await nexus.client.get("/service/rest/v1/status/check")
        if hc.status_code != 200:
            return []
        lines = [
            "# HELP nexus_health_probe Nexus health probe (1=healthy, 0=unhealthy)",
            "# TYPE nexus_health_probe gauge",
        ]
        for name, info in (hc.json() or {}).items():
            safe_name = name.replace(" ", "_").replace("-", "_").lower()
            val = 1 if info.get("healthy") else 0
            lines.append(f'nexus_health_probe{{probe="{safe_name}"}} {val}')
        return lines
    except Exception:  # noqa: BLE001
        return []


async def _nexus_blobstore_lines(nexus) -> list[str]:
    try:
        bs = await nexus.client.get("/service/rest/v1/blobstores")
        if bs.status_code != 200:
            return []
        lines = [
            "# HELP nexus_blobstore_used_bytes Used bytes per blobstore",
            "# TYPE nexus_blobstore_used_bytes gauge",
            "# HELP nexus_blobstore_free_bytes Free bytes per blobstore",
            "# TYPE nexus_blobstore_free_bytes gauge",
            "# HELP nexus_blobstore_blob_count Blob count per blobstore",
            "# TYPE nexus_blobstore_blob_count gauge",
        ]
        for b in bs.json() or []:
            name = b.get("name", "unknown")
            total = b.get("totalSizeInBytes", 0)
            free = b.get("availableSpaceInBytes", 0)
            lines.append(f'nexus_blobstore_used_bytes{{blobstore="{name}"}} {total}')
            lines.append(f'nexus_blobstore_free_bytes{{blobstore="{name}"}} {free}')
            lines.append(f'nexus_blobstore_blob_count{{blobstore="{name}"}} {b.get("blobCount", 0)}')
        return lines
    except Exception:  # noqa: BLE001
        return []


async def _redis_reachable_lines(cache) -> list[str]:
    redis_ok = 0
    if cache is not None and cache.redis is not None:
        try:
            await cache.redis.ping()
            redis_ok = 1
        except Exception:  # noqa: BLE001
            pass
    return [
        "# HELP redis_reachable Redis reachable (1=yes, 0=no)",
        "# TYPE redis_reachable gauge",
        f"redis_reachable {redis_ok}",
    ]


@router.get("/metrics/export")
async def prometheus_metrics(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> Response:
    """Return all metrics in Prometheus text exposition format."""
    nexus = getattr(request.app.state, "nexus", None)
    cache = getattr(request.app.state, "cache", None)
    lines: list[str] = []

    snapshot = await latest_snapshot(session)
    lines.extend(_storage_metric_lines(snapshot))

    if nexus is not None:
        lines.extend(await _nexus_reachable_lines(nexus))
        lines.extend(await _nexus_health_probe_lines(nexus))
        lines.extend(await _nexus_blobstore_lines(nexus))

    lines.extend(await _redis_reachable_lines(cache))

    return Response(content="\n".join(lines) + "\n", media_type="text/plain")
