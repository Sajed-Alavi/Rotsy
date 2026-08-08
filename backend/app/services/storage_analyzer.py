"""Deep Storage Analyzer (Feature A) — multi-format.

Two analysis modes, dispatched by repository format:

  * **Docker** (``format == "docker"``): the original deep analysis. Phase 1
    sums physical asset sizes; Phase 2 enumerates ``(image, tag)`` components;
    Phase 3 recursively traverses multi-arch manifests to compute per-tag
    logical sizes and a globally-deduped active payload. ``wasted = raw - active``.

  * **Generic** (maven2, nuget, npm, pypi, raw, ...): no manifest concept.
    Phase 1 sums physical asset sizes; Phase 2 groups assets by component
    ``(group/name, version)``. ``active == total`` and ``wasted == 0`` because
    every asset is referenced by its component. The value here is the full
    per-component size breakdown — something the native Nexus UI hides.

The result schema is uniform across both modes so the frontend renders one
table; for docker, ``items[].versions`` are tags; for generic, they are
versions. Field name ``items`` (was ``images``) is format-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from ..modules.nexus.connector import DOCKER_MANIFEST_ACCEPT, NexusClient
from .images import component_timestamps

logger = logging.getLogger(__name__)

# Depth guard from the original script: stop recursing into nested manifest
# lists beyond this depth.
_MAX_MANIFEST_DEPTH = 2


class ProgressCallback(Protocol):
    """Async callable invoked with a normalised event dict."""

    async def __call__(self, event: dict[str, Any]) -> None: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _earliest(*values: str | None) -> str | None:
    """Oldest of the given RFC3339 timestamps, ignoring None.

    Nexus emits a fixed RFC3339 layout, so string comparison orders these
    correctly without the cost of parsing every one.
    """
    present = [v for v in values if v]
    return min(present) if present else None


def _latest(*values: str | None) -> str | None:
    """Newest of the given RFC3339 timestamps, ignoring None."""
    present = [v for v in values if v]
    return max(present) if present else None


def _manifest_layer_blobs(data: dict[str, Any]) -> tuple[int, list[tuple[str | None, int]]]:
    """Config + layer blobs of a single-arch docker/oci manifest."""
    local_size = 0
    blobs: list[tuple[str | None, int]] = []
    config = data.get("config") or {}
    config_size = config.get("size") or 0
    if config_size:
        local_size += config_size
        blobs.append((config.get("digest"), config_size))
    for layer in data.get("layers") or []:
        layer_size = layer.get("size") or 0
        if layer_size:
            local_size += layer_size
            blobs.append((layer.get("digest"), layer_size))
    return local_size, blobs


def _build_item_tree(
    item_version_sizes: dict[str, dict[str, int]],
    timestamps: dict[tuple[str, str], tuple[str | None, str | None]] | None = None,
) -> list[dict[str, Any]]:
    """Convert ``{name: {version: size}}`` into a sorted tree payload.

    Names (images for docker, group/name for generic) are sorted by total size
    descending; versions within an item likewise.

    ``timestamps`` maps ``(name, version)`` to ``(created_at, last_modified)``.
    Knowing an image's size without knowing when it arrived makes it impossible
    to judge whether it is worth keeping, so both are carried through to the UI.
    """
    stamps = timestamps or {}
    items: list[dict[str, Any]] = []
    for name, versions in item_version_sizes.items():
        sorted_versions = []
        for version, size in sorted(versions.items(), key=lambda kv: kv[1], reverse=True):
            created, modified = stamps.get((name, version), (None, None))
            sorted_versions.append({
                "version": version,
                "size_bytes": size,
                "created_at": created,
                "last_modified": modified,
            })
        newest = max((v["created_at"] for v in sorted_versions if v["created_at"]), default=None)
        oldest = min((v["created_at"] for v in sorted_versions if v["created_at"]), default=None)
        items.append(
            {
                "name": name,
                "total_bytes": sum(versions.values()),
                "version_count": len(versions),
                "created_at": oldest,
                "last_pushed_at": newest,
                "versions": sorted_versions,
            }
        )
    items.sort(key=lambda it: it["total_bytes"], reverse=True)
    return items


class StorageAnalyzer:
    """Multi-format async storage analyzer."""

    def __init__(self, nexus: NexusClient, *, max_concurrency: int) -> None:
        self._nexus = nexus
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def analyze_repo(
        self,
        repo: str,
        *,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        fmt_hint: str | None = None,
    ) -> dict[str, Any]:
        """Analyze ``repo`` and return a uniform result dict.

        Dispatches to docker vs generic mode based on the repo format.
        ``fmt_hint``, when supplied, skips the extra ``GET /repositories/{repo}``
        lookup — the caller (the frontend) already knows the format from the
        repo listing it used to populate the selector.
        """
        async def _noop(_event: dict[str, Any]) -> None:  # pragma: no cover  # NOSONAR — must stay async: it's awaited below as a stand-in for a real on_progress callback
            return None

        emit = on_progress or _noop
        await emit({"type": "phase", "phase": "init", "message": f"Starting analysis for '{repo}'"})

        fmt = fmt_hint or await self._repo_format(repo)
        await emit({"type": "phase", "phase": "detect_format", "message": f"Repository format: {fmt}"})

        if fmt == "docker":
            return await self._analyze_docker(repo, fmt, emit)
        return await self._analyze_generic(repo, fmt, emit)

    # ------------------------------------------------------------------
    # Format detection
    # ------------------------------------------------------------------
    async def _repo_format(self, repo: str) -> str:
        """Return the repository format (docker, maven2, nuget, ...)."""
        try:
            resp = await self._nexus.client.get(f"/service/rest/v1/repositories/{repo}")
            resp.raise_for_status()
            return (resp.json() or {}).get("format", "unknown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not detect format for '%s': %s", repo, exc)
            return "unknown"

    # ==================================================================
    # DOCKER mode — deep manifest traversal (original algorithm)
    # ==================================================================
    async def _analyze_docker(
        self,
        repo: str,
        fmt: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        total_raw_bytes, (target_tags, timestamps) = await asyncio.gather(
            self._total_physical_usage(repo, emit),
            self._collect_docker_components(repo, emit),
        )

        if not target_tags:
            logger.warning("No image components found in repo '%s'.", repo)
            return self._empty_result(repo, fmt, "docker", total_raw_bytes)

        image_tag_sizes, active_bytes = await self._deep_scan(target_tags, repo, emit)
        wasted_bytes = max(0, total_raw_bytes - active_bytes)
        result = {
            "repo": repo,
            "format": fmt,
            "mode": "docker",
            "scanned_at": _now_iso(),
            "stats": {
                "total_bytes": total_raw_bytes,
                "active_bytes": active_bytes,
                "wasted_bytes": wasted_bytes,
                "item_count": len(target_tags),
            },
            "items": await asyncio.to_thread(_build_item_tree, image_tag_sizes, timestamps),
        }
        await emit({"type": "result", "message": "Analysis complete", "result": result})
        return result

    async def _total_physical_usage(
        self,
        repo: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> int:
        """Phase 1: sum ``fileSize`` of every asset (shared by both modes)."""
        await emit({"type": "phase", "phase": "scanning_assets", "message": "Scanning raw physical disk usage"})
        total = 0
        async for asset in self._nexus.paginate("/service/rest/v1/assets", params={"repository": repo}):
            size = asset.get("fileSize") or 0
            if size:
                total += size
        await emit({"type": "progress", "phase": "scanning_assets", "percent": 100, "message": "Assets scan complete"})
        logger.info("[%s] Phase 1 raw disk bytes: %d", repo, total)
        return total

    async def _collect_docker_components(
        self,
        repo: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> tuple[list[tuple[str, str]], dict[tuple[str, str], tuple[str | None, str | None]]]:
        """Phase 2: the live ``(image, tag)`` list plus each tag's push time.

        The timestamps come from the same listing, so surfacing "when was this
        pushed" costs no extra requests — Nexus already returns the asset
        metadata that carries it.
        """
        await emit({"type": "phase", "phase": "collecting_tags", "message": "Collecting live image tags"})
        targets: list[tuple[str, str]] = []
        timestamps: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        async for component in self._nexus.paginate("/service/rest/v1/components", params={"repository": repo}):
            name = component.get("name")
            version = component.get("version")
            if name and version:
                targets.append((name, version))
                timestamps[(name, version)] = component_timestamps(component)
        await emit({"type": "progress", "phase": "collecting_tags", "percent": 100, "message": f"Discovered {len(targets)} tags"})
        logger.info("[%s] Phase 2 discovered %d tags.", repo, len(targets))
        return targets, timestamps

    async def _deep_scan(
        self,
        targets: list[tuple[str, str]],
        repo: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> tuple[dict[str, dict[str, int]], int]:
        """Concurrent deep manifest traversal (docker only)."""
        await emit({"type": "phase", "phase": "deep_scan", "message": f"Recursively extracting layer metrics for {len(targets)} tags"})

        image_tag_sizes: dict[str, dict[str, int]] = defaultdict(dict)
        unique_active_blobs: set[str] = set()
        active_bytes = 0
        completed = 0
        total = len(targets)

        async def worker(image: str, tag: str) -> None:
            nonlocal active_bytes, completed
            logical_size, blobs = await self._process_manifest(repo, image, tag, depth=0)
            if logical_size > 0:
                image_tag_sizes[image][tag] = logical_size
                for digest, size in blobs:
                    if digest and digest not in unique_active_blobs:
                        unique_active_blobs.add(digest)
                        active_bytes += size
            completed += 1
            if completed % 5 == 0 or completed == total:
                pct = int(completed / total * 100) if total else 100
                await emit({"type": "progress", "phase": "deep_scan", "percent": pct,
                            "completed": completed, "total": total,
                            "message": f"Analyzed {completed}/{total} tags"})

        async def bounded(image: str, tag: str) -> None:
            async with self._semaphore:
                await worker(image, tag)

        await asyncio.gather(*(bounded(img, tag) for img, tag in targets))
        logger.info("[%s] Phase 3 active payload bytes (deduped): %d", repo, active_bytes)
        return image_tag_sizes, active_bytes

    async def _process_manifest(
        self,
        repo: str,
        image: str,
        ref: str,
        *,
        depth: int,
    ) -> tuple[int, list[tuple[str | None, int]]]:
        """Recursively process a docker/oci manifest."""
        if depth > _MAX_MANIFEST_DEPTH:
            return 0, []
        url = f"/repository/{repo}/v2/{image}/manifests/{ref}"
        try:
            response = await self._nexus.get(url, headers={"Accept": DOCKER_MANIFEST_ACCEPT})
        except Exception as exc:  # noqa: BLE001
            logger.debug("Manifest fetch failed for %s:%s (depth %d): %s", image, ref, depth, exc)
            return 0, []
        if response.status_code != 200:
            return 0, []
        try:
            data = response.json()
        except ValueError:
            return 0, []

        if "layers" in data:  # single-arch manifest
            return _manifest_layer_blobs(data)
        if "manifests" in data:  # multi-arch manifest list
            return await self._process_manifest_list(repo, image, data, depth=depth)
        return 0, []

    async def _process_manifest_list(
        self, repo: str, image: str, data: dict[str, Any], *, depth: int,
    ) -> tuple[int, list[tuple[str | None, int]]]:
        child_digests = [d for d in (m.get("digest") for m in data.get("manifests") or []) if d]
        results = await asyncio.gather(*(
            self._process_manifest(repo, image, digest, depth=depth + 1) for digest in child_digests
        ))
        local_size = 0
        blobs: list[tuple[str | None, int]] = []
        for child_size, child_blobs in results:
            local_size += child_size
            blobs.extend(child_blobs)
        return local_size, blobs

    # ==================================================================
    # GENERIC mode — component/asset aggregation (no manifests)
    # ==================================================================
    async def _analyze_generic(
        self,
        repo: str,
        fmt: str,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        """For non-docker repos: aggregate asset sizes by component.

        Each component (group/name + version) gets the sum of its asset
        sizes. ``active == total`` because every asset is referenced.
        """
        # Gather assets + components concurrently is tempting, but we need the
        # asset sizes first to know component sizes. Nexus assets expose their
        # component identity, so a single assets walk suffices.
        await emit({"type": "phase", "phase": "collecting_components", "message": "Aggregating components and assets"})

        item_version_sizes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        timestamps: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        total_bytes = 0
        asset_count = 0

        async for asset in self._nexus.paginate("/service/rest/v1/assets", params={"repository": repo}):
            asset_count += 1
            size = asset.get("fileSize") or 0
            total_bytes += size
            component = asset.get("component") or {}
            name = component.get("name")
            version = component.get("version")
            # Proxy repositories often return component=None; fall back to the
            # asset path so we still get a meaningful name + version.
            if not name or not version:
                parsed = parse_asset_path(asset.get("path", ""), fmt)
                name = name or parsed[0]
                version = version or parsed[1]
            if size:
                item_version_sizes[name][version] += size

            # A component version spans several assets: keep the earliest
            # creation and the latest modification across all of them.
            created = str(asset["blobCreated"]) if asset.get("blobCreated") else None
            modified = str(asset["lastModified"]) if asset.get("lastModified") else created
            prev_created, prev_modified = timestamps.get((name, version), (None, None))
            timestamps[(name, version)] = (
                _earliest(prev_created, created),
                _latest(prev_modified, modified),
            )

        item_count = sum(len(v) for v in item_version_sizes.values())
        await emit({"type": "progress", "phase": "collecting_components", "percent": 100,
                    "message": f"{asset_count} assets · {item_count} component versions"})

        result = {
            "repo": repo,
            "format": fmt,
            "mode": "generic",
            "scanned_at": _now_iso(),
            "stats": {
                "total_bytes": total_bytes,
                "active_bytes": total_bytes,  # every asset is referenced
                "wasted_bytes": 0,
                "item_count": item_count,
                "asset_count": asset_count,
            },
            "items": await asyncio.to_thread(_build_item_tree, item_version_sizes, timestamps),
        }
        await emit({"type": "result", "message": "Analysis complete", "result": result})
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _empty_result(self, repo: str, fmt: str, mode: str, total_bytes: int) -> dict[str, Any]:
        return {
            "repo": repo,
            "format": fmt,
            "mode": mode,
            "scanned_at": _now_iso(),
            "stats": {
                "total_bytes": total_bytes,
                "active_bytes": 0,
                "wasted_bytes": total_bytes,
                "item_count": 0,
            },
            "items": [],
        }


def parse_asset_path(path: str, fmt: str) -> tuple[str, str]:
    """Best-effort ``(name, version)`` from an asset path when Nexus omits the
    component (common for proxy and raw repositories).

    Maven2 layout: ``groupId/artifactId/version/artifactId-version.ext``
        e.g. ``org/apache/commons/commons-lang3/3.13.0/commons-lang3-3.13.0.jar``
        -> ("org.apache.commons:commons-lang3", "3.13.0")
    Raw / generic: the directory structure is arbitrary, so we treat the last
    path segment as the file name and its parent directory as the "name":
        e.g. ``/screenshots/foo.png`` -> ("screenshots", "foo.png")
    Fallback: last segment as name, "unspecified" version.
    """
    if not path:
        return ("unknown", "unspecified")
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return ("unknown", "unspecified")

    filename = parts[-1]

    if fmt == "maven2" and len(parts) >= 4:
        # [...group dirs..., artifactId, version, artifactId-version.ext]
        artifact = parts[-3]
        version = parts[-2]
        group = ".".join(parts[:-3])
        name = f"{group}:{artifact}" if group else artifact
        return (name, version)

    # Raw / arbitrary layout: name = parent dir, version = filename.
    if len(parts) >= 2:
        return (parts[-2], filename)

    return (filename, "unspecified")
