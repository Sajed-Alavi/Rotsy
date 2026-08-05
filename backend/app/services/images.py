"""Docker image inventory and deletion.

Nexus models a Docker repository as a flat list of *assets* — manifests and
layer blobs like ``v2/myapp/blobs/sha256:6f2a…``. That is what the raw asset
listing shows, and it is not what anyone wants to look at: an operator thinks in
**images and tags**, not blobs.

This module turns the flat listing into the image → tag structure, carrying the
push time and physical size for each tag, and provides deletion that reports
*why* a delete failed instead of silently doing nothing.

Timestamps: for a Docker tag the meaningful "created" time is when its
**manifest** was written, since layer blobs are shared between tags and can be
far older. :func:`component_timestamps` prefers the manifest asset and falls
back to the oldest asset for non-Docker formats.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..modules.nexus.connector import NexusClient

logger = logging.getLogger(__name__)


def _iso(value: Any) -> str | None:
    return str(value) if value else None


def component_timestamps(component: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(created_at, last_modified)`` for a component.

    Nexus reports these per *asset*, never on the component itself — code that
    reads ``component["blobCreated"]`` gets ``None`` every time.
    """
    assets = component.get("assets") or []
    if not assets:
        return None, None

    # Docker: the manifest asset's creation time is the push time.
    manifest = next((a for a in assets if "/manifests/" in (a.get("path") or "")), None)
    if manifest is not None:
        return _iso(manifest.get("blobCreated")), _iso(
            manifest.get("lastModified") or manifest.get("blobCreated")
        )

    created = [a.get("blobCreated") for a in assets if a.get("blobCreated")]
    modified = [a.get("lastModified") or a.get("blobCreated") for a in assets
                if a.get("lastModified") or a.get("blobCreated")]
    return (
        _iso(min(created)) if created else None,
        _iso(max(modified)) if modified else None,
    )


def component_size(component: dict[str, Any]) -> int:
    """Sum of the component's asset sizes (physical bytes it accounts for)."""
    return sum((a.get("fileSize") or 0) for a in component.get("assets") or [])


def component_digest(component: dict[str, Any]) -> str:
    """Manifest digest of a Docker component, when Nexus exposes one."""
    for asset in component.get("assets") or []:
        if "/manifests/" in (asset.get("path") or ""):
            checksum = (asset.get("checksum") or {}).get("sha256")
            if checksum:
                return f"sha256:{checksum}"
    return ""


def component_display_name(component: dict[str, Any]) -> str:
    """The image name a component belongs to, as shown in the UI and used for scoping."""
    name = component.get("name") or ""
    group = component.get("group") or ""
    if group and not name.startswith(group):
        return f"{group}/{name}".lstrip("/")
    return name


async def asset_image_map(nexus: NexusClient, repo: str) -> dict[str, str]:
    """Map every asset path in ``repo`` to the image that owns it.

    This is the *authoritative* answer to "which image does this asset belong
    to", straight from the components API — as opposed to inferring it by
    splitting the raw asset path, which cannot be trusted for an access-control
    decision (a crafted path can be parsed as one image while Nexus resolves it
    as another). Costs one pagination pass, which is the trade the RBAC scope
    checks in ``routers/repositories.py`` make for correctness.

    Keys are normalized with surrounding slashes stripped so callers can look
    up both ``foo/bar.jar`` and ``/foo/bar.jar``.
    """
    mapping: dict[str, str] = {}
    async for component in nexus.paginate("/service/rest/v1/components", params={"repository": repo}):
        display = component_display_name(component)
        if not display:
            continue
        for asset in component.get("assets") or []:
            path = (asset.get("path") or "").strip("/")
            if path:
                mapping[path] = display
    return mapping


