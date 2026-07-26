"""Trivy + Grype scanner integration.

Runs the binaries installed in the backend container (see Dockerfile) against
images hosted in the Nexus Docker registry. The Nexus credentials come from
the app's service account (the same ones it uses for the REST API).

Binaries are invoked via :mod:`asyncio.create_subprocess_exec`; their JSON
output is parsed and persisted into ``scan_reports`` / ``scan_vulnerabilities``.

Database lifecycle helpers:
  * :func:`db_status` — read each scanner's cache (Trivy ``metadata.json`` has
    ``Version``/``CreatedAt``/``NextUpdate``/``DownloadedAt`` — exactly the
    "what day is this DB for" info; grype exposes a ``metadata.json`` too).
  * :func:`update_scanner_dbs` — streams stdout line-by-line so we can parse
    Trivy's ``[xxMB/yyMB]`` progress, and prunes old DB files after success.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.nexus_client import NexusClient
from ..models import ScanReport, Vulnerability

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], Awaitable[None]]

# Cache locations. Trivy lays out as <cache>/db/metadata.json.
# Grype lays out as <cache>/<schema>/metadata.json (e.g. ~/.cache/grype/5/...).
# The Dockerfile sets TRIVY_CACHE_DIR and GRYPE_CACHE_DIR and pre-creates them
# writable for the non-root app user.
_TRIVY_CACHE_ROOT = Path(os.environ.get("TRIVY_CACHE_DIR") or (Path.home() / ".cache" / "trivy"))
_TRIVY_DB_DIR = _TRIVY_CACHE_ROOT / "db"
# Grype stores under a "grype-db" dir regardless of the env var name; account
# for both <root>/grype-db and <root>/ (older layouts) by searching.
_GRYPE_CACHE_ROOT = Path(os.environ.get("GRYPE_CACHE_DIR") or (Path.home() / ".cache" / "grype"))
_GRYPE_DB_DIR = _GRYPE_CACHE_ROOT

# Offline / air-gapped import directory. On a restricted network (Docker Hub,
# ghcr.io and github.com blocked) the DBs can't be pulled at runtime. Instead
# an operator downloads them on a machine with internet, copies the archives
# into this host folder (mounted into the container by docker-compose), and
# triggers an *import* which extracts them straight into the scanner caches —
# no network needed. See :func:`import_offline_dbs`.
_OFFLINE_DB_DIR = Path(os.environ.get("SCANNER_OFFLINE_DIR") or "/app/offline-db")


def _registry_ref(nexus: NexusClient, repo: str, image_ref: str) -> tuple[str, str]:
    """Return ``(registry_host_with_port, full_image_ref)``.

    For Nexus docker repositories, the pull path is:
        ``registry:port/v2/{repo}/{image}/manifests/{tag}``
    So the full image reference for trivy/grype must include the repo name:
        ``registry:port/{repo}/{image}:{tag}``

    ``image_ref`` comes in as ``{image}:{tag}`` (e.g. ``nginx:1.25``).
    We need to produce ``{host}:{port}/{repo}/{image}:{tag}``.
    """
    from urllib.parse import urlparse
    base_url = str(nexus.client.base_url).rstrip("/")
    parsed = urlparse(base_url)
    host = parsed.hostname or "host.docker.internal"
    port = f":{parsed.port}" if parsed.port else ""
    registry = f"{host}{port}"
    # repo is the Nexus repository name (e.g. "testing-docker").
    # image_ref is "image:tag" (e.g. "nexus-project-backend:latest").
    full_ref = f"{registry}/{repo}/{image_ref}"
    return registry, full_ref


def _which(bin_name: str) -> str | None:
    return shutil.which(bin_name)


async def _run(args: list[str], timeout: float = 300.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Scanner timed out after {timeout}s: {' '.join(args)}")
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


# ---------------------------------------------------------------------------
# DB status (the "what day is this DB for" view)
# ---------------------------------------------------------------------------
def _parse_iso(value) -> str | None:
    if not value:
        return None
    try:
        # Trivy/grype write RFC3339 strings.
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Reject Go's zero-time ("0001-01-01T00:00:00Z") which Trivy writes for
        # unset fields like DownloadedAt — it's not a real date.
        if dt.year <= 1:
            return None
        return dt.isoformat()
    except (ValueError, TypeError):
        return str(value) if value else None


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _trivy_db_status() -> dict[str, Any]:
    out: dict[str, Any] = {"installed": _which("trivy") is not None}
    if not _TRIVY_DB_DIR.exists():
        out["present"] = False
        return out
    meta_file = _TRIVY_DB_DIR / "metadata.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text())
            # The DB's "as of" date: Trivy writes UpdatedAt (build time). Older
            # layouts used CreatedAt. DownloadedAt is often Go zero-time when
            # the DB was extracted rather than pulled by trivy itself.
            built = _parse_iso(meta.get("UpdatedAt") or meta.get("CreatedAt"))
            out.update({
                "present": True,
                "version": meta.get("Version"),
                "created_at": built,
                "updated_at": built,
                "next_update": _parse_iso(meta.get("NextUpdate")),
                "downloaded_at": _parse_iso(meta.get("DownloadedAt")),
            })
        except (json.JSONDecodeError, OSError):
            out["present"] = True
    else:
        out["present"] = False
    out["size_bytes"] = _dir_size(_TRIVY_DB_DIR)
    out["path"] = str(_TRIVY_DB_DIR)
    return out


def _grype_db_status() -> dict[str, Any]:
    out: dict[str, Any] = {"installed": _which("grype") is not None}
    if not _GRYPE_DB_DIR.exists():
        out["present"] = False
        return out
    # Grype's on-disk layout depends on the DB schema version:
    #   * schema v5 and earlier: <cache>/<n>/metadata.json  (has "built"/"schema")
    #   * schema v6+ (grype >= 0.8x): <cache>/db/6/import.json + a
    #     vulnerability.db; the build date lives in import.json's "source" URL
    #     (…/vulnerability-db_v6.1.9_2026-07-24T00:34:54Z_….tar.zst) and the
    #     client version in "client_version".
    # Probe both so the UI reflects the DB that actually downloaded.
    v5_meta = next(_GRYPE_DB_DIR.rglob("metadata.json"), None)
    v6_meta = next(_GRYPE_DB_DIR.rglob("import.json"), None)

    if v5_meta and v5_meta.is_file():
        try:
            meta = json.loads(v5_meta.read_text())
            out.update({
                "present": True,
                "version": meta.get("version") or meta.get("Version"),
                "built": _parse_iso(meta.get("built") or meta.get("Built")),
                "schema_version": meta.get("schema"),
            })
        except (json.JSONDecodeError, OSError):
            out["present"] = True
    elif v6_meta and v6_meta.is_file():
        try:
            meta = json.loads(v6_meta.read_text())
            source = meta.get("source") or ""
            built = _grype_built_from_source(source)
            # Schema version from the parent dir name (…/db/6/import.json → "6").
            schema = v6_meta.parent.name
            out.update({
                "present": True,
                "version": meta.get("client_version"),
                "built": built,
                "schema_version": schema,
            })
        except (json.JSONDecodeError, OSError):
            out["present"] = True
    else:
        out["present"] = False
    out["size_bytes"] = _dir_size(_GRYPE_DB_DIR)
    out["path"] = str(_GRYPE_DB_DIR)
    return out


def _grype_built_from_source(source: str) -> str | None:
    """Extract the DB build timestamp from a grype v6 ``source`` URL.

    Example URL fragment::

        …/vulnerability-db_v6.1.9_2026-07-24T00:34:54Z_1784876719.tar.zst?…

    Returns the ISO date string, or None if it can't be found.
    """
    if not source:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", source)
    return _parse_iso(m.group(1)) if m else None


def db_status() -> dict[str, Any]:
    """Return each scanner's DB info — version, date, size, install state."""
    return {"trivy": _trivy_db_status(), "grype": _grype_db_status()}


