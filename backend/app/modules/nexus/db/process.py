"""Subprocess, download-progress and archive helpers shared by update/import.

Both the online update and the offline import extract tar archives and shell
out to the scanner binaries; keeping those primitives here stops the two
lifecycle modules from growing their own copies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Awaitable, Callable

from .paths import TRIVY_DB_DIR, ProgressCallback
from .status import dir_size

logger = logging.getLogger(__name__)


def proxy_env(proxy: str) -> dict[str, str]:
    """Proxy variables for database-download subprocesses."""
    if not proxy:
        return {}
    return {
        "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
        "http_proxy": proxy, "https_proxy": proxy,
    }


async def run_streaming(
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
    except asyncio.CancelledError:
        # The job was cancelled (POST /jobs/{id}/cancel -> Task.cancel()). Kill
        # the subprocess before re-raising, or it keeps running orphaned and
        # writing to the cache dir after the job itself has been marked
        # cancelled — this was the "cancel does nothing" bug: the job hash
        # flipped to cancelled but nothing ever stopped the download.
        proc.kill()
        await proc.wait()
        pump_task.cancel()
        raise
    await pump_task
    return rc, lines


async def oras_pull(
    oras: str,
    image: str,
    out_dir: str,
    *,
    expected_mb: int,
    emit: ProgressCallback,
    env: dict[str, str],
    progress_range: tuple[int, int],
    label: str,
    scanner: str = "trivy",
    timeout: float = 1800.0,
    total_bytes: int | None = None,
) -> bool:
    """``oras pull`` with live byte progress polled from the output directory.

    ``oras`` does not report progress in a machine-readable way, so the output
    directory is sized every two seconds to derive speed and ETA.

    ``total_bytes``, when the caller resolved it from the image's real manifest
    (see ``_oras_manifest_size`` in ``update.py``), is the actual expected size
    and is reported with ``estimated: False``. When it is ``None`` or ``0`` this
    falls back to the ``expected_mb`` hardcoded guess, reported as an estimate —
    that guess has drifted from the real archive size before (the database grows
    over time), which is why a caller-supplied real size always wins.

    ``timeout`` bounds the whole pull: a stalled connection would otherwise hold
    the job open indefinitely, with the queue behind it.
    """
    low, high = progress_range
    span = high - low
    real_total = int(total_bytes) if total_bytes else 0
    estimated = real_total <= 0
    if real_total > 0:
        display_total = real_total
    elif expected_mb:
        display_total = int(expected_mb * 1e6)
    else:
        display_total = 0
    display_mb = display_total / 1e6
    deadline = time.monotonic() + timeout
    await emit(low, f"{label}: connecting to the registry",
               {"scanner": scanner, "stage": "connecting", "total_bytes": display_total,
                "estimated": estimated, "artifact": label})
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
    try:
        prev_bytes, prev_time = 0, time.monotonic()
        while proc.returncode is None:
            if time.monotonic() > deadline:
                proc.kill()
                await proc.wait()
                drain_task.cancel()
                await emit(high, f"{label}: timed out after {timeout / 60:.0f} minutes",
                           {"scanner": scanner, "stage": "failed", "artifact": label,
                            "error": f"timed out after {timeout / 60:.0f} minutes"})
                return False
            downloaded = dir_size(Path(out_dir))
            now = time.monotonic()
            elapsed = now - prev_time
            speed_bps = ((downloaded - prev_bytes) / elapsed) if elapsed > 0 else 0.0
            prev_bytes, prev_time = downloaded, now

            done_mb = downloaded / 1e6
            speed_mbps = speed_bps / 1e6
            remaining_mb = max(0.0, display_mb - done_mb) if display_total else 0.0
            pct = low + min(span, int(done_mb / display_mb * span)) if display_total else low
            total_label = f"{display_mb:.0f}" if not estimated else f"~{display_mb:.0f}"
            await emit(
                pct,
                f"{label}: {done_mb:.1f}" + (f" / {total_label} MB{rate(speed_mbps, remaining_mb)}"
                                              if display_total else " MB downloaded"),
                # ``estimated`` says whether ``total_bytes`` came from the real
                # manifest (False) or the hardcoded fallback guess (True) — the UI
                # shows an indeterminate bar rather than a precise-looking but
                # invented percentage when it can't trust the total.
                {"scanner": scanner, "stage": "downloading", "artifact": label,
                 "done_bytes": downloaded, "total_bytes": display_total, "estimated": estimated,
                 "indeterminate": not display_total,
                 "speed_bps": round(speed_bps),
                 "eta_seconds": round(remaining_mb / speed_mbps) if speed_mbps > 0.1 else None},
            )
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=2.0)
            except asyncio.TimeoutError:
                continue

        await drain_task
    except asyncio.CancelledError:
        # Same reasoning as run_streaming(): a cancelled job must not leave
        # ``oras`` running in the background, still writing to out_dir after the
        # job itself has been marked cancelled.
        proc.kill()
        await proc.wait()
        drain_task.cancel()
        raise

    ok = proc.returncode == 0
    final_bytes = dir_size(Path(out_dir))
    if ok:
        await emit(high, f"{label}: {final_bytes / 1e6:.1f} MB downloaded",
                   {"scanner": scanner, "stage": "downloading", "artifact": label,
                    "done_bytes": final_bytes, "total_bytes": final_bytes, "estimated": False})
    else:
        detail_msg = ' | '.join(tail[-2:])[:200]
        await emit(high, f"{label}: FAILED (exit {proc.returncode}) {detail_msg}",
                   {"scanner": scanner, "stage": "failed", "artifact": label,
                    "error": f"exit {proc.returncode}: {detail_msg}"})
    return ok


def rate(speed_mbps: float, remaining_mb: float) -> str:
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


def extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        # filter="data" refuses absolute paths, "..", symlinks and device
        # files — these archives come off the network, so they get the same
        # scrutiny as any other untrusted input.
        tf.extractall(dest, filter="data")


def prune_trivy() -> None:
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
