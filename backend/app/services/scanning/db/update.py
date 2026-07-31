"""Refresh the vulnerability databases over the network."""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    GRYPE_PROGRESS,
    SI_UNITS,
    TRIVY_CACHE_ROOT,
    TRIVY_DB_IMAGE,
    TRIVY_DB_MB,
    TRIVY_JAVA_DB_DIR,
    TRIVY_JAVA_DB_IMAGE,
    TRIVY_JAVA_DB_MB,
    TRIVY_DB_DIR,
    ProgressCallback,
    which,
)
from .process import extract, oras_pull, proxy_env, prune_trivy, rate, run_streaming
from .status import as_datetime, grype_status, status

logger = logging.getLogger(__name__)


def _is_current(scanner: str, info: dict[str, Any]) -> str | None:
    """Return a human reason when the on-disk database is already current.

    Trivy publishes ``NextUpdate``, which is authoritative. Grype publishes one
    build per day, so a database built today (UTC) is the newest there is.
    """
    if not info.get("present"):
        return None
    if scanner == "trivy":
        next_update = as_datetime(info.get("next_update"))
        if next_update and next_update > datetime.now(timezone.utc):
            built = (info.get("built") or "?")[:10]
            return f"database built {built} is current until {info['next_update'][:10]}"
        return None
    built = as_datetime(info.get("built"))
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
    async def emit(pct: int, msg: str, detail: dict[str, Any] | None = None) -> None:
        if on_progress is not None:
            await on_progress(pct, msg, detail)

    enabled = [s.strip().lower() for s in scanners if s.strip()]
    results: dict[str, Any] = {}
    env = proxy_env(proxy)

    if not force:
        snapshot = status()
        for name in list(enabled):
            reason = _is_current(name, snapshot.get(name, {}))
            if reason:
                results[name] = {"ok": True, "skipped": True, "downloaded": False, "reason": reason}
                await emit(50, f"{name}: {reason} — skipping download",
                           {"scanner": name, "stage": "skipped", "reason": reason})
                enabled.remove(name)

    if not enabled:
        await emit(100, "all databases current, nothing to download", {"stage": "done"})
        return results

    if "trivy" in enabled:
        results["trivy"] = await _update_trivy(emit, env)
    if "grype" in enabled:
        results["grype"] = await _update_grype(emit, env)

    if not results:
        await emit(100, "no scanners enabled", {"stage": "done"})
    return results


async def _update_trivy(emit: ProgressCallback, env: dict[str, str]) -> dict[str, Any]:
    trivy = which("trivy")
    if trivy is None:
        return {"ok": False, "error": "trivy binary not installed"}

    oras = which("oras")
    if oras is not None:
        tmp = tempfile.mkdtemp(prefix="trivy-db-")
        try:
            if not await oras_pull(oras, TRIVY_DB_IMAGE, tmp, expected_mb=TRIVY_DB_MB,
                                   emit=emit, env=env, progress_range=(2, 35),
                                   label="trivy-db", scanner="trivy"):
                raise RuntimeError(f"oras pull {TRIVY_DB_IMAGE} failed")
            # The Java database is optional: without it Trivy still scans OS
            # packages and every non-Java language ecosystem.
            java_ok = await oras_pull(oras, TRIVY_JAVA_DB_IMAGE, tmp, expected_mb=TRIVY_JAVA_DB_MB,
                                      emit=emit, env=env, progress_range=(35, 46),
                                      label="trivy-java-db", scanner="trivy")
            await emit(46, "trivy: extracting database",
                       {"scanner": "trivy", "stage": "extracting"})
            db_tar, java_tar = Path(tmp) / "db.tar.gz", Path(tmp) / "javadb.tar.gz"
            if not db_tar.exists():
                raise RuntimeError("oras pull succeeded but db.tar.gz is missing from the artifact")
            extract(db_tar, TRIVY_DB_DIR)
            if java_ok and java_tar.exists():
                extract(java_tar, TRIVY_JAVA_DB_DIR)
            prune_trivy()
            await emit(50, "trivy: database updated", {"scanner": "trivy", "stage": "done"})
            return {"ok": True, "downloaded": True, "java_db": java_ok, "via": "oras"}
        except Exception as exc:  # noqa: BLE001 - fall through to Trivy's own downloader
            logger.warning("Trivy database update via oras failed: %s", exc)
            await emit(20, f"trivy: oras path failed ({exc}); trying trivy's own downloader",
                       {"scanner": "trivy", "stage": "connecting", "note": str(exc)})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Fallback: let Trivy fetch its own database. Trivy's downloader reports no
    # machine-readable progress at all, so this path can only report the stage —
    # said plainly rather than left as a silent gap.
    await emit(25, "trivy: downloading database (trivy image --download-db-only)",
               {"scanner": "trivy", "stage": "downloading", "indeterminate": True,
                "note": "trivy's own downloader reports no progress"})
    try:
        rc, lines = await run_streaming(
            [trivy, "image", "--download-db-only", "--cache-dir", str(TRIVY_CACHE_ROOT)],
            timeout=900, env=env,
        )
    except Exception as exc:  # noqa: BLE001
        await emit(50, f"trivy: database update failed — {exc}",
                   {"scanner": "trivy", "stage": "failed", "error": str(exc)})
        return {"ok": False, "error": str(exc)}
    if rc == 0:
        prune_trivy()
        await emit(50, "trivy: database updated", {"scanner": "trivy", "stage": "done"})
        return {"ok": True, "downloaded": True, "via": "trivy"}
    tail = " | ".join(line for line in lines[-4:] if line.strip())[:400]
    await emit(50, f"trivy: database download FAILED (exit {rc})",
               {"scanner": "trivy", "stage": "failed", "error": f"exit {rc}: {tail}"})
    return {
        "ok": False, "downloaded": False, "rc": rc,
        "error": f"trivy could not download its database (exit {rc}). "
                 f"On a restricted network use the offline import instead. {tail}",
    }


