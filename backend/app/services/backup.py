"""Nexus backup.

Nexus OSS does NOT expose ``/service/rest/v1/backup`` or ``/scheduler/tasks``
(those are Pro-only or version-dependent). Instead, this module provides a
**metadata export** — a downloadable JSON containing all repository configs +
asset manifests. This is actually more useful for migration than a raw DB
dump because it's version-independent and can be fed into the sync service.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..core.nexus_client import NexusClient

logger = logging.getLogger(__name__)


async def list_backup_tasks(nexus: NexusClient) -> list[dict[str, Any]]:
    """Return Nexus scheduler tasks (if available). Empty on OSS."""
    try:
        resp = await nexus.client.get("/service/rest/v1/scheduler/tasks")
        if resp.status_code != 200:
            return []
        tasks = resp.json() or []
        return [t for t in tasks if "backup" in (t.get("type") or "").lower()
                or "export" in (t.get("type") or "").lower()
                or t.get("typeId") == "blobstore.compact"]
    except Exception:  # noqa: BLE001
        return []


async def trigger_backup(nexus: NexusClient) -> dict[str, Any]:
    """Trigger a backup/export task if one exists. Returns status."""
    tasks = await list_backup_tasks(nexus)
    if not tasks:
        return {"ok": False,
                "error": "No backup task found in Nexus. On Nexus OSS, use 'Export metadata' (Settings → System → Backup) to download repository configs + asset manifests as JSON instead."}
    t = tasks[0]
    task_id = t.get("id")
    try:
        resp = await nexus.client.post(f"/service/rest/v1/scheduler/run/{task_id}")
        return {"ok": resp.status_code in (200, 204), "task_id": task_id, "task_name": t.get("name")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def export_metadata(nexus: NexusClient) -> dict[str, Any]:
    """Export all repository configs + asset manifests as a dict.

    This works on ANY Nexus version (uses only the standard REST API):
    - All repositories with their full config
    - All assets per repository (path, size, checksum, contentType)

    The result is JSON-serializable and can be downloaded or fed into the
    sync service to replicate repos on another Nexus instance.
    """
    export: dict[str, Any] = {"repositories": [], "assets": {}}

    resp = await nexus.client.get("/service/rest/v1/repositories")
    resp.raise_for_status()
    repos = resp.json() or []
    export["repositories"] = repos

    for repo in repos:
        name = repo.get("name")
        if not name:
            continue
        assets = []
        async for asset in nexus.paginate("/service/rest/v1/assets", params={"repository": name}):
            assets.append({
                "path": asset.get("path"),
                "downloadUrl": asset.get("downloadUrl"),
                "fileSize": asset.get("fileSize"),
                "contentType": asset.get("contentType"),
                "checksum": asset.get("checksum"),
                "format": asset.get("format"),
                "id": asset.get("id"),
            })
        export["assets"][name] = assets
        logger.info("Exported %d assets from %s", len(assets), name)

    return export
