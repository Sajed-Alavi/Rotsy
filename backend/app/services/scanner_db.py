"""Vulnerability-database lifecycle for Trivy and Grype.

This module is the **single** owner of scanner database state. Before the
refactor the same job was spread across four places that could disagree with
each other (a shell script run from the entrypoint, an unconditional refresh
enqueued on every boot, an ``oras``-based downloader and an offline importer);
scans then failed for reasons none of them reported. Everything now lives here
and every caller goes through one of these functions:

  * :func:`status`     — what is on disk (version, build date, size, path).
  * :func:`readiness`  — is each scanner *able to scan right now*, and if not why.
  * :func:`update`     — refresh from the network (with live progress).
  * :func:`import_offline` — install from pre-downloaded archives (no network).

**Why the scanners must never update their own DB mid-scan.** Both tools will,
by default, try to refresh their database when they are asked to scan. On a
restricted network that download fails and the *scan* fails with it — which is
exactly how a working scanner ends up reporting ``FAILED``. Scans therefore run
with auto-update disabled (see :mod:`app.services.scanners`) and the database is
managed only through this module. :func:`readiness` is what turns "no database"
into an actionable message instead of an opaque scanner exit code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], Awaitable[None]]

# Cache locations. Trivy lays its database out as <cache>/db/metadata.json;
# Grype uses <cache>/db/<schema>/. The Dockerfile sets TRIVY_CACHE_DIR and
# GRYPE_CACHE_DIR and pre-creates them writable for the non-root app user.
TRIVY_CACHE_ROOT = Path(os.environ.get("TRIVY_CACHE_DIR") or (Path.home() / ".cache" / "trivy"))
TRIVY_DB_DIR = TRIVY_CACHE_ROOT / "db"
TRIVY_JAVA_DB_DIR = TRIVY_CACHE_ROOT / "java-db"
GRYPE_CACHE_ROOT = Path(os.environ.get("GRYPE_CACHE_DIR") or (Path.home() / ".cache" / "grype"))

# Offline / air-gapped import directory. Where Docker Hub, ghcr.io and github.com
# are blocked the databases cannot be pulled at runtime: an operator downloads
# them on a connected machine, drops the archives into this host folder (mounted
# by docker-compose) and triggers an import, which extracts them straight into
# the scanner caches. See :func:`import_offline`.
OFFLINE_DB_DIR = Path(os.environ.get("SCANNER_OFFLINE_DIR") or "/app/offline-db")

# A Grype database older than this is reported as stale by :func:`readiness`.
# Grype's own default is to *refuse* a database older than 5 days; we scan with
# that check disabled (a slightly stale database beats no scan at all) but the
# operator still needs to see that it is aging.
STALE_AFTER = timedelta(days=5)

# Approximate archive sizes, used only to render download progress.
_TRIVY_DB_MB = 50
_TRIVY_JAVA_DB_MB = 125

_TRIVY_DB_IMAGE = "registry-1.docker.io/aquasec/trivy-db:2"
_TRIVY_JAVA_DB_IMAGE = "ghcr.io/aquasecurity/trivy-java-db:1"

# Grype's progress line: "Vulnerability DB [30 MB / 208 MB]".
_GRYPE_PROGRESS = re.compile(r"\[\s*([\d.]+)\s*([KMGTP]?B)\s*/\s*([\d.]+)\s*([KMGTP]?B)\s*\]")
_SI_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def which(binary: str) -> str | None:
    """Absolute path of a scanner binary, or None when it is not installed."""
    return shutil.which(binary)


# ---------------------------------------------------------------------------
# On-disk status
# ---------------------------------------------------------------------------
def _parse_iso(value: Any) -> str | None:
    """Normalise an RFC3339 timestamp, rejecting Go's zero time."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return str(value) or None
    # Trivy writes "0001-01-01T00:00:00Z" for unset fields such as DownloadedAt.
    return None if dt.year <= 1 else dt.isoformat()


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


def _grype_built_from_source(source: str) -> str | None:
    """Pull the build timestamp out of a Grype v6 ``source`` URL.

    e.g. ``…/vulnerability-db_v6.1.9_2026-07-24T00:34:54Z_1784876719.tar.zst``
    """
    if not source:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", source)
    return _parse_iso(match.group(1)) if match else None