async def list_images(nexus: NexusClient, repo: str) -> list[dict[str, Any]]:
    """Group a repository's components into images, each with its tags.

    Works for every format: for Docker the children are tags, for maven2/npm/etc
    they are versions. Sorted by name, with tags newest-first so the most recent
    push is the first thing on screen.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    async for component in nexus.paginate("/service/rest/v1/components", params={"repository": repo}):
        name = component.get("name")
        version = component.get("version")
        if not name or not version:
            continue
        created, modified = component_timestamps(component)
        display = component_display_name(component)
        grouped.setdefault(display, []).append({
            "component_id": component.get("id"),
            "tag": version,
            "size_bytes": component_size(component),
            "asset_count": len(component.get("assets") or []),
            "created_at": created,
            "last_modified": modified,
            "digest": component_digest(component),
            "format": component.get("format"),
        })

    images: list[dict[str, Any]] = []
    for name, tags in grouped.items():
        # Newest first; tags without a timestamp sort last rather than crashing
        # the comparison against None.
        tags.sort(key=lambda t: (t["created_at"] or "", t["tag"]), reverse=True)
        images.append({
            "name": name,
            "tag_count": len(tags),
            "total_bytes": sum(t["size_bytes"] for t in tags),
            "last_pushed_at": next((t["created_at"] for t in tags if t["created_at"]), None),
            "tags": tags,
        })
    images.sort(key=lambda i: i["name"])
    return images


async def delete_component(nexus: NexusClient, component_id: str) -> tuple[bool, str]:
    """Delete one component (one image tag). Returns ``(ok, reason)``.

    The reason matters: deletion previously returned a bare boolean, so a 404
    from a stale id, a 403 from insufficient privileges and a network error were
    indistinguishable — all of them just looked like "nothing was deleted".
    """
    # Component ids are opaque, base64-ish strings; encode them so an id
    # containing a reserved character cannot alter the request path.
    path = f"/service/rest/v1/components/{quote(str(component_id), safe='')}"
    try:
        response = await nexus.client.delete(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Delete of component %s failed: %s", component_id, exc)
        return False, f"could not reach Nexus: {exc}"

    if response.status_code in (200, 204):
        return True, ""
    if response.status_code == 404:
        return False, "component not found — it may already be deleted; refresh the list"
    if response.status_code in (401, 403):
        return False, ("Nexus refused the delete (HTTP %d) — the service account needs delete "
                       "privileges on this repository" % response.status_code)
    if response.status_code == 405:
        return False, ("this repository does not allow deletion — its write policy is probably "
                       "'Disable redeploy' or 'Read-only'; change it in Nexus")
    body = (response.text or "").strip()[:200]
    return False, f"Nexus returned HTTP {response.status_code}{f': {body}' if body else ''}"


async def delete_components(nexus: NexusClient, component_ids: list[str]) -> dict[str, Any]:
    """Delete several components, reporting each outcome separately.

    Deleting is sequential on purpose: Nexus serialises component deletes
    internally, and firing them concurrently mostly produces lock contention and
    confusing partial failures.
    """
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for component_id in component_ids:
        ok, reason = await delete_component(nexus, component_id)
        if ok:
            deleted.append(component_id)
        else:
            failed.append({"component_id": component_id, "reason": reason})
    return {"deleted": deleted, "failed": failed,
            "deleted_count": len(deleted), "failed_count": len(failed)}


async def trigger_compact(nexus: NexusClient) -> dict[str, Any]:
    """Ask Nexus to run its 'Compact blob store' task so blobs are reclaimed.

    Deleting a component removes the tag immediately but leaves the blobs on
    disk until this task runs — which is why disk usage can look unchanged after
    a successful delete. Best-effort, and it reports honestly when the task does
    not exist: Nexus does not create one by default, and the operator has to.
    """
    try:
        response = await nexus.client.get("/service/rest/v1/tasks")
        if response.status_code != 200:
            return {"triggered": False,
                    "reason": f"could not list Nexus tasks (HTTP {response.status_code})"}
        tasks = (response.json() or {}).get("items", [])
    except Exception as exc:  # noqa: BLE001
        return {"triggered": False, "reason": f"could not list Nexus tasks: {exc}"}

    for task in tasks:
        type_id = (task.get("type") or "").lower()
        if "compact" not in type_id:
            continue
        task_id = task.get("id")
        if not task_id:
            continue
        try:
            run = await nexus.client.post(f"/service/rest/v1/tasks/{quote(str(task_id), safe='')}/run")
        except Exception as exc:  # noqa: BLE001
            return {"triggered": False, "reason": f"could not run compact task: {exc}"}
        if run.status_code in (200, 204):
            logger.info("Triggered Nexus compact task %s", task_id)
            return {"triggered": True, "task_id": task_id}
        return {"triggered": False,
                "reason": f"compact task returned HTTP {run.status_code}"}

    return {"triggered": False,
            "reason": ("no 'Compact blob store' task exists in Nexus. Tags are deleted, but the "
                       "disk space stays allocated until one runs. Create it in Nexus under "
                       "Administration → System → Tasks.")}