# ---------------------------------------------------------------------------
# DB update with streaming progress
# ---------------------------------------------------------------------------
# Match Trivy's progress line: "[2.5MiB/156MiB]". Capture groups are (done, total).
_TRIVY_PROGRESS = re.compile(r"\[\s*([\d.]+)\s*([KMGTP]?i?B)\s*/\s*([\d.]+)\s*([KMGTP]?i?B)\s*\]")

# Match Grype's progress line: "Vulnerability DB [30 MB / 208 MB]" (no 'i' in units).
_GRYPE_PROGRESS = re.compile(r"\[\s*([\d.]+)\s*([KMGTP]?B)\s*/\s*([\d.]+)\s*([KMGTP]?B)\s*\]")


def _to_bytes(value: float, unit: str) -> float:
    """Convert a value with a Trivy-style unit (e.g. ``MiB``, ``KB``) to bytes."""
    u = unit.upper()
    factors = {
        "B": 1, "KB": 1e3, "KIB": 1024,
        "MB": 1e6, "MIB": 1024**2,
        "GB": 1e9, "GIB": 1024**3,
        "TB": 1e12, "TIB": 1024**4,
        "PB": 1e15, "PIB": 1024**5,
    }
    return value * factors.get(u, 1)


def _grype_to_bytes(value: float, unit: str) -> float:
    """Convert a value with a Grype-style unit (e.g. ``MB``, ``GB``) to bytes."""
    u = unit.upper()
    factors = {
        "B": 1, "KB": 1e3,
        "MB": 1e6,
        "GB": 1e9,
        "TB": 1e12,
        "PB": 1e15,
    }
    return value * factors.get(u, 1)