def _trivy_status() -> dict[str, Any]:
    out: dict[str, Any] = {"installed": which("trivy") is not None, "present": False}
    meta_file = TRIVY_DB_DIR / "metadata.json"
    if not meta_file.is_file():
        out["path"] = str(TRIVY_DB_DIR)
        return out
    # trivy.db is the database itself; metadata.json alone is not usable.
    out["present"] = (TRIVY_DB_DIR / "trivy.db").is_file()
    try:
        meta = json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        meta = {}
    # The database's "as of" date: modern Trivy writes UpdatedAt (build time),
    # older layouts used CreatedAt. DownloadedAt is Go's zero time when the
    # database was extracted from an archive rather than pulled by Trivy.
    built = _parse_iso(meta.get("UpdatedAt") or meta.get("CreatedAt"))
    out.update({
        "version": meta.get("Version"),
        "built": built,
        "next_update": _parse_iso(meta.get("NextUpdate")),
        "downloaded_at": _parse_iso(meta.get("DownloadedAt")),
        "java_db_present": (TRIVY_JAVA_DB_DIR / "metadata.json").is_file(),
        "size_bytes": _dir_size(TRIVY_CACHE_ROOT),
        "path": str(TRIVY_DB_DIR),
    })
    return out


def _grype_status() -> dict[str, Any]:
    out: dict[str, Any] = {"installed": which("grype") is not None, "present": False}
    out["path"] = str(GRYPE_CACHE_ROOT)
    if not GRYPE_CACHE_ROOT.exists():
        return out

    # Grype's layout depends on the database schema version:
    #   schema <= 5: <cache>/<n>/metadata.json      (keys "built" / "schema")
    #   schema >= 6: <cache>/db/6/import.json + vulnerability.db, where the
    #                build date is embedded in the "source" URL.
    db_file = next(GRYPE_CACHE_ROOT.rglob("vulnerability.db"), None)
    out["present"] = db_file is not None
    v5_meta = next(GRYPE_CACHE_ROOT.rglob("metadata.json"), None)
    v6_meta = next(GRYPE_CACHE_ROOT.rglob("import.json"), None)

    if v5_meta is not None and v5_meta.is_file():
        try:
            meta = json.loads(v5_meta.read_text())
            out.update({
                "version": meta.get("version") or meta.get("Version"),
                "built": _parse_iso(meta.get("built") or meta.get("Built")),
                "schema_version": meta.get("schema"),
            })
        except (json.JSONDecodeError, OSError):
            pass
    elif v6_meta is not None and v6_meta.is_file():
        try:
            meta = json.loads(v6_meta.read_text())
            out.update({
                "version": meta.get("client_version"),
                "built": _grype_built_from_source(meta.get("source") or ""),
                "schema_version": v6_meta.parent.name,
            })
        except (json.JSONDecodeError, OSError):
            pass
    out["size_bytes"] = _dir_size(GRYPE_CACHE_ROOT)
    return out


def status() -> dict[str, Any]:
    """Per-scanner database facts: version, build date, size, install state."""
    return {"trivy": _trivy_status(), "grype": _grype_status()}


@dataclass
class Readiness:
    """Whether a scanner can run a scan right now."""

    scanner: str
    ready: bool
    reason: str = ""
    stale: bool = False
    built: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "scanner": self.scanner, "ready": self.ready, "reason": self.reason,
            "stale": self.stale, "built": self.built,
        }


def readiness(scanners: list[str]) -> dict[str, Readiness]:
    """Check each scanner's preconditions and explain any that are unmet.

    Called before every scan so a missing database is reported as
    "no vulnerability database on disk — run a DB update or offline import"
    rather than as a bare non-zero exit code from the scanner.
    """
    snapshot = status()
    now = datetime.now(timezone.utc)
    out: dict[str, Readiness] = {}
    for name in scanners:
        name = name.lower()
        info = snapshot.get(name)
        if info is None:
            out[name] = Readiness(name, False, f"unknown scanner '{name}'")
            continue
        if not info.get("installed"):
            out[name] = Readiness(name, False, f"the {name} binary is not installed in this image")
            continue
        if not info.get("present"):
            out[name] = Readiness(
                name, False,
                f"no {name} vulnerability database on disk — run a database update "
                "(or an offline import on a restricted network) before scanning",
            )
            continue
        built = info.get("built")
        built_dt = _as_datetime(built)
        stale = built_dt is not None and (now - built_dt) > STALE_AFTER
        out[name] = Readiness(name, True, "", stale=stale, built=built)
    return out


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------
def proxy_env(proxy: str) -> dict[str, str]:
    """Proxy variables for database-download subprocesses."""
    if not proxy:
        return {}
    return {
        "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
        "http_proxy": proxy, "https_proxy": proxy,
    }