async def _update_grype(emit: ProgressCallback, env: dict[str, str]) -> dict[str, Any]:
    grype = which("grype")
    if grype is None:
        return {"ok": False, "error": "grype binary not installed"}

    await emit(52, "grype: downloading database (grype db update)",
               {"scanner": "grype", "stage": "connecting"})
    state = {"prev_bytes": 0.0, "prev_time": time.monotonic()}

    async def on_line(line: str) -> None:
        match = GRYPE_PROGRESS.search(line)
        if not match:
            return
        done = float(match.group(1)) * SI_UNITS.get(match.group(2).upper(), 1)
        total = float(match.group(3)) * SI_UNITS.get(match.group(4).upper(), 1)
        now = time.monotonic()
        elapsed = now - state["prev_time"]
        speed_bps = ((done - state["prev_bytes"]) / elapsed) if elapsed > 0 else 0.0
        speed_mbps = speed_bps / 1e6
        state["prev_bytes"], state["prev_time"] = done, now
        pct = 52 + (int(min(44, done / total * 44)) if total else 0)
        # Grype prints its own totals, so unlike Trivy these numbers are real:
        # estimated=False, and the UI can draw an exact bar.
        await emit(
            pct,
            f"grype-db: {done / 1e6:.1f} / ~{total / 1e6:.0f} MB{rate(speed_mbps, (total - done) / 1e6)}",
            {"scanner": "grype", "stage": "downloading", "artifact": "grype-db",
             "done_bytes": round(done), "total_bytes": round(total), "estimated": False,
             "speed_bps": round(speed_bps),
             "eta_seconds": round((total - done) / speed_bps) if speed_bps > 1e5 else None},
        )

    try:
        rc, lines = await run_streaming([grype, "db", "update", "-v"], timeout=1200, env=env, on_line=on_line)
    except Exception as exc:  # noqa: BLE001
        await emit(100, f"grype: database update failed — {exc}",
                   {"scanner": "grype", "stage": "failed", "error": str(exc)})
        return {"ok": False, "error": str(exc)}

    if rc == 0:
        await emit(100, "grype: database updated", {"scanner": "grype", "stage": "done"})
        return {"ok": True, "downloaded": True}

    # The download failed. Never report that as success: say so, and say
    # whether a usable (older) database is still on disk.
    tail = " | ".join(line for line in lines[-4:] if line.strip())[:400]
    existing = grype_status()
    if existing.get("present"):
        built = str(existing.get("built") or "unknown date")[:10]
        await emit(100, f"grype: download FAILED (exit {rc}) — keeping existing database (built {built})",
                   {"scanner": "grype", "stage": "failed", "used_existing": True,
                    "error": f"exit {rc}; retained the existing database built {built}"})
        return {
            "ok": False, "downloaded": False, "used_existing": True, "rc": rc,
            "error": f"grype db update failed (exit {rc}); the existing database (built {built}) "
                     f"was retained and scans will keep using it. {tail}",
        }
    await emit(100, f"grype: download FAILED (exit {rc}) — no database on disk",
               {"scanner": "grype", "stage": "failed", "used_existing": False,
                "error": f"exit {rc}: {tail}"})
    return {
        "ok": False, "downloaded": False, "used_existing": False, "rc": rc,
        "error": f"grype db update failed (exit {rc}) and no database is present. "
                 f"On a restricted network use the offline import. {tail}",
    }