async def _stream_subprocess(
    args: list[str],
    on_line: Callable[[str], None] | None = None,
    timeout: float = 600.0,
    env: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Run a subprocess; optionally feed each stdout line to ``on_line``.

    Returns ``(returncode, all_lines)`` so the caller can post-process. The
    ``on_line`` callback is synchronous (called inline as lines arrive); use
    it only for cheap parsing, not for awaiting async work. ``env`` (when
    given) is merged over the current process env — used for proxy vars.
    """
    full_env = None
    if env is not None:
        full_env = {**os.environ, **env}
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # merge for easier parsing
        env=full_env,
    )
    assert proc.stdout is not None
    lines: list[str] = []
    try:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\r\n")
            lines.append(line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001 - never let parsing crash the run
                    pass
        rc = await asyncio.wait_for(proc.wait(), timeout=30)
        return rc, lines
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"subprocess timed out: {' '.join(args)}")


def _proxy_env(proxy: str) -> dict[str, str]:
    """Build proxy env vars for scanner subprocesses (mirrors the bash script)."""
    if not proxy:
        return {}
    return {
        "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
        "http_proxy": proxy, "https_proxy": proxy,
    }


async def _oras_pull_with_progress(
    oras: str,
    image: str,
    out_dir: str,
    *,
    expected_mb: int,
    emit: Callable[[int, str], Awaitable[None]],
    env: dict[str, str] | None,
    progress_range: tuple[int, int],
    label: str,
) -> bool:
    """Run ``oras pull`` while polling the output dir for live byte progress.

    Returns True on success (exit 0), False otherwise. Emits messages like
    ``"trivy-db: 12.3 / ~50 MB"`` every ~2 seconds so the UI shows real
    download progress instead of being stuck.
    """
    p_lo, p_hi = progress_range
    span = p_hi - p_lo
    current_file = {"name": ""}

    def on_line(line: str) -> None:
        # oras prints "Downloading <hash> <filename>" lines
        if line.startswith("Downloading ") or line.startswith("Processing "):
            parts = line.split(None, 2)
            if len(parts) >= 3:
                current_file["name"] = parts[2]

    # Start oras as a background subprocess
    full_env = {**os.environ, **(env or {})}
    proc = await asyncio.create_subprocess_exec(
        oras, "pull", "--no-tty", image, "--output", out_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=full_env,
    )

    # Reader task: consume lines so the pipe doesn't block
    async def reader() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            on_line(line.decode(errors="replace").rstrip())

    reader_task = asyncio.create_task(reader())

    # Progress poller: check dir size every 2s, compute speed + ETA.
    import time as _time
    prev_bytes = 0
    prev_time = _time.monotonic()

    while proc.returncode is None:
        downloaded = _dir_size(Path(out_dir))
        dl_mb = downloaded / 1e6
        now = _time.monotonic()
        elapsed = now - prev_time
        # Speed in MB/s (since last poll).
        speed_mbps = ((downloaded - prev_bytes) / 1e6 / elapsed) if elapsed > 0 else 0
        prev_bytes = downloaded
        prev_time = now

        pct = p_lo + min(span, int(dl_mb / expected_mb * span))
        remaining_mb = max(0, expected_mb - dl_mb)
        eta_str = ""
        if speed_mbps > 0.1:
            eta_secs = remaining_mb / speed_mbps
            if eta_secs < 60:
                eta_str = f", {eta_secs:.0f}s left"
            elif eta_secs < 3600:
                eta_str = f", {eta_secs/60:.0f}m left"
            else:
                eta_str = f", {eta_secs/3600:.1f}h left"

        speed_str = f"{speed_mbps:.1f} MB/s" if speed_mbps > 0.1 else "…"
        file_str = f" ({current_file['name']})" if current_file["name"] else ""
        await emit(pct, f"{label}: {dl_mb:.1f} / ~{expected_mb} MB @ {speed_str}{eta_str}{file_str}")

        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=2.0)
        except asyncio.TimeoutError:
            continue  # still running, poll again

    # Process finished — drain reader
    await reader_task
    rc = proc.returncode
    final_mb = _dir_size(Path(out_dir)) / 1e6
    await emit(p_hi, f"{label}: {final_mb:.1f} MB downloaded" + ("" if rc == 0 else f" (exit {rc})"))
    return rc == 0


async def update_scanner_dbs(
    scanners: list[str],
    on_progress: ProgressCallback | None = None,
    proxy: str = "",
    force: bool = False,
) -> dict:
    """Refresh vulnerability databases for the requested scanners.

    Streams progress through ``on_progress(percent, message)``.

    **Date-checking**: before downloading, checks the local DB's
    ``NextUpdate`` (Trivy) or ``built`` (Grype) timestamp. If the DB is
    still current (not past its update window), the download is skipped to
    save bandwidth. Pass ``force=True`` to bypass the check.

    Strategy:
      * **Trivy DB** + **Java DB**: ``oras pull`` from OCI registry → extract.
      * **Grype**: ``grype db update``.
    """
    import tempfile

    async def emit(p: int, m: str) -> None:
        if on_progress is not None:
            await on_progress(p, m)

    results: dict[str, Any] = {}
    enabled = [s.lower() for s in scanners]
    penv = _proxy_env(proxy)

    # --- Date-check: skip download if the DB already covers "today" (UTC) ---
    # Rationale: Trivy/Grype publish at most one DB per day. If the local DB is
    # already current for the current UTC calendar day there is nothing newer to
    # fetch, so we skip and report the DB's release date instead of downloading.
    if not force:
        status = db_status()
        today_utc = datetime.now(timezone.utc).date()

        if "trivy" in enabled:
            trivy_info = status.get("trivy", {})
            # Trivy ships a NextUpdate timestamp — the authoritative "is it stale"
            # signal. If NextUpdate is still in the future, today's DB is current.
            if trivy_info.get("present") and trivy_info.get("next_update"):
                try:
                    next_upd = datetime.fromisoformat(trivy_info["next_update"].replace("Z", "+00:00"))
                    released = (trivy_info.get("created_at") or trivy_info.get("downloaded_at") or "")[:10]
                    if next_upd > datetime.now(timezone.utc):
                        results["trivy"] = {
                            "ok": True, "skipped": True, "downloaded": False,
                            "released": released or None,
                            "reason": f"DB released {released or '?'}, current until {trivy_info['next_update'][:10]}",
                        }
                        await emit(50, f"trivy: DB current (released {released or '?'}, next update {trivy_info['next_update'][:10]}) — skipping")
                        enabled = [s for s in enabled if s != "trivy"]
                except (ValueError, TypeError):
                    pass  # can't parse date, proceed with download

        if "grype" in enabled:
            grype_info = status.get("grype", {})
            # Grype exposes a "built" date. Skip if it was built on today's UTC
            # calendar day (there is no newer daily build to fetch yet).
            if grype_info.get("present") and grype_info.get("built"):
                try:
                    built = datetime.fromisoformat(grype_info["built"].replace("Z", "+00:00"))
                    built_day = built.astimezone(timezone.utc).date()
                    if built_day >= today_utc:
                        results["grype"] = {
                            "ok": True, "skipped": True, "downloaded": False,
                            "released": grype_info["built"][:10],
                            "reason": f"DB built {grype_info['built'][:10]} (today, UTC)",
                        }
                        await emit(98, f"grype: DB current (built {grype_info['built'][:10]}, today UTC) — skipping")
                        enabled = [s for s in enabled if s != "grype"]
                except (ValueError, TypeError):
                    pass

    if not enabled:
        await emit(100, "all DBs current, nothing to download")
        return results

    # ------------------------------------------------------------------
    # Trivy: oras pull + extract (the reliable path)
    # ------------------------------------------------------------------
    if "trivy" in enabled:
        oras = _which("oras")
        trivy = _which("trivy")
        if oras is None:
            results["trivy"] = {"ok": False, "error": "oras binary not installed (needed to fetch Trivy DB)"}
        elif trivy is None:
            results["trivy"] = {"ok": False, "error": "trivy binary not installed"}
        else:
            tmp = tempfile.mkdtemp(prefix="trivy-sync-")
            try:
                # --- Pull trivy-db:2 (main vulnerability DB, ~50MB) ---
                await emit(2, "trivy-db: connecting to OCI registry...")
                # Expected sizes (approx) for progress estimation:
                #   trivy-db:2 → db.tar.gz ~50MB
                #   trivy-java-db:1 → javadb.tar.gz ~30MB
                db_ok = await _oras_pull_with_progress(
                    oras, "registry-1.docker.io/aquasec/trivy-db:2", tmp,
                    expected_mb=50, emit=emit, env=penv,
                    progress_range=(2, 35), label="trivy-db",
                )
                if not db_ok:
                    raise RuntimeError("oras pull trivy-db failed")
                await emit(35, "trivy-db: downloaded")

                # --- Pull trivy-java-db:1 (Java DB, ~30MB) ---
                java_ok = await _oras_pull_with_progress(
                    oras, "ghcr.io/aquasecurity/trivy-java-db:1", tmp,
                    expected_mb=30, emit=emit, env=penv,
                    progress_range=(35, 48), label="trivy-java-db",
                )
                if not java_ok:
                    logger.warning("trivy-java-db pull failed — continuing without it")
                await emit(48, "trivy: extracting DB")

                # Extract archives into Trivy's cache layout.
                db_dir = _TRIVY_CACHE_ROOT / "db"
                java_dir = _TRIVY_CACHE_ROOT / "java-db"
                db_dir.mkdir(parents=True, exist_ok=True)
                java_dir.mkdir(parents=True, exist_ok=True)

                import tarfile
                db_tar = Path(tmp) / "db.tar.gz"
                java_tar = Path(tmp) / "javadb.tar.gz"
                if db_tar.exists():
                    with tarfile.open(db_tar) as tf:
                        tf.extractall(db_dir)
                if java_tar.exists():
                    with tarfile.open(java_tar) as tf:
                        tf.extractall(java_dir)

                shutil.rmtree(tmp, ignore_errors=True)
                _prune_trivy_old()
                await emit(50, "trivy: DB updated")
                results["trivy"] = {"ok": True}
            except Exception as exc:  # noqa: BLE001
                shutil.rmtree(tmp, ignore_errors=True)
                results["trivy"] = {"ok": False, "error": str(exc)}
                await emit(50, f"trivy: DB update failed — {exc}")

    # ------------------------------------------------------------------
    # Grype: db update (with proxy)
    # ------------------------------------------------------------------
    if "grype" in enabled:
        path = _which("grype")
        if path is None:
            results["grype"] = {"ok": False, "error": "grype not installed"}
        else:
            await emit(52, "grype: downloading DB (grype db update)")

            # Use a polling approach similar to _oras_pull_with_progress for live progress
            full_env = {**os.environ, **(penv or {})}
            proc = await asyncio.create_subprocess_exec(
                path, "db", "update", "-v",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=full_env,
            )

            assert proc.stdout is not None

            # Track progress state for live MB/s + ETA like Trivy
            grype_progress_state = {
                "total_bytes": None,
                "prev_bytes": 0,
                "prev_time": time.monotonic(),
            }

            async def reader_and_emit() -> list[str]:
                """Read lines, parse progress, emit updates, return all lines."""
                lines: list[str] = []
                while True:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=1200)
                    if not raw:
                        break
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    lines.append(line)

                    # Parse grype's progress line: "Vulnerability DB [30 MB / 208 MB]"
                    m = _GRYPE_PROGRESS.search(line)
                    if m:
                        done_val = float(m.group(1))
                        done_unit = m.group(2)
                        total_val = float(m.group(3))
                        total_unit = m.group(4)

                        done_bytes = _grype_to_bytes(done_val, done_unit)
                        total_bytes = _grype_to_bytes(total_val, total_unit)

                        # Initialize total on first progress line
                        if grype_progress_state["total_bytes"] is None:
                            grype_progress_state["total_bytes"] = total_bytes

                        now = time.monotonic()
                        elapsed = now - grype_progress_state["prev_time"]
                        speed_bps = (done_bytes - grype_progress_state["prev_bytes"]) / elapsed if elapsed > 0 else 0
                        grype_progress_state["prev_bytes"] = done_bytes
                        grype_progress_state["prev_time"] = now

                        done_mb = done_bytes / 1e6
                        total_mb = total_bytes / 1e6
                        speed_mbps = speed_bps / 1e6

                        # Compute ETA
                        eta_str = ""
                        if speed_mbps > 0.1 and total_bytes > done_bytes:
                            remaining_mb = (total_bytes - done_bytes) / 1e6
                            eta_secs = remaining_mb / speed_mbps
                            if eta_secs < 60:
                                eta_str = f", {eta_secs:.0f}s left"
                            elif eta_secs < 3600:
                                eta_str = f", {eta_secs/60:.0f}m left"
                            else:
                                eta_str = f", {eta_secs/3600:.1f}h left"

                        speed_str = f"{speed_mbps:.1f} MB/s" if speed_mbps > 0.1 else "…"
                        # Map to 52-96% range based on download progress
                        pct = 52 + int(min(44, (done_bytes / total_bytes * 44)) if total_bytes else 0)
                        await emit(pct, f"grype-db: {done_mb:.1f} / ~{total_mb:.0f} MB @ {speed_str}{eta_str}")

                return lines

            try:
                reader_task = asyncio.create_task(reader_and_emit())
                rc = await asyncio.wait_for(proc.wait(), timeout=1200)
                lines = await reader_task

                if rc == 0:
                    _prune_grype_old()
                    await emit(100, "grype: DB downloaded")
                    results["grype"] = {"ok": True, "rc": 0}
                else:
                    # The update FAILED (e.g. blocked network). Do NOT fake success.
                    # Only report ok if a usable DB is already present on disk —
                    # and label it honestly as "existing", not "downloaded".
                    tail = " | ".join(l for l in lines[-4:] if l.strip())[:400]
                    status = _grype_db_status()
                    if status.get("present"):
                        built = status.get("built") or "unknown date"
                        await emit(
                            100,
                            f"grype: download FAILED (exit {rc}) — using existing DB (built {str(built)[:10]})",
                        )
                        results["grype"] = {
                            "ok": False,
                            "downloaded": False,
                            "used_existing": True,
                            "rc": rc,
                            "error": f"grype db update failed (exit {rc}); existing DB retained. {tail}",
                        }
                    else:
                        await emit(100, f"grype: download FAILED (exit {rc}) — no DB on disk")
                        results["grype"] = {
                            "ok": False,
                            "downloaded": False,
                            "used_existing": False,
                            "rc": rc,
                            "error": f"grype db update failed (exit {rc}) and no DB present. "
                                     f"On a restricted network use offline import. {tail}",
                        }
            except Exception as exc:  # noqa: BLE001
                await emit(100, f"grype: DB update failed — {exc}")
                results["grype"] = {"ok": False, "error": str(exc)}

    if not results:
        await emit(100, "no scanners enabled")
    return results


def _prune_trivy_old() -> None:
    """Delete the previous Trivy DB version, keeping only the current one.

    Trivy's cache layout: ``db/`` holds ``metadata.json``, ``trivy.db`` (the
    current DB) and sometimes ``trivy.db.bak`` / older versions. Keep
    ``metadata.json`` + ``trivy.db``; remove everything else.
    """
    if not _TRIVY_DB_DIR.exists():
        return
    for entry in _TRIVY_DB_DIR.iterdir():
        if entry.name in ("metadata.json", "trivy.db"):
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not prune trivy cache entry %s: %s", entry, exc)


def _prune_grype_old() -> None:
    """Grype keeps only the active schema version, so nothing extra to do."""
    return


# ---------------------------------------------------------------------------
# Offline / air-gapped import (no network — reads files from _OFFLINE_DB_DIR)
# ---------------------------------------------------------------------------
def offline_status() -> dict[str, Any]:
    """Report which archives are present in the offline import directory.

    Lets the UI tell the operator exactly what files to drop in and whether
    they've been detected yet. Recognised filenames (case-insensitive):
      * Trivy DB:      ``db.tar.gz``  or ``trivy-db.tar.gz``
      * Trivy Java DB: ``javadb.tar.gz`` or ``trivy-java-db.tar.gz`` (optional)
      * Grype DB:      ``grype-db.tar.gz`` / ``grype-db.tar.zst`` / ``vulnerability-*.tar.*``
    """
    d = _OFFLINE_DB_DIR
    out: dict[str, Any] = {"dir": str(d), "exists": d.exists(), "files": []}
    if not d.exists():
        return out
    found = []
    for p in sorted(d.iterdir()):
        if p.is_file():
            found.append({"name": p.name, "size_bytes": p.stat().st_size})
    out["files"] = found
    names = {f["name"].lower() for f in found}
    out["trivy_db"] = any(n in names for n in ("db.tar.gz", "trivy-db.tar.gz"))
    out["grype_db"] = any(
        n.startswith(("grype-db", "vulnerability")) and n.endswith((".tar.gz", ".tar.zst", ".tar"))
        for n in names
    )
    return out


def _find_offline(*patterns: str) -> Path | None:
    """Return the first file in the offline dir whose lowercase name matches."""
    if not _OFFLINE_DB_DIR.exists():
        return None
    for p in sorted(_OFFLINE_DB_DIR.iterdir()):
        if p.is_file() and p.name.lower() in patterns:
            return p
    return None


def _find_offline_prefix(prefixes: tuple[str, ...], suffixes: tuple[str, ...]) -> Path | None:
    if not _OFFLINE_DB_DIR.exists():
        return None
    for p in sorted(_OFFLINE_DB_DIR.iterdir()):
        n = p.name.lower()
        if p.is_file() and n.startswith(prefixes) and n.endswith(suffixes):
            return p
    return None


async def import_offline_dbs(
    scanners: list[str],
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Install scanner DBs from pre-downloaded archives — no network access.

    Trivy: extracts ``db.tar.gz`` (and optional ``javadb.tar.gz``) straight
    into the Trivy cache layout, exactly like the online path does after the
    ``oras pull``.

    Grype: shells out to ``grype db import <archive>`` which is the officially
    supported offline path (it validates the checksum + schema and lays the DB
    into grype's cache). Works with the ``.tar.gz``/``.tar.zst`` listing that
    ``grype db update`` would normally fetch.
    """
    async def emit(p: int, m: str) -> None:
        if on_progress is not None:
            await on_progress(p, m)

    results: dict[str, Any] = {}
    enabled = [s.lower() for s in scanners]

    if not _OFFLINE_DB_DIR.exists():
        await emit(100, f"offline dir {_OFFLINE_DB_DIR} not found — create it and drop DB archives in")
        return {s: {"ok": False, "error": f"offline dir {_OFFLINE_DB_DIR} does not exist"} for s in enabled}

    # --- Trivy: extract archives directly into the cache ---
    if "trivy" in enabled:
        await emit(5, "trivy: looking for offline DB archive")
        db_tar = _find_offline("db.tar.gz", "trivy-db.tar.gz")
        java_tar = _find_offline("javadb.tar.gz", "trivy-java-db.tar.gz")
        if db_tar is None:
            results["trivy"] = {"ok": False, "error": "no db.tar.gz / trivy-db.tar.gz in offline dir"}
            await emit(50, "trivy: no offline archive found")
        else:
            try:
                import tarfile
                db_dir = _TRIVY_CACHE_ROOT / "db"
                db_dir.mkdir(parents=True, exist_ok=True)
                await emit(15, f"trivy: extracting {db_tar.name}")
                with tarfile.open(db_tar) as tf:
                    tf.extractall(db_dir)
                if java_tar is not None:
                    java_dir = _TRIVY_CACHE_ROOT / "java-db"
                    java_dir.mkdir(parents=True, exist_ok=True)
                    await emit(30, f"trivy: extracting {java_tar.name}")
                    with tarfile.open(java_tar) as tf:
                        tf.extractall(java_dir)
                _prune_trivy_old()
                await emit(50, "trivy: offline DB imported")
                results["trivy"] = {"ok": True, "source": db_tar.name}
            except Exception as exc:  # noqa: BLE001
                results["trivy"] = {"ok": False, "error": str(exc)}
                await emit(50, f"trivy: import failed — {exc}")

    # --- Grype: grype db import <archive> ---
    if "grype" in enabled:
        grype = _which("grype")
        archive = _find_offline("grype-db.tar.gz", "grype-db.tar.zst") or _find_offline_prefix(
            ("grype-db", "vulnerability"), (".tar.gz", ".tar.zst", ".tar")
        )
        if grype is None:
            results["grype"] = {"ok": False, "error": "grype binary not installed"}
        elif archive is None:
            results["grype"] = {"ok": False, "error": "no grype-db.tar.* / vulnerability-*.tar.* in offline dir"}
            await emit(98, "grype: no offline archive found")
        else:
            await emit(60, f"grype: importing {archive.name}")
            try:
                rc, lines = await _stream_subprocess(
                    [grype, "db", "import", str(archive)], on_line=None, timeout=600,
                )
                if rc == 0:
                    await emit(100, "grype: offline DB imported")
                    results["grype"] = {"ok": True, "source": archive.name}
                else:
                    tail = " | ".join(lines[-3:])[:300]
                    results["grype"] = {"ok": False, "error": f"grype db import exit {rc}: {tail}"}
                    await emit(100, f"grype: import failed (exit {rc})")
            except Exception as exc:  # noqa: BLE001
                results["grype"] = {"ok": False, "error": str(exc)}
                await emit(100, f"grype: import failed — {exc}")

    if not results:
        await emit(100, "no scanners enabled")
    return results


