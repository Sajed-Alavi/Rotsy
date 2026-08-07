"""Metrics endpoints (real-time + historical).

  * ``GET /metrics/overview`` — latest per-repo snapshot (cards/table).
  * ``GET /metrics/{repo}/timeseries?hours=24`` — samples for a chart.
  * ``GET /metrics/realtime`` — live-ish Nexus status (reachable, db writable).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.access_control import AccessResolver
from ..dependencies import RequirePermission, get_access, get_session
from ..state import app_state
from ..services.metrics_collector import blobstore_timeseries, latest_snapshot, timeseries

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/metrics", tags=["metrics"])

_NEXUS_CLIENT_NOT_AVAILABLE = "Nexus client not available"


@router.get("/overview", dependencies=[Depends(RequirePermission("metrics:read"))])
async def overview(
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
) -> list[dict[str, Any]]:
    """Latest per-repository snapshot, limited to repositories the caller may see."""
    rows = await latest_snapshot(session)
    return [row for row in rows if access.repo(row.get("repo") or "").visible]


@router.get("/{repo}/timeseries", dependencies=[Depends(RequirePermission("metrics:read"))])
async def repo_timeseries(
    repo: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
) -> list[dict[str, Any]]:
    if not access.repo(repo).visible:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"No access rule of yours covers '{repo}'."
        )
    return await timeseries(session, repo, hours)


@router.get("/blobstore/{name}/timeseries", dependencies=[Depends(RequirePermission("metrics:read"))])
async def blobstore_timeseries_endpoint(
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
) -> list[dict[str, Any]]:
    """Disk-usage history for one blobstore — feeds a sparkline/chart."""
    return await blobstore_timeseries(session, name, hours)


@router.get("/realtime", dependencies=[Depends(RequirePermission("metrics:read"))])
async def realtime(request: Request) -> dict[str, Any]:
    """Best-effort live snapshot: Nexus reachability + status flags."""
    nexus = app_state(request).nexus
    out: dict[str, Any] = {"reachable": False, "writable": False, "version": None}
    if nexus is None:
        return out
    try:
        s = await nexus.client.get("/service/rest/v1/status")
        out["reachable"] = s.status_code < 500
        if s.status_code == 200:
            out["version"] = _parse_status_version(s.text)
    except Exception:  # noqa: BLE001
        pass
    try:
        w = await nexus.client.get("/service/rest/v1/status/writable")
        out["writable"] = w.status_code == 200 and "true" in w.text.lower()
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("/health", dependencies=[Depends(RequirePermission("metrics:read"))])
async def health_checks(request: Request) -> dict[str, Any]:
    """Nexus health-check probes from ``/service/rest/v1/status/check``.

    Probes are categorized so the UI can distinguish critical system issues
    (blob stores, DB) from advisory security recommendations (default admin
    password, encryption key). Categories:
      - ``critical``: blob stores, DB, scheduler, JVM, CPU
      - ``security``: default credentials, encryption key, roles
      - ``info``: everything else (ECR tokens, NuGet V2, etc.)
    """
    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _NEXUS_CLIENT_NOT_AVAILABLE)
    try:
        resp = await nexus.client.get("/service/rest/v1/status/check")
        resp.raise_for_status()
        raw = resp.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("health check fetch failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to read Nexus health checks")

    # Keywords that map a probe to a category.
    CRITICAL_KEYWORDS = ("blob", "cpu", "scheduler", "deadlock", "node", "upgrade", "h2", "limit")
    SECURITY_KEYWORDS = ("admin", "credential", "secret", "encryption", "default", "role realm", "script")

    probes = []
    for name, info in raw.items():
        healthy = bool(info.get("healthy"))
        msg = (info.get("message") or "").replace("<br>", " ").strip()
        name_lower = name.lower()
        if any(k in name_lower for k in CRITICAL_KEYWORDS):
            category = "critical"
        elif any(k in name_lower for k in SECURITY_KEYWORDS):
            category = "security"
        else:
            category = "info"
        probes.append({
            "name": name,
            "healthy": healthy,
            "message": msg,
            "category": category,
        })
    # Sort: critical first, then security, then info; unhealthy first within each.
    cat_order = {"critical": 0, "security": 1, "info": 2}
    probes.sort(key=lambda p: (cat_order.get(p["category"], 9), not p["healthy"]))
    failing = sum(1 for p in probes if not p["healthy"])
    return {"total": len(probes), "failing": failing, "probes": probes}


@router.get("/blobstores", dependencies=[Depends(RequirePermission("metrics:read"))])
async def blobstore_stats(request: Request) -> list[dict[str, Any]]:
    """Disk usage for every blobstore (the real "is my disk full?" metric)."""
    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _NEXUS_CLIENT_NOT_AVAILABLE)
    try:
        resp = await nexus.client.get("/service/rest/v1/blobstores")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("blobstore stats fetch failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to read blobstore stats")
    out = []
    for b in resp.json() or []:
        total = int(b.get("totalSizeInBytes") or 0)
        free = int(b.get("availableSpaceInBytes") or 0)
        used = total  # Nexus reports totalSize as the used size of blobs stored
        capacity = total + free
        out.append({
            "name": b.get("name"),
            "type": b.get("type"),
            "blob_count": int(b.get("blobCount") or 0),
            "used_bytes": used,
            "free_bytes": free,
            "capacity_bytes": capacity,
            "unavailable": bool(b.get("unavailable")),
            "used_pct": round(used / capacity * 100, 1) if capacity else 0.0,
        })
    return out


@router.get("/host", dependencies=[Depends(RequirePermission("metrics:read"))])
async def host_metrics() -> dict[str, Any]:
    """Live host/process resource snapshot for the dashboard's CPU/memory/disk
    tiles — a point-in-time probe, not persisted, matching ``/metrics/realtime``'s
    "live probe" pattern rather than growing the ``Metric`` table.

    ``cpu_percent`` uses psutil's non-blocking mode (compares to the previous
    call in this process rather than sampling over a blocking interval) — the
    first call after a worker starts reads 0.0, which is expected and
    self-corrects on the next poll.
    """
    import psutil

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "memory_used_bytes": memory.used,
        "memory_total_bytes": memory.total,
        "disk_used_bytes": disk.used,
        "disk_total_bytes": disk.total,
    }


@router.get("/system", dependencies=[Depends(RequirePermission("metrics:read"))])
async def system_info(request: Request) -> dict[str, Any]:
    """High-level system info: version, write mode, security warnings."""
    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _NEXUS_CLIENT_NOT_AVAILABLE)
    out: dict[str, Any] = {"version": None, "writable": None, "edition": None,
                           "anonymous_enabled": None, "warnings": []}
    try:
        s = await nexus.client.get("/service/rest/v1/status")
        if s.status_code == 200:
            # Nexus returns "<version>/<edition>" plain text.
            text = s.text.strip()
            parts = text.split("/", 1)
            out["version"] = parts[0] if parts else text
            if len(parts) > 1:
                out["edition"] = parts[1]
    except Exception:  # noqa: BLE001
        pass
    try:
        a = await nexus.client.get("/service/rest/v1/security/anonymous")
        if a.status_code == 200:
            out["anonymous_enabled"] = bool((a.json() or {}).get("enabled"))
    except Exception:  # noqa: BLE001
        pass
    # Pull the security-related probes from health checks to surface warnings.
    try:
        hc = await nexus.client.get("/service/rest/v1/status/check")
        if hc.status_code == 200:
            for name, info in (hc.json() or {}).items():
                if not info.get("healthy") and any(k in name.lower() for k in
                                                    ("admin", "credential", "secret", "encryption", "default")):
                    out["warnings"].append({
                        "name": name,
                        "message": (info.get("message") or "").replace("<br>", " ").strip(),
                    })
    except Exception:  # noqa: BLE001
        pass
    return out


def _parse_status_version(text: str) -> str | None:
    # Nexus /status returns a plain text line like "{nexus.version}/{nexus.edition}"
    if not text:
        return None
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first or None
