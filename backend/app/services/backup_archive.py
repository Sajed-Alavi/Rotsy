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

import asyncio
import io
import json
import logging
import secrets
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..modules.nexus.connector import NexusClient
from . import make_emitter

logger = logging.getLogger(__name__)


def _permission_error(output_dir: Path, exc: PermissionError) -> RuntimeError:
    """Turn a bare ``PermissionError`` into an actionable message.

    The most common cause is a freshly-created Docker named volume mounted at
    ``output_dir``: Docker seeds it as ``root:root`` unless the mountpoint
    already existed (and was chowned) inside the image, so the app's non-root
    user can create the mountpoint's *parent* but not write inside it. A bare
    ``[Errno 13]`` on a ``BackupRun.error`` column gives an operator nothing to
    act on, so this is raised instead, everywhere a write into ``output_dir``
    can fail this way.
    """
    logger.error("Backup directory %s is not writable: %s", output_dir, exc)
    err = RuntimeError(
        f"Cannot write to backup directory {output_dir}: permission denied. "
        "The container's non-root 'app' user does not own this path — if this "
        "is a pre-existing Docker volume, run "
        "`docker compose run --rm -u root backend chown -R app:app /app/backups` "
        "once to fix it (see the Troubleshooting docs)."
    )
    err.__cause__ = exc
    return err

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


async def _resolve_target_repos(nexus: NexusClient, mode: str, repos: list[str] | None) -> list[str]:
    if mode == "selective":
        if not repos:
            raise ValueError("selective backup requires at least one repository")
        return list(repos)
    if mode == "full":
        resp = await nexus.client.get("/service/rest/v1/repositories")
        resp.raise_for_status()
        return [r.get("name") for r in (resp.json() or []) if r.get("name")]
    raise ValueError(f"unknown backup mode '{mode}'")


async def _download_asset(
    nexus: NexusClient, download_url: str, dest: Path, output_dir: Path, repo: str, path: str,
) -> int | None:
    """Stream one asset to ``dest``. Returns the byte count written, or
    ``None`` if the upstream refused the download (logged, not fatal — the
    caller skips this one asset and continues the backup)."""
    upstream = await nexus.client.send(nexus.client.build_request("GET", download_url), stream=True)
    try:
        if upstream.status_code >= 400:
            logger.warning("skip %s/%s: upstream returned %d", repo, path, upstream.status_code)
            return None
        size = 0
        try:
            f = await asyncio.to_thread(open, dest, "wb")
            try:
                async for chunk in upstream.aiter_raw():
                    await asyncio.to_thread(f.write, chunk)
                    size += len(chunk)
            finally:
                await asyncio.to_thread(f.close)
        except PermissionError as exc:
            raise _permission_error(output_dir, exc)
        return size
    finally:
        await upstream.aclose()


def _prepare_archive_target(
    output_dir: Path, run_id: str, compress: bool,
) -> tuple[Path | None, Path | None, tarfile.TarFile | None, Path | None]:
    """Returns ``(run_dir, scratch_dir, tar, archive_path)`` for this run —
    exactly one of ``run_dir`` or ``(scratch_dir, tar, archive_path)`` is
    populated, matching whether ``compress`` is set."""
    try:
        if compress:
            archive_path = output_dir / f"{run_id}.tar.gz"
            scratch_dir = output_dir / f".{run_id}.scratch"
            scratch_dir.mkdir(parents=True, exist_ok=True)
            tar = tarfile.open(archive_path, "w:gz")
            return None, scratch_dir, tar, archive_path
        run_dir = output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir, None, None, None
    except PermissionError as exc:
        raise _permission_error(output_dir, exc)