# ---------------------------------------------------------------------------
# Scanning (Trivy + Grype)
# ---------------------------------------------------------------------------
def _trivy_parse(raw: dict) -> list[dict]:
    out = []
    for result in raw.get("Results", []) or []:
        for v in result.get("Vulnerabilities", []) or []:
            out.append({
                "cve": v.get("VulnerabilityID") or v.get("CVE", "") or "UNKNOWN",
                "severity": (v.get("Severity") or "UNKNOWN").upper(),
                "package": v.get("PkgName") or "",
                "installed_version": v.get("InstalledVersion") or "",
                "fixed_version": v.get("FixedVersion") or "",
                "title": v.get("Title") or v.get("Description", "")[:200] or "",
                "cvss": _trivy_cvss(v),
            })
    return out


def _trivy_cvss(v: dict) -> float:
    cvss = v.get("CVSS") or {}
    for vendor in cvss.values():
        if isinstance(vendor, dict) and "V3Score" in vendor:
            return float(vendor["V3Score"])
        if isinstance(vendor, dict) and "V2Score" in vendor:
            return float(vendor["V2Score"])
    return 0.0


async def run_trivy(image_ref_with_host: str, nexus: NexusClient) -> dict:
    bin_path = _which("trivy")
    if bin_path is None:
        return {"error": "trivy binary not installed", "vulnerabilities": []}
    verify_ssl = getattr(nexus.settings, "NEXUS_VERIFY_SSL", True)
    args = [
        bin_path, "image", "--quiet", "--format", "json",
        "--username", nexus.settings.NEXUS_USERNAME,
        "--password", nexus.settings.NEXUS_PASSWORD,
        "--timeout", "5m",
    ]
    # If the Nexus registry uses self-signed certs / HTTP, allow insecure.
    if not verify_ssl:
        args.append("--insecure")
    args.append(image_ref_with_host)

    code, stdout, stderr = await _run(args, timeout=420)
    if code != 0:
        return {"error": f"trivy exit {code}: {stderr[:500]}", "vulnerabilities": []}
    try:
        raw = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"error": f"trivy JSON parse error: {exc}", "vulnerabilities": []}
    return {"vulnerabilities": _trivy_parse(raw), "raw": raw}


