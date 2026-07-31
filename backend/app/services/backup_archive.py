"""Real byte-level Nexus backup/archive to a local directory.

Distinct from :mod:`app.services.backup` (Nexus scheduler-task trigger +
metadata-only export — it records asset paths/checksums/download URLs but
never writes actual bytes anywhere). This module downloads and persists every
selected asset's bytes, plus a ``manifest.json``, under a per-run directory on
the dedicated backup volume — full (every repository) or selective (a
caller-supplied list).

Reuses the same streaming-download idiom already proven in
``routers/repositories.py``'s ``download_asset`` (``nexus.client.send(...,
stream=True)`` + ``aiter_raw()``), rather than buffering whole assets in
memory the way :func:`app.services.sync.sync_repository` does — acceptable
there for typically-small artifacts, not safe here for a full-repo archive
that may include large Docker layer blobs.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..core.nexus_client import NexusClient

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], Awaitable[None]]

# How often to re-check free disk space during a run, so a long backup aborts
# cleanly instead of filling the volume to zero.
#
# Both bounds are needed. The asset count alone leaves a gap: 50 Docker layer
# blobs can be several GB, enough to run the volume to zero inside a single
# check window. The byte bound closes that; the asset bound still catches a
# long tail of small files that individually never trip the byte threshold.
_DISK_CHECK_EVERY = 50
_DISK_CHECK_EVERY_BYTES = 256 * 1024 * 1024  # 256 MiB


def _new_run_id() -> str:
    """Return a unique, chronologically sortable run id.

    The timestamp alone has one-second resolution, so two runs starting in the
    same second shared a ``run_dir`` and interleaved their writes — producing a
    backup that reported success but had a corrupt manifest. The random suffix
    makes the collision effectively impossible while keeping runs sortable.
    """
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def _safe_relpath(path: str) -> Path:
    """Normalize a Nexus asset path into a safe relative filesystem path.

    Asset paths are server-supplied strings; nothing upstream validates they
    can't contain ``..`` segments, and this is the first place in the codebase
    that writes to disk based on that data — so this is the one place that
    must guard against escaping the run's output directory.
    """
    parts = [p for p in path.strip("/").split("/") if p not in ("", ".", "..")]
    if not parts:
        parts = ["_"]
    return Path(*parts)


class InvalidRepositoryName(ValueError):
    """Raised when a caller-supplied repository name isn't a safe directory segment."""


def safe_repo_dirname(repo: str) -> str:
    """Validate a repository name is safe to use as a single directory segment.

    Unlike asset paths (``_safe_relpath``), a repo name must never contain a
    path separator at all — it names one directory, not a nested path. In
    selective mode ``repo`` comes straight from the caller (``POST
    /system/backup/archive``): ``pathlib``'s ``/`` operator silently discards
    the left operand when the right one is absolute (so ``repo="/etc"``
    collapses ``run_dir / repo`` to ``/etc`` outright), and a relative
    ``../../etc`` walks out of the run directory the same way asset paths
    could before ``_safe_relpath`` existed. Reject anything that isn't a
    single plain segment rather than trying to sanitize it.
    """
    if not repo or repo in (".", "..") or "/" in repo or "\\" in repo:
        raise InvalidRepositoryName(f"unsafe repository name: {repo!r}")
    return repo


def _ensure_disk_space(output_dir: Path, min_free_bytes: int) -> None:
    usage = shutil.disk_usage(output_dir)
    if usage.free < min_free_bytes:
        raise RuntimeError(
            f"Only {usage.free} bytes free under {output_dir}; need at least {min_free_bytes}. "
            "Aborting to avoid filling the volume."
        )


async def create_archive(
    nexus: NexusClient,
    *,
    output_dir: Path,
    mode: str,
    repos: list[str] | None,
    min_free_bytes: int,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Back up asset bytes to a new per-run directory under ``output_dir``.

    ``mode == "full"`` backs up every repository Nexus reports; ``mode ==
    "selective"`` backs up only ``repos`` (required, non-empty).

    Returns a summary: ``run_id``, ``output_path``, ``repos``, ``total_bytes``,
    ``asset_count``, ``per_repo`` (per-repo asset_count/total_bytes).
    """
    async def emit(pct: int, msg: str) -> None:
        if on_progress is not None:
            await on_progress(pct, msg)

    if mode == "selective":
        if not repos:
            raise ValueError("selective backup requires at least one repository")
        target_repos = list(repos)
    elif mode == "full":
        resp = await nexus.client.get("/service/rest/v1/repositories")
        resp.raise_for_status()
        target_repos = [r.get("name") for r in (resp.json() or []) if r.get("name")]
    else:
        raise ValueError(f"unknown backup mode '{mode}'")

    # Validate every target repo name before creating any directory. In
    # selective mode these come straight from the caller; reject the whole
    # run rather than silently dropping or mangling an unsafe one.
    for repo in target_repos:
        safe_repo_dirname(repo)

    output_dir.mkdir(parents=True, exist_ok=True)
    _ensure_disk_space(output_dir, min_free_bytes)

    run_id = _new_run_id()
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"run_id": run_id, "mode": mode, "repos": target_repos, "assets": {}}
    total_bytes = 0
    asset_count = 0
    bytes_since_disk_check = 0
    per_repo: dict[str, dict[str, int]] = {}

    await emit(0, f"backing up {len(target_repos)} repositories")
    total_repos = max(1, len(target_repos))
    for i, repo in enumerate(target_repos):
        repo_dir = run_dir / repo
        repo_bytes = 0
        repo_assets = 0
        manifest["assets"][repo] = []

        async for asset in nexus.paginate("/service/rest/v1/assets", params={"repository": repo}):
            path = asset.get("path") or asset.get("id") or ""
            download_url = asset.get("downloadUrl")
            if not download_url:
                continue
            dest = repo_dir / _safe_relpath(path)
            dest.parent.mkdir(parents=True, exist_ok=True)

            upstream = await nexus.client.send(nexus.client.build_request("GET", download_url), stream=True)
            try:
                if upstream.status_code >= 400:
                    logger.warning("skip %s/%s: upstream returned %d", repo, path, upstream.status_code)
                    continue
                size = 0
                with open(dest, "wb") as f:
                    async for chunk in upstream.aiter_raw():
                        f.write(chunk)
                        size += len(chunk)
            finally:
                await upstream.aclose()

            manifest["assets"][repo].append({
                "path": path, "size": size,
                "checksum": asset.get("checksum"), "contentType": asset.get("contentType"),
            })
            repo_bytes += size
            repo_assets += 1
            asset_count += 1
            bytes_since_disk_check += size
            if (
                asset_count % _DISK_CHECK_EVERY == 0
                or bytes_since_disk_check >= _DISK_CHECK_EVERY_BYTES
            ):
                _ensure_disk_space(output_dir, min_free_bytes)
                bytes_since_disk_check = 0

        total_bytes += repo_bytes
        per_repo[repo] = {"asset_count": repo_assets, "total_bytes": repo_bytes}
        await emit(int((i + 1) / total_repos * 100), f"backed up {repo} ({repo_assets} assets, {repo_bytes} bytes)")

    manifest["total_bytes"] = total_bytes
    manifest["asset_count"] = asset_count
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    return {
        "run_id": run_id,
        "output_path": str(run_dir),
        "repos": target_repos,
        "total_bytes": total_bytes,
        "asset_count": asset_count,
        "per_repo": per_repo,
    }