async def _run_streaming(
    args: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[int, list[str]]:
    """Run a command, streaming merged stdout/stderr through ``on_line``.

    Returns ``(returncode, lines)``. The line callback is awaited, so it can
    emit progress events.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # merged: these tools log to both
        env={**os.environ, **(env or {})},
    )
    assert proc.stdout is not None
    lines: list[str] = []

    async def pump() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            lines.append(line)
            if on_line is not None:
                try:
                    await on_line(line)
                except Exception:  # noqa: BLE001 - progress must never kill the run
                    logger.debug("progress callback failed", exc_info=True)

    pump_task = asyncio.create_task(pump())
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        pump_task.cancel()
        raise RuntimeError(f"timed out after {timeout:.0f}s: {args[0]} {' '.join(args[1:3])}")
    await pump_task
    return rc, lines


async def _oras_pull(
    oras: str,
    image: str,
    out_dir: str,
    *,
    expected_mb: int,
    emit: ProgressCallback,
    env: dict[str, str],
    progress_range: tuple[int, int],
    label: str,
    timeout: float = 1800.0,
) -> bool:
    """``oras pull`` with live byte progress polled from the output directory.

    ``oras`` does not report progress in a machine-readable way, so the output
    directory is sized every two seconds to derive speed and ETA.

    ``timeout`` bounds the whole pull: a stalled connection would otherwise hold
    the job open indefinitely, with the queue behind it.
    """
    low, high = progress_range
    span = high - low
    deadline = time.monotonic() + timeout
    proc = await asyncio.create_subprocess_exec(
        oras, "pull", "--no-tty", image, "--output", out_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **env},
    )
    tail: list[str] = []

    async def drain() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                return
            tail.append(raw.decode(errors="replace").rstrip())
            del tail[:-10]

    drain_task = asyncio.create_task(drain())
    prev_bytes, prev_time = 0, time.monotonic()
    while proc.returncode is None:
        if time.monotonic() > deadline:
            proc.kill()
            await proc.wait()
            drain_task.cancel()
            await emit(high, f"{label}: timed out after {timeout / 60:.0f} minutes")
            return False
        downloaded = _dir_size(Path(out_dir))
        now = time.monotonic()
        elapsed = now - prev_time
        speed_mbps = ((downloaded - prev_bytes) / 1e6 / elapsed) if elapsed > 0 else 0.0
        prev_bytes, prev_time = downloaded, now

        done_mb = downloaded / 1e6
        pct = low + min(span, int(done_mb / expected_mb * span)) if expected_mb else low
        await emit(pct, f"{label}: {done_mb:.1f} / ~{expected_mb} MB{_rate(speed_mbps, expected_mb - done_mb)}")
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=2.0)
        except asyncio.TimeoutError:
            continue

    await drain_task
    ok = proc.returncode == 0
    final_mb = _dir_size(Path(out_dir)) / 1e6
    if ok:
        await emit(high, f"{label}: {final_mb:.1f} MB downloaded")
    else:
        await emit(high, f"{label}: FAILED (exit {proc.returncode}) {' | '.join(tail[-2:])[:200]}")
    return ok


def _rate(speed_mbps: float, remaining_mb: float) -> str:
    """Render " @ 4.2 MB/s, 3m left" for a progress message."""
    if speed_mbps <= 0.1:
        return ""
    eta = max(0.0, remaining_mb) / speed_mbps
    if eta < 60:
        left = f"{eta:.0f}s"
    elif eta < 3600:
        left = f"{eta / 60:.0f}m"
    else:
        left = f"{eta / 3600:.1f}h"
    return f" @ {speed_mbps:.1f} MB/s, {left} left"


# ---------------------------------------------------------------------------
# Online update
# ---------------------------------------------------------------------------
def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        # filter="data" refuses absolute paths, "..", symlinks and device
        # files — these archives come off the network, so they get the same
        # scrutiny as any other untrusted input.
        tf.extractall(dest, filter="data")


def _prune_trivy() -> None:
    """Keep only the live Trivy database files, dropping superseded ones."""
    if not TRIVY_DB_DIR.exists():
        return
    for entry in TRIVY_DB_DIR.iterdir():
        if entry.name in ("metadata.json", "trivy.db"):
            continue
        try:
            shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not prune Trivy cache entry %s: %s", entry, exc)


def _is_current(scanner: str, info: dict[str, Any]) -> str | None:
    """Return a human reason when the on-disk database is already current.

    Trivy publishes ``NextUpdate``, which is authoritative. Grype publishes one
    build per day, so a database built today (UTC) is the newest there is.
    """
    if not info.get("present"):
        return None
    if scanner == "trivy":
        next_update = _as_datetime(info.get("next_update"))
        if next_update and next_update > datetime.now(timezone.utc):
            built = (info.get("built") or "?")[:10]
            return f"database built {built} is current until {info['next_update'][:10]}"
        return None
    built = _as_datetime(info.get("built"))
    if built and built.astimezone(timezone.utc).date() >= datetime.now(timezone.utc).date():
        return f"database built {info['built'][:10]} (today, UTC) is the newest published build"
    return None


async def update(
    scanners: list[str],
    on_progress: ProgressCallback | None = None,
    proxy: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Refresh vulnerability databases over the network.

    Skips a scanner whose database is already current unless ``force`` is set —
    both projects publish at most one build per day, so re-downloading hundreds
    of megabytes for the same content is pure waste.

    Trivy is fetched with ``oras`` (its databases are plain OCI artifacts) and
    falls back to Trivy's own downloader; Grype uses ``grype db update``.
    """
    async def emit(pct: int, msg: str) -> None:
        if on_progress is not None:
            await on_progress(pct, msg)

    enabled = [s.strip().lower() for s in scanners if s.strip()]
    results: dict[str, Any] = {}
    env = proxy_env(proxy)

    if not force:
        snapshot = status()
        for name in list(enabled):
            reason = _is_current(name, snapshot.get(name, {}))
            if reason:
                results[name] = {"ok": True, "skipped": True, "downloaded": False, "reason": reason}
                await emit(50, f"{name}: {reason} — skipping download")
                enabled.remove(name)

    if not enabled:
        await emit(100, "all databases current, nothing to download")
        return results

    if "trivy" in enabled:
        results["trivy"] = await _update_trivy(emit, env)
    if "grype" in enabled:
        results["grype"] = await _update_grype(emit, env)

    if not results:
        await emit(100, "no scanners enabled")
    return results


async def _update_trivy(emit: ProgressCallback, env: dict[str, str]) -> dict[str, Any]:
    trivy = which("trivy")
    if trivy is None:
        return {"ok": False, "error": "trivy binary not installed"}

    oras = which("oras")
    if oras is not None:
        tmp = tempfile.mkdtemp(prefix="trivy-db-")
        try:
            await emit(2, "trivy-db: connecting to the OCI registry")
            if not await _oras_pull(oras, _TRIVY_DB_IMAGE, tmp, expected_mb=_TRIVY_DB_MB,
                                    emit=emit, env=env, progress_range=(2, 35), label="trivy-db"):
                raise RuntimeError(f"oras pull {_TRIVY_DB_IMAGE} failed")
            # The Java database is optional: without it Trivy still scans OS
            # packages and every non-Java language ecosystem.
            java_ok = await _oras_pull(oras, _TRIVY_JAVA_DB_IMAGE, tmp, expected_mb=_TRIVY_JAVA_DB_MB,
                                       emit=emit, env=env, progress_range=(35, 46), label="trivy-java-db")
            await emit(46, "trivy: extracting database")
            db_tar, java_tar = Path(tmp) / "db.tar.gz", Path(tmp) / "javadb.tar.gz"
            if not db_tar.exists():
                raise RuntimeError("oras pull succeeded but db.tar.gz is missing from the artifact")
            _extract(db_tar, TRIVY_DB_DIR)
            if java_ok and java_tar.exists():
                _extract(java_tar, TRIVY_JAVA_DB_DIR)
            _prune_trivy()
            await emit(50, "trivy: database updated")
            return {"ok": True, "downloaded": True, "java_db": java_ok, "via": "oras"}
        except Exception as exc:  # noqa: BLE001 - fall through to Trivy's own downloader
            logger.warning("Trivy database update via oras failed: %s", exc)
            await emit(20, f"trivy: oras path failed ({exc}); trying trivy's own downloader")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Fallback: let Trivy fetch its own database.
    await emit(25, "trivy: downloading database (trivy image --download-db-only)")
    try:
        rc, lines = await _run_streaming(
            [trivy, "image", "--download-db-only", "--cache-dir", str(TRIVY_CACHE_ROOT)],
            timeout=900, env=env,
        )
    except Exception as exc:  # noqa: BLE001
        await emit(50, f"trivy: database update failed — {exc}")
        return {"ok": False, "error": str(exc)}
    if rc == 0:
        _prune_trivy()
        await emit(50, "trivy: database updated")
        return {"ok": True, "downloaded": True, "via": "trivy"}
    tail = " | ".join(line for line in lines[-4:] if line.strip())[:400]
    await emit(50, f"trivy: database download FAILED (exit {rc})")
    return {
        "ok": False, "downloaded": False, "rc": rc,
        "error": f"trivy could not download its database (exit {rc}). "
                 f"On a restricted network use the offline import instead. {tail}",
    }


async def _update_grype(emit: ProgressCallback, env: dict[str, str]) -> dict[str, Any]:
    grype = which("grype")
    if grype is None:
        return {"ok": False, "error": "grype binary not installed"}

    await emit(52, "grype: downloading database (grype db update)")
    state = {"prev_bytes": 0.0, "prev_time": time.monotonic()}

    async def on_line(line: str) -> None:
        match = _GRYPE_PROGRESS.search(line)
        if not match:
            return
        done = float(match.group(1)) * _SI_UNITS.get(match.group(2).upper(), 1)
        total = float(match.group(3)) * _SI_UNITS.get(match.group(4).upper(), 1)
        now = time.monotonic()
        elapsed = now - state["prev_time"]
        speed_mbps = ((done - state["prev_bytes"]) / 1e6 / elapsed) if elapsed > 0 else 0.0
        state["prev_bytes"], state["prev_time"] = done, now
        pct = 52 + (int(min(44, done / total * 44)) if total else 0)
        await emit(pct, f"grype-db: {done / 1e6:.1f} / ~{total / 1e6:.0f} MB"
                        f"{_rate(speed_mbps, (total - done) / 1e6)}")

    try:
        rc, lines = await _run_streaming([grype, "db", "update", "-v"], timeout=1200, env=env, on_line=on_line)
    except Exception as exc:  # noqa: BLE001
        await emit(100, f"grype: database update failed — {exc}")
        return {"ok": False, "error": str(exc)}

    if rc == 0:
        await emit(100, "grype: database updated")
        return {"ok": True, "downloaded": True}

    # The download failed. Never report that as success: say so, and say
    # whether a usable (older) database is still on disk.
    tail = " | ".join(line for line in lines[-4:] if line.strip())[:400]
    existing = _grype_status()
    if existing.get("present"):
        built = str(existing.get("built") or "unknown date")[:10]
        await emit(100, f"grype: download FAILED (exit {rc}) — keeping existing database (built {built})")
        return {
            "ok": False, "downloaded": False, "used_existing": True, "rc": rc,
            "error": f"grype db update failed (exit {rc}); the existing database (built {built}) "
                     f"was retained and scans will keep using it. {tail}",
        }
    await emit(100, f"grype: download FAILED (exit {rc}) — no database on disk")
    return {
        "ok": False, "downloaded": False, "used_existing": False, "rc": rc,
        "error": f"grype db update failed (exit {rc}) and no database is present. "
                 f"On a restricted network use the offline import. {tail}",
    }


# ---------------------------------------------------------------------------
# Offline / air-gapped import
# ---------------------------------------------------------------------------
def offline_status() -> dict[str, Any]:
    """Report which archives are present in the offline import directory.

    Recognised filenames (case-insensitive):
      * Trivy database:      ``db.tar.gz`` or ``trivy-db.tar.gz``
      * Trivy Java database: ``javadb.tar.gz`` or ``trivy-java-db.tar.gz`` (optional)
      * Grype database:      ``grype-db.tar.*`` or ``vulnerability-*.tar.*``
    """
    out: dict[str, Any] = {"dir": str(OFFLINE_DB_DIR), "exists": OFFLINE_DB_DIR.exists(), "files": []}
    if not OFFLINE_DB_DIR.exists():
        return out
    files = [
        {"name": p.name, "size_bytes": p.stat().st_size}
        for p in sorted(OFFLINE_DB_DIR.iterdir()) if p.is_file()
    ]
    names = {f["name"].lower() for f in files}
    out["files"] = files
    out["trivy_db"] = any(n in names for n in ("db.tar.gz", "trivy-db.tar.gz"))
    out["grype_db"] = any(
        n.startswith(("grype-db", "vulnerability")) and n.endswith((".tar.gz", ".tar.zst", ".tar"))
        for n in names
    )
    return out


def _find_offline(*exact: str, prefixes: tuple[str, ...] = (), suffixes: tuple[str, ...] = ()) -> Path | None:
    """First offline archive matching an exact name, else a prefix/suffix pair."""
    if not OFFLINE_DB_DIR.exists():
        return None
    candidates = [p for p in sorted(OFFLINE_DB_DIR.iterdir()) if p.is_file()]
    for path in candidates:
        if path.name.lower() in exact:
            return path
    if prefixes and suffixes:
        for path in candidates:
            name = path.name.lower()
            if name.startswith(prefixes) and name.endswith(suffixes):
                return path
    return None


async def import_offline(
    scanners: list[str],
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Install databases from pre-downloaded archives — no network access.

    Trivy archives are extracted straight into its cache layout (identical to
    what the online path does after the pull). Grype goes through
    ``grype db import``, its supported offline path, which validates the
    checksum and schema before installing.
    """
    async def emit(pct: int, msg: str) -> None:
        if on_progress is not None:
            await on_progress(pct, msg)

    enabled = [s.strip().lower() for s in scanners if s.strip()]
    if not OFFLINE_DB_DIR.exists():
        message = (f"the offline database directory {OFFLINE_DB_DIR} does not exist — "
                   "create ./offline-db on the host and drop the archives in")
        await emit(100, message)
        return {s: {"ok": False, "error": message} for s in enabled}

    results: dict[str, Any] = {}
    if "trivy" in enabled:
        results["trivy"] = await _import_trivy(emit)
    if "grype" in enabled:
        results["grype"] = await _import_grype(emit)
    if not results:
        await emit(100, "no scanners enabled")
    return results


async def _import_trivy(emit: ProgressCallback) -> dict[str, Any]:
    await emit(5, "trivy: looking for an offline database archive")
    db_tar = _find_offline("db.tar.gz", "trivy-db.tar.gz")
    if db_tar is None:
        await emit(50, "trivy: no offline archive found")
        return {"ok": False, "error": f"no db.tar.gz / trivy-db.tar.gz in {OFFLINE_DB_DIR}"}
    java_tar = _find_offline("javadb.tar.gz", "trivy-java-db.tar.gz")
    try:
        await emit(15, f"trivy: extracting {db_tar.name}")
        _extract(db_tar, TRIVY_DB_DIR)
        if java_tar is not None:
            await emit(30, f"trivy: extracting {java_tar.name}")
            _extract(java_tar, TRIVY_JAVA_DB_DIR)
        _prune_trivy()
        await emit(50, "trivy: offline database imported")
        return {"ok": True, "source": db_tar.name, "java_db": java_tar is not None}
    except Exception as exc:  # noqa: BLE001
        await emit(50, f"trivy: import failed — {exc}")
        return {"ok": False, "error": str(exc)}


async def _import_grype(emit: ProgressCallback) -> dict[str, Any]:
    grype = which("grype")
    if grype is None:
        return {"ok": False, "error": "grype binary not installed"}
    archive = _find_offline(
        "grype-db.tar.gz", "grype-db.tar.zst",
        prefixes=("grype-db", "vulnerability"), suffixes=(".tar.gz", ".tar.zst", ".tar"),
    )
    if archive is None:
        await emit(100, "grype: no offline archive found")
        return {"ok": False, "error": f"no grype-db.tar.* / vulnerability-*.tar.* in {OFFLINE_DB_DIR}"}
    await emit(60, f"grype: importing {archive.name}")
    try:
        rc, lines = await _run_streaming([grype, "db", "import", str(archive)], timeout=900)
    except Exception as exc:  # noqa: BLE001
        await emit(100, f"grype: import failed — {exc}")
        return {"ok": False, "error": str(exc)}
    if rc == 0:
        await emit(100, "grype: offline database imported")
        return {"ok": True, "source": archive.name}
    tail = " | ".join(line for line in lines[-3:] if line.strip())[:300]
    await emit(100, f"grype: import failed (exit {rc})")
    return {"ok": False, "error": f"grype db import exited {rc}: {tail}"}