def _grype_parse(raw: dict) -> list[dict]:
    out = []
    for m in raw.get("matches", []) or []:
        v = m.get("vulnerability", {}) or {}
        artifact = m.get("artifact", {}) or {}
        rel = m.get("relatedVulnerabilities", []) or []
        cvss = 0.0
        for rv in rel:
            for c in rv.get("cvss", []) or []:
                cvss = max(cvss, float(c.get("score") or 0))
        fix_versions = ((v.get("fix", {}) or {}).get("versions") or []) or []
        out.append({
            "cve": v.get("id") or "UNKNOWN",
            "severity": (v.get("severity") or "UNKNOWN").upper(),
            "package": artifact.get("name") or "",
            "installed_version": artifact.get("version") or "",
            "fixed_version": fix_versions[0] if fix_versions else "",
            "title": v.get("description", "")[:200] or "",
            "cvss": cvss,
        })
    return out


async def run_grype(image_ref_with_host: str, nexus: NexusClient) -> dict:
    bin_path = _which("grype")
    if bin_path is None:
        return {"error": "grype binary not installed", "vulnerabilities": []}
    user = nexus.settings.NEXUS_USERNAME
    password = nexus.settings.NEXUS_PASSWORD
    verify_ssl = getattr(nexus.settings, "NEXUS_VERIFY_SSL", True)
    env = {**os.environ, "GRYPE_REGISTRY_AUTH_USERNAME": user, "GRYPE_REGISTRY_AUTH_PASSWORD": password}
    if not verify_ssl:
        env["GRYPE_REGISTRY_INSECURE_SKIP_TLS_VERIFY"] = "true"
    args = [bin_path, image_ref_with_host, "-o", "json"]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=420)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": "grype timed out", "vulnerabilities": []}
    if proc.returncode != 0:
        return {"error": f"grype exit {proc.returncode}: {stderr.decode()[:300]}", "vulnerabilities": []}
    try:
        raw = json.loads(stdout.decode() or "{}")
    except json.JSONDecodeError as exc:
        return {"error": f"grype JSON parse error: {exc}", "vulnerabilities": []}
    return {"vulnerabilities": _grype_parse(raw), "raw": raw}