async def _backup_repo_assets(
    nexus: NexusClient, output_dir: Path, run_dir: Path | None, scratch_dir: Path | None,
    tar: tarfile.TarFile | None, repo: str, compress: bool, min_free_bytes: int, asset_count: int,
) -> tuple[int, int, int, list[dict[str, Any]]]:
    """Download and (if compressing) archive every asset in one repository.
    Returns ``(repo_bytes, repo_assets, new_asset_count, manifest_entries)``."""
    repo_dir = run_dir / repo if run_dir is not None else None
    repo_bytes = 0
    repo_assets = 0
    bytes_since_disk_check = 0
    entries: list[dict[str, Any]] = []

    async for asset in nexus.paginate("/service/rest/v1/assets", params={"repository": repo}):
        path = asset.get("path") or asset.get("id") or ""
        download_url = asset.get("downloadUrl")
        if not download_url:
            continue
        relpath = _safe_relpath(path)
        if compress:
            dest = scratch_dir / f"{asset_count}.blob"
        else:
            dest = repo_dir / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)

        size = await _download_asset(nexus, download_url, dest, output_dir, repo, path)
        if size is None:
            continue

        if compress:
            tar.add(dest, arcname=str(Path(repo) / relpath))
            dest.unlink(missing_ok=True)

        entries.append({
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

    return repo_bytes, repo_assets, asset_count, entries


def _finalize_manifest(
    manifest: dict[str, Any], run_dir: Path | None, archive_path: Path | None,
    tar: tarfile.TarFile | None, compress: bool,
) -> str:
    manifest_bytes = json.dumps(manifest, indent=2, default=str).encode()
    if compress:
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(manifest_bytes))
        return str(archive_path)
    (run_dir / "manifest.json").write_bytes(manifest_bytes)
    return str(run_dir)


async def create_archive(
    nexus: NexusClient,
    *,
    output_dir: Path,
    mode: str,
    repos: list[str] | None,
    min_free_bytes: int,
    on_progress: ProgressCallback | None = None,
    compress: bool = False,
) -> dict[str, Any]:
    """Back up asset bytes under ``output_dir``.

    ``mode == "full"`` backs up every repository Nexus reports; ``mode ==
    "selective"`` backs up only ``repos`` (required, non-empty).

    When ``compress`` is false (the default, used by the manual on-demand
    backup endpoint), assets are written to a plain per-run directory tree
    exactly as before. When ``compress`` is true (used by scheduled backups),
    assets are written to a single ``<run_id>.tar.gz`` instead: each asset is
    still streamed to a small scratch file first (so a stalled download can't
    corrupt bytes already inside the tar stream), then immediately appended
    with :meth:`tarfile.TarFile.add` and deleted — bounding the extra,
    uncompressed disk usage to the single largest in-flight asset rather than
    the whole backup, so nothing "runs the volume to 2x" the way compressing a
    fully-materialized directory afterwards would.

    Returns a summary: ``run_id``, ``output_path``, ``repos``, ``total_bytes``,
    ``asset_count``, ``per_repo`` (per-repo asset_count/total_bytes),
    ``compressed``.
    """
    emit = make_emitter(on_progress)
    target_repos = await _resolve_target_repos(nexus, mode, repos)

    # Validate every target repo name before creating any directory. In
    # selective mode these come straight from the caller; reject the whole
    # run rather than silently dropping or mangling an unsafe one.
    for repo in target_repos:
        safe_repo_dirname(repo)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise _permission_error(output_dir, exc)
    _ensure_disk_space(output_dir, min_free_bytes)

    run_id = _new_run_id()
    run_dir, scratch_dir, tar, archive_path = _prepare_archive_target(output_dir, run_id, compress)

    manifest: dict[str, Any] = {"run_id": run_id, "mode": mode, "repos": target_repos, "assets": {}}
    total_bytes = 0
    asset_count = 0
    per_repo: dict[str, dict[str, int]] = {}

    try:
        await emit(0, f"backing up {len(target_repos)} repositories")
        total_repos = max(1, len(target_repos))
        for i, repo in enumerate(target_repos):
            repo_bytes, repo_assets, asset_count, entries = await _backup_repo_assets(
                nexus, output_dir, run_dir, scratch_dir, tar, repo, compress, min_free_bytes, asset_count,
            )
            manifest["assets"][repo] = entries
            total_bytes += repo_bytes
            per_repo[repo] = {"asset_count": repo_assets, "total_bytes": repo_bytes}
            await emit(int((i + 1) / total_repos * 100), f"backed up {repo} ({repo_assets} assets, {repo_bytes} bytes)")

        manifest["total_bytes"] = total_bytes
        manifest["asset_count"] = asset_count
        output_path = _finalize_manifest(manifest, run_dir, archive_path, tar, compress)
    finally:
        if tar is not None:
            tar.close()
        if scratch_dir is not None:
            shutil.rmtree(scratch_dir, ignore_errors=True)

    return {
        "run_id": run_id,
        "output_path": output_path,
        "repos": target_repos,
        "total_bytes": total_bytes,
        "asset_count": asset_count,
        "per_repo": per_repo,
        "compressed": compress,
    }
