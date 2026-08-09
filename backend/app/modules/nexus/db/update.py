"""Refresh the vulnerability databases over the network."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .paths import (
    GRYPE_AVAILABLE_TIMEOUT,
    GRYPE_DOWNLOAD_TIMEOUT,
    GRYPE_PROGRESS,
    GRYPE_TMPDIR,
    GRYPE_V6_BASE,
    GRYPE_V6_LATEST,
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
from .status import as_datetime, dir_size, grype_db_usable, grype_status, status
from ..base import TRIVY_LOCK
from ....services import make_detail_emitter

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


async def _skip_current_scanners(enabled: list[str], emit) -> dict[str, Any]:
    """Remove (in place) and record already-current scanners from ``enabled``."""
    skipped: dict[str, Any] = {}
    snapshot = status()
    for name in list(enabled):
        reason = _is_current(name, snapshot.get(name, {}))
        if reason:
            skipped[name] = {"ok": True, "skipped": True, "downloaded": False, "reason": reason}
            await emit(50, f"{name}: {reason} — skipping download",
                       {"scanner": name, "stage": "skipped", "reason": reason})
            enabled.remove(name)
    return skipped


async def _run_isolated_update(
    name: str, updater, emit, env: dict[str, str], results: dict[str, Any],
) -> None:
    """Run one scanner's update, isolating any crash so the other scanner is
    still attempted. Previously an uncaught exception in one updater
    propagated straight out of :func:`update`, so the other scanner was
    silently never reached — this looked like "the database is never
    downloaded" with nothing in the UI explaining why."""
    try:
        results[name] = await updater(emit, env)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - see docstring above
        logger.exception("%s database update crashed", name)
        results[name] = {"ok": False, "error": str(exc)}


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
    emit = make_detail_emitter(on_progress)

    enabled = [s.strip().lower() for s in scanners if s.strip()]
    results: dict[str, Any] = {}
    env = proxy_env(proxy)

    if not force:
        results.update(await _skip_current_scanners(enabled, emit))

    if not enabled:
        await emit(100, "all databases current, nothing to download", {"stage": "done"})
        return results

    # Each scanner is isolated: an unexpected crash in one must not stop the
    # other from being attempted (see _run_isolated_update).
    if "trivy" in enabled:
        await _run_isolated_update("trivy", _update_trivy, emit, env, results)
    if "grype" in enabled:
        await _run_isolated_update("grype", _update_grype, emit, env, results)

    if not results:
        await emit(100, "no scanners enabled", {"stage": "done"})
    return results