async def scan_image(
    nexus: NexusClient,
    session: AsyncSession,
    repo: str,
    image_ref: str,
    scanners: list[str],
) -> list[ScanReport]:
    _registry, full_ref = _registry_ref(nexus, repo, image_ref)
    reports: list[ScanReport] = []
    for scanner in scanners:
        scanner = scanner.lower()
        report = ScanReport(target_repo=repo, image=image_ref, scanner=scanner, status="running")
        session.add(report)
        await session.flush()
        try:
            if scanner == "trivy":
                result = await run_trivy(full_ref, nexus)
            elif scanner == "grype":
                result = await run_grype(full_ref, nexus)
            else:
                result = {"error": f"unknown scanner {scanner}"}
            vulns = result.get("vulnerabilities", [])
            counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
            vuln_rows = []
            for v in vulns:
                sev = (v.get("severity") or "UNKNOWN").upper()
                if sev not in counts:
                    sev = "UNKNOWN"
                counts[sev] += 1
                vuln_rows.append(Vulnerability(
                    report_id=report.id, repo=repo, scanner=scanner,
                    cve=v.get("cve", "UNKNOWN"), severity=sev,
                    package=v.get("package", ""), installed_version=v.get("installed_version", ""),
                    fixed_version=v.get("fixed_version", ""), title=v.get("title", ""),
                    cvss=float(v.get("cvss") or 0.0),
                ))
            session.add_all(vuln_rows)
            report.status = "success" if "error" not in result else "failed"
            report.critical = counts["CRITICAL"]
            report.high = counts["HIGH"]
            report.medium = counts["MEDIUM"]
            report.low = counts["LOW"]
            report.unknown = counts["UNKNOWN"]
            report.raw_json = json.dumps({"error": result.get("error")} if "error" in result else {"count": len(vulns)}, default=str)
        except Exception as exc:  # noqa: BLE001
            logger.exception("scan failed for %s via %s", image_ref, scanner)
            report.status = "failed"
            report.raw_json = json.dumps({"error": str(exc)})
        finally:
            report.finished_at = datetime.now(timezone.utc)
        reports.append(report)
    await session.commit()
    return reports