async def _oras_manifest_size(oras: str, image: str, env: dict[str, str]) -> int:
    """Real total size (config + all layers) of an OCI artifact, from its manifest.

    ``oras pull`` itself reports no machine-readable progress, and the hardcoded
    ``TRIVY_DB_MB``/``TRIVY_JAVA_DB_MB`` guesses in paths.py go stale as the
    published databases grow — that drift is exactly why the UI has shown a
    ~50-125 MB total while a download kept going past 250 MB. The manifest is
    small and fetching it costs one extra round trip; a failure here just falls
    back to the hardcoded guess (marked ``estimated``), never blocks the pull.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            oras, "manifest", "fetch", "--output", "-", image,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, **env},
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode != 0:
            return 0
        manifest = json.loads(out)
        total = int((manifest.get("config") or {}).get("size", 0) or 0)
        total += sum(int((layer or {}).get("size", 0) or 0) for layer in manifest.get("layers") or [])
        return total
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - best-effort size hint only
        logger.debug("could not resolve the real size of %s: %s", image, exc)
        return 0


async def _oras_pull_retrying(*, retries: int = 2, backoff: float = 5.0, **kwargs: Any) -> bool:
    """``oras_pull`` with a couple of retries for transient network failures.

    ``oras pull`` streams over HTTP/2 through a registry/CDN, and a stream
    reset mid-transfer (``stream error: stream ID 1; PROTOCOL_ERROR``) is a
    known transient failure mode there — not a sign the artifact or the
    network path is actually broken. Retrying the whole pull (oras has no
    resume) turns "download 265 of 945 MB, hit a stream reset, fail the whole
    job" into "retry and usually succeed a few seconds later". A deliberate
    cancellation is not retried — it propagates immediately.
    """
    attempt = 0
    while True:
        attempt += 1
        # No try/except here: nothing in this loop catches Exception broadly,
        # so a CancelledError from oras_pull already propagates out on its
        # own — no need to catch and immediately re-raise it.
        ok = await oras_pull(**kwargs)
        if ok or attempt > retries:
            return ok
        label = kwargs.get("label", "download")
        logger.warning("%s: pull failed (attempt %d/%d), retrying in %.0fs",
                        label, attempt, retries + 1, backoff)
        await kwargs["emit"](
            kwargs["progress_range"][0],
            f"{label}: attempt {attempt} failed, retrying in {backoff:.0f}s",
            {"scanner": kwargs.get("scanner", "trivy"), "stage": "connecting",
             "artifact": label, "note": f"retry {attempt}/{retries}"},
        )
        await asyncio.sleep(backoff)


async def _run_streaming_retrying(
    args: list[str],
    *,
    timeout: float,
    env: dict[str, str],
    emit: ProgressCallback,
    pct: int,
    label: str,
    scanner: str,
    on_line: Callable[[str], Awaitable[None]] | None = None,
    retries: int = 2,
    backoff: float = 15.0,
) -> tuple[int, list[str]]:
    """``run_streaming`` with a couple of retries for transient failures.

    Mirrors ``_oras_pull_retrying``: a multi-hundred-MB-to-multi-GB database
    transfer that drops partway through (a reset connection, a registry
    hiccup) is retried automatically instead of being left failed for the
    operator to notice and re-trigger by hand — which, since neither trivy's
    nor grype's own downloader exposes byte-range resume, would otherwise
    mean starting the whole transfer over from zero regardless. What resume
    there is comes from the destination being the same persistent directory
    across attempts (trivy's cache dir, grype's TMPDIR): whatever either
    tool's downloader can itself reuse from a previous attempt, it gets the
    chance to. A non-zero exit is retried the same as a raised exception; a
    deliberate cancellation is not retried — it propagates immediately.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            rc, lines = await run_streaming(args, timeout=timeout, env=env, on_line=on_line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried below, or re-raised past the last attempt
            if attempt > retries:
                raise
            reason = str(exc)
        else:
            if rc == 0 or attempt > retries:
                return rc, lines
            reason = f"exit {rc}"
        logger.warning("%s: attempt %d/%d failed (%s), retrying in %.0fs",
                        label, attempt, retries + 1, reason, backoff)
        await emit(
            pct, f"{label}: attempt {attempt} failed ({reason}), retrying in {backoff:.0f}s",
            {"scanner": scanner, "stage": "connecting", "note": f"retry {attempt}/{retries}"},
        )
        await asyncio.sleep(backoff)


async def _update_trivy(emit: ProgressCallback, env: dict[str, str]) -> dict[str, Any]:
    trivy = which("trivy")
    if trivy is None:
        return {"ok": False, "error": "trivy binary not installed"}

    oras = which("oras")
    if oras is not None:
        tmp = tempfile.mkdtemp(prefix="trivy-db-")
        try:
            db_size = await _oras_manifest_size(oras, TRIVY_DB_IMAGE, env)
            if not await _oras_pull_retrying(
                oras=oras, image=TRIVY_DB_IMAGE, out_dir=tmp, expected_mb=TRIVY_DB_MB,
                emit=emit, env=env, progress_range=(2, 35),
                label="trivy-db", scanner="trivy", total_bytes=db_size,
            ):
                raise RuntimeError(f"oras pull {TRIVY_DB_IMAGE} failed")
            # The Java database is optional: without it Trivy still scans OS
            # packages and every non-Java language ecosystem.
            java_size = await _oras_manifest_size(oras, TRIVY_JAVA_DB_IMAGE, env)
            java_ok = await _oras_pull_retrying(
                oras=oras, image=TRIVY_JAVA_DB_IMAGE, out_dir=tmp, expected_mb=TRIVY_JAVA_DB_MB,
                emit=emit, env=env, progress_range=(35, 46),
                label="trivy-java-db", scanner="trivy", total_bytes=java_size,
            )
            await emit(46, "trivy: extracting database",
                       {"scanner": "trivy", "stage": "extracting"})
            db_tar, java_tar = Path(tmp) / "db.tar.gz", Path(tmp) / "javadb.tar.gz"
            if not db_tar.exists():
                raise RuntimeError("oras pull succeeded but db.tar.gz is missing from the artifact")
            # Held for the actual write to the canonical dir: trivy.py's scan
            # replicas are copied from here under the same lock, and must
            # never see it mid-write.
            async with TRIVY_LOCK:
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
        async with TRIVY_LOCK:
            rc, lines = await _run_streaming_retrying(
                [trivy, "image", "--download-db-only", "--cache-dir", str(TRIVY_CACHE_ROOT)],
                timeout=900, env=env, emit=emit, pct=25, label="trivy-db", scanner="trivy",
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


async def _grype_db_size(env: dict[str, str]) -> int:
    """Expected download size in bytes, or 0 if it cannot be determined.

    Grype reports no total of its own when its output is piped, so this asks the
    database host directly: resolve ``latest.json`` for the current archive name,
    then read its ``Content-Length``. Best-effort — a failure here costs an exact
    progress bar, never the download itself.
    """
    try:
        proxy = env.get("HTTPS_PROXY") or env.get("HTTP_PROXY") or None
        async with httpx.AsyncClient(timeout=20.0, proxy=proxy, follow_redirects=True) as client:
            meta = (await client.get(GRYPE_V6_LATEST)).json()
            path = meta.get("path")
            if not path:
                return 0
            head = await client.head(f"{GRYPE_V6_BASE}/{path}")
            return int(head.headers.get("content-length") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not determine the Grype database size: %s", exc)
        return 0


async def _emit_download_tick(
    emit: ProgressCallback, done: int, total: int, prev_bytes: int, prev_time: float,
) -> tuple[int, float]:
    """One progress tick: compute speed/ETA from the delta since the last
    tick, emit it, and return the new (bytes, time) baseline for the next one."""
    now = time.monotonic()
    elapsed = now - prev_time
    speed_bps = ((done - prev_bytes) / elapsed) if elapsed > 0 else 0.0

    speed_mbps = speed_bps / 1e6
    remaining_mb = max(0.0, (total - done) / 1e6) if total else 0.0
    pct = 52 + (int(min(44, done / total * 44)) if total else 0)
    await emit(
        pct,
        f"grype-db: {done / 1e6:.1f}"
        + (f" / {total / 1e6:.0f} MB{rate(speed_mbps, remaining_mb)}" if total else " MB downloaded"),
        {"scanner": "grype", "stage": "downloading", "artifact": "grype-db",
         "done_bytes": done, "total_bytes": total, "estimated": False,
         "indeterminate": not total,
         "speed_bps": round(speed_bps),
         "eta_seconds": round(remaining_mb * 1e6 / speed_bps) if (total and speed_bps > 1e5) else None},
    )
    return done, now


async def _watch_download(
    target: Path, total: int, emit: ProgressCallback, stop: asyncio.Event,
) -> None:
    """Emit byte progress by sizing Grype's download directory every two seconds.

    Grype 0.87 prints no incremental progress when piped, so parsing its output
    yields nothing between "downloading" and the result — which is why the UI sat
    at "Connecting / 0 B" for the whole transfer. It streams through go-getter
    into ``$TMPDIR``, so pointing that at a directory of our choosing and sizing
    it gives real bytes. Same technique already used for the Trivy ``oras`` pull.
    """
    prev_bytes, prev_time = 0, time.monotonic()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
            return
        except asyncio.TimeoutError:
            pass

        done = dir_size(target)
        if done <= 0:
            continue
        prev_bytes, prev_time = await _emit_download_tick(emit, done, total, prev_bytes, prev_time)


def _prepare_grype_download_env(env: dict[str, str]) -> tuple[dict[str, str], Path]:
    """A scratch TMPDIR plus timeouts long enough for a ~139 MB database on a
    slow link. Grype's own download timeout defaults to 5 minutes, which such
    a transfer cannot meet; it then aborts mid-stream and reports "unexpected
    EOF", which looks like a network fault rather than a deadline. TMPDIR is
    pinned so the transfer can be observed while it happens."""
    GRYPE_TMPDIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="dl-", dir=str(GRYPE_TMPDIR)))
    merged = {
        **env,
        "TMPDIR": str(tmp),
        "GRYPE_DB_UPDATE_DOWNLOAD_TIMEOUT": GRYPE_DOWNLOAD_TIMEOUT,
        "GRYPE_DB_UPDATE_AVAILABLE_TIMEOUT": GRYPE_AVAILABLE_TIMEOUT,
    }
    return merged, tmp


def _grype_line_progress(line: str, state: dict[str, float]) -> tuple[int, str, dict[str, Any]] | None:
    """Parse one Grype progress line, updating the download-speed ``state`` in
    place. Returns the ``(pct, message, detail)`` triple to emit, or ``None``
    if the line isn't a progress line."""
    match = GRYPE_PROGRESS.search(line)
    if not match:
        return None
    done = float(match.group(1)) * SI_UNITS.get(match.group(2).upper(), 1)
    reported = float(match.group(3)) * SI_UNITS.get(match.group(4).upper(), 1)
    now = time.monotonic()
    elapsed = now - state["prev_time"]
    speed_bps = ((done - state["prev_bytes"]) / elapsed) if elapsed > 0 else 0.0
    speed_mbps = speed_bps / 1e6
    state["prev_bytes"], state["prev_time"] = done, now
    pct = 52 + (int(min(44, done / reported * 44)) if reported else 0)
    message = f"grype-db: {done / 1e6:.1f} / {reported / 1e6:.0f} MB{rate(speed_mbps, (reported - done) / 1e6)}"
    detail = {"scanner": "grype", "stage": "downloading", "artifact": "grype-db",
             "done_bytes": round(done), "total_bytes": round(reported), "estimated": False,
             "speed_bps": round(speed_bps),
             "eta_seconds": round((reported - done) / speed_bps) if speed_bps > 1e5 else None}
    return pct, message, detail


async def _grype_update_success_result(emit: ProgressCallback) -> dict[str, Any]:
    # Exit 0 is not proof of a usable database. Grype can complete an update
    # and still leave nothing the binary can load — that is exactly what
    # happened with a v5-schema binary against a v6-only feed: the job
    # reported success, the dashboard showed READY, and every scan failed.
    # Verify through the same load path a scan uses before claiming success.
    usable, why = grype_db_usable()
    if not usable:
        await emit(100, f"grype: update finished but the database is unusable — {why}",
                   {"scanner": "grype", "stage": "failed", "error": why})
        return {"ok": False, "downloaded": True, "error": why}
    await emit(100, "grype: database updated", {"scanner": "grype", "stage": "done"})
    return {"ok": True, "downloaded": True}


async def _grype_update_failure_result(rc: int, lines: list[str], emit: ProgressCallback) -> dict[str, Any]:
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


async def _update_grype(emit: ProgressCallback, env: dict[str, str]) -> dict[str, Any]:
    grype = which("grype")
    if grype is None:
        return {"ok": False, "error": "grype binary not installed"}

    await emit(52, "grype: resolving the current database",
               {"scanner": "grype", "stage": "connecting"})

    env, tmp = _prepare_grype_download_env(env)

    total = await _grype_db_size(env)
    await emit(52, f"grype: downloading database ({total / 1e6:.0f} MB)" if total
               else "grype: downloading database (size unknown)",
               {"scanner": "grype", "stage": "downloading", "artifact": "grype-db",
                "done_bytes": 0, "total_bytes": total, "estimated": False,
                "indeterminate": not total})

    state = {"prev_bytes": 0.0, "prev_time": time.monotonic()}

    async def on_line(line: str) -> None:
        """Fast path for Grype builds that do print progress."""
        result = _grype_line_progress(line, state)
        if result is not None:
            await emit(*result)

    stop = asyncio.Event()
    watcher = asyncio.create_task(_watch_download(tmp, total, emit, stop))
    try:
        # The subprocess ceiling must exceed Grype's own download timeout, or
        # this would kill a transfer that was still within its budget.
        rc, lines = await _run_streaming_retrying(
            [grype, "db", "update", "-v"], timeout=3600, env=env, on_line=on_line,
            emit=emit, pct=52, label="grype-db", scanner="grype",
        )
    except Exception as exc:  # noqa: BLE001
        await emit(100, f"grype: database update failed — {exc}",
                   {"scanner": "grype", "stage": "failed", "error": str(exc)})
        return {"ok": False, "error": str(exc)}
    finally:
        stop.set()
        await watcher
        shutil.rmtree(tmp, ignore_errors=True)

    if rc == 0:
        return await _grype_update_success_result(emit)
    return await _grype_update_failure_result(rc, lines, emit)
