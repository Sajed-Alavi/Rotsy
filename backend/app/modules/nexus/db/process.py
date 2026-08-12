"""Subprocess, download-progress and archive helpers shared by update/import.

Both the online update and the offline import extract tar archives and shell
out to the scanner binaries; keeping those primitives here stops the two
lifecycle modules from growing their own copies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tarfile
import time
from pathlib import Path
from typing import Awaitable, Callable

import aiofiles
import httpx

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
    timeout: float,  # NOSONAR
    env: dict[str, str] | None = None,
    on_line: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[int, list[str]]:
    """Run a command, streaming merged stdout/stderr through ``on_line``.

    ``timeout`` stays part of this function's own signature rather than a
    caller-side ``asyncio.timeout()`` (a linter's generic preference): on
    expiry this also kills the subprocess and cancels the line-pump task,
    cleanup only possible here, where ``proc`` and ``pump_task`` are in scope.

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
        async with asyncio.timeout(timeout):
            rc = await proc.wait()
    except TimeoutError:
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


def _resolve_display_total(total_bytes: int | None, expected_mb: int) -> tuple[int, bool]:
    """``(display_total_bytes, estimated)``. A caller-supplied real total
    (from the image's actual manifest) always wins; the hardcoded
    ``expected_mb`` guess is a marked-estimated fallback — that guess has
    drifted from the real archive size before (the database grows over
    time)."""
    real_total = int(total_bytes) if total_bytes else 0
    if real_total > 0:
        return real_total, False
    if expected_mb:
        return int(expected_mb * 1e6), True
    return 0, True


async def _emit_pull_tick(
    emit: ProgressCallback, scanner: str, label: str, out_dir: str,
    low: int, span: int, display_total: int, display_mb: float, estimated: bool,
    prev_bytes: int, prev_time: float,
) -> tuple[int, float]:
    """One progress tick: size the output directory, derive speed/ETA from
    the delta since the last tick, and emit it. Returns the new (bytes, time)
    baseline for the next tick."""
    downloaded = dir_size(Path(out_dir))
    now = time.monotonic()
    elapsed = now - prev_time
    speed_bps = ((downloaded - prev_bytes) / elapsed) if elapsed > 0 else 0.0

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
    return downloaded, now


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
    timeout: float = 1800.0,  # NOSONAR
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

    Uses a manual deadline rather than ``asyncio.timeout()`` (a linter's
    generic preference): the poll loop below wraps ``proc.wait()`` in
    ``asyncio.shield()`` specifically so a 2-second poll timeout doesn't
    cancel the wait itself, only the "check back in 2s" — a context-manager
    timeout around the whole loop would cancel that shielded wait too.
    """
    low, high = progress_range
    span = high - low
    display_total, estimated = _resolve_display_total(total_bytes, expected_mb)
    display_mb = display_total / 1e6
    deadline = time.monotonic() + timeout
    # oras has no byte-range resume, so a retried pull always restarts this
    # artifact from zero regardless of what a previous, killed attempt left
    # behind in out_dir. Clearing it here — safe because each artifact this is
    # called for gets its own out_dir, never shared with another (see
    # _update_trivy) — keeps that honest: without it, the first tick below
    # would count the previous attempt's leftover bytes as if they downloaded
    # in the instant since this attempt started, producing a nonsense speed
    # (seen in practice as "559680.0 MB/s") and a progress number that jumps
    # backward once oras actually starts overwriting the old file.
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
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
            prev_bytes, prev_time = await _emit_pull_tick(
                emit, scanner, label, out_dir, low, span, display_total, display_mb, estimated,
                prev_bytes, prev_time,
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


# Bounded quantifiers throughout: a WWW-Authenticate challenge is a short,
# server-controlled header, but leaving \w+/[^"]* unbounded is still flagged
# as superlinear-backtracking-prone by construction — the bound removes that
# regardless of how long an actual challenge string could ever get.
_OCI_CHALLENGE_FIELD = re.compile(r'(\w{1,32})="([^"]{0,4096})"')


def _parse_oci_ref(image: str) -> tuple[str, str, str]:
    """Split "registry.host/namespace/repo:tag" into (registry, repo, tag)."""
    registry, _, rest = image.partition("/")
    repo, _, tag = rest.rpartition(":")
    return registry, repo, tag or "latest"


async def _oci_bearer_token(client: httpx.AsyncClient, challenge: str, repo: str) -> str | None:
    """Exchange a ``WWW-Authenticate: Bearer ...`` challenge for a pull token.

    The standard Docker Registry v2 auth flow: an anonymous request gets a
    401 naming a token endpoint (``realm``) plus ``service``/``scope``; that
    endpoint, hit anonymously in turn, hands back a short-lived token good for
    the manifest and blob requests that follow.
    """
    params = dict(_OCI_CHALLENGE_FIELD.findall(challenge))
    realm = params.get("realm")
    if not realm:
        return None
    resp = await client.get(realm, params={
        "service": params.get("service", ""),
        "scope": params.get("scope") or f"repository:{repo}:pull",
    })
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data.get("token") or data.get("access_token")


async def _resolve_oci_blob(
    client: httpx.AsyncClient, registry: str, repo: str, tag: str, headers: dict[str, str],
) -> tuple[str, int]:
    """Manifest fetch, with one 401-retry through the bearer-token exchange.

    Raises on failure — ``resumable_oci_pull``'s single caller turns any of
    these into one uniform "could not resolve the manifest" outcome.
    """
    manifest_url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    resp = await client.get(manifest_url, headers=headers)
    if resp.status_code == 401:
        token = await _oci_bearer_token(client, resp.headers.get("www-authenticate", ""), repo)
        if not token:
            raise RuntimeError("registry did not accept an anonymous pull")
        headers["Authorization"] = f"Bearer {token}"
        resp = await client.get(manifest_url, headers=headers)
    resp.raise_for_status()
    layers = resp.json().get("layers") or []
    if not layers:
        raise RuntimeError("manifest has no layers")
    return layers[0]["digest"], int(layers[0]["size"])


async def _pull_blob_chunk(
    client: httpx.AsyncClient, blob_url: str, headers: dict[str, str], repo: str,
    partial: Path, done: int, size: int, progress_range: tuple[int, int],
    prev_bytes: float, prev_time: float, emit: ProgressCallback, scanner: str, label: str,
) -> tuple[int, float, float]:
    """One GET against the blob endpoint — resumed via ``Range`` when ``done``
    is already nonzero — streaming chunks to ``partial`` and reporting
    progress as it goes. Raises on any failure; ``resumable_oci_pull``'s loop
    treats every failure here the same way: pause, then retry from wherever
    ``done`` landed. Returns the updated ``(done, prev_bytes, prev_time)``.
    """
    low, high = progress_range
    req_headers = dict(headers)
    if done:
        req_headers["Range"] = f"bytes={done}-"
    async with client.stream("GET", blob_url, headers=req_headers) as stream:
        if stream.status_code == 401:
            # A transfer of this size at these speeds can easily outlive a
            # short-lived registry token — refresh it and retry, on a short
            # pause rather than the outer loop's full 10s (this is usually
            # legitimate and self-resolving). Still a real pause, not zero:
            # a token that keeps coming back 401'd (a scope the registry
            # silently narrows, say) would otherwise loop this GET-then-
            # token-exchange pair back to back for the whole surrounding
            # timeout with no throttling at all.
            token = await _oci_bearer_token(client, stream.headers.get("www-authenticate", ""), repo)
            if not token:
                raise RuntimeError("token refresh failed after a 401")
            headers["Authorization"] = f"Bearer {token}"
            await asyncio.sleep(2)
            return done, prev_bytes, prev_time
        if stream.status_code not in (200, 206):
            raise httpx.HTTPStatusError(f"HTTP {stream.status_code}", request=stream.request, response=stream)
        # The server may not honor Range and send the whole blob back
        # (status 200) even though we asked to resume — in that case what's
        # already on disk is not a prefix of what's coming, so start over.
        if done and stream.status_code == 200:
            done = 0
        async with aiofiles.open(partial, "ab" if done else "wb") as f:
            async for chunk in stream.aiter_bytes(262_144):
                await f.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                elapsed = now - prev_time
                if elapsed > 1.0:
                    speed_mbps = (done - prev_bytes) / elapsed / 1e6
                    pct = low + (int(min(high - low, done / size * (high - low))) if size else 0)
                    await emit(
                        pct,
                        f"{label}: {done / 1e6:.1f} / {size / 1e6:.0f} MB"
                        + rate(speed_mbps, max(0.0, (size - done) / 1e6)),
                        {"scanner": scanner, "stage": "downloading", "artifact": label,
                         "done_bytes": done, "total_bytes": size, "estimated": False},
                    )
                    prev_bytes, prev_time = done, now
    return done, prev_bytes, prev_time


async def resumable_oci_pull(
    image: str,
    dest: Path,
    *,
    emit: ProgressCallback,
    scanner: str,
    label: str,
    progress_range: tuple[int, int],
    env: dict[str, str],
    timeout: float = 3600.0,  # NOSONAR
) -> bool:
    """Pull a single-layer OCI artifact's blob directly over HTTPS, resuming
    a dropped transfer with an HTTP ``Range`` request instead of restarting it.

    Last-resort fallback for the Java DB (see ``_update_trivy`` in
    ``update.py``): ``oras pull`` has no concept of resume at all, so on a
    slow or flaky link a multi-hundred-MB transfer that keeps resetting
    (``stream error ... PROTOCOL_ERROR``, seen in practice against both
    ghcr.io and its mirrors) can never complete — every retry pays for the
    same bytes again. The blob endpoint behind an OCI registry is ordinary
    HTTPS and, unlike ``oras``'s own transport, does honor ``Range``.

    ``dest`` is written via a same-named ``.partial`` file that is only
    renamed into place once its size matches the manifest — the caller is
    expected to pass a stable, persistent path (not a per-job tempdir) so a
    partial transfer surviving past this call's own timeout, or past the
    whole job failing, can still be resumed by a later run.
    """
    registry, repo, tag = _parse_oci_ref(image)
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or None
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    low, high = progress_range

    async with httpx.AsyncClient(timeout=30.0, proxy=proxy, follow_redirects=True) as client:
        await emit(low, f"{label}: resolving the manifest",
                   {"scanner": scanner, "stage": "connecting", "artifact": label})
        headers = {"Accept": "application/vnd.oci.image.manifest.v1+json, "
                              "application/vnd.docker.distribution.manifest.v2+json"}
        try:
            digest, size = await _resolve_oci_blob(client, registry, repo, tag, headers)
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
            await emit(high, f"{label}: could not resolve the manifest — {exc}",
                       {"scanner": scanner, "stage": "failed", "artifact": label, "error": str(exc)})
            return False

        done = partial.stat().st_size if partial.exists() else 0
        if done > size:  # a stale partial from a different build; start clean
            done = 0
            partial.unlink(missing_ok=True)

        blob_url = f"https://{registry}/v2/{repo}/blobs/{digest}"
        deadline = time.monotonic() + timeout
        prev_bytes, prev_time = done, time.monotonic()
        while done < size:
            if time.monotonic() > deadline:
                await emit(high, f"{label}: timed out after {timeout / 60:.0f} minutes "
                                  f"({done / 1e6:.1f} / {size / 1e6:.0f} MB kept for next time)",
                           {"scanner": scanner, "stage": "failed", "artifact": label,
                            "done_bytes": done, "total_bytes": size})
                return False
            try:
                done, prev_bytes, prev_time = await _pull_blob_chunk(
                    client, blob_url, headers, repo, partial, done, size, progress_range,
                    prev_bytes, prev_time, emit, scanner, label,
                )
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                await emit(
                    low + (int(min(high - low, done / size * (high - low))) if size else 0),
                    f"{label}: dropped at {done / 1e6:.1f} / {size / 1e6:.0f} MB ({exc}); resuming in 10s",
                    {"scanner": scanner, "stage": "connecting", "artifact": label,
                     "done_bytes": done, "total_bytes": size},
                )
                await asyncio.sleep(10)

    if done != size:
        return False
    partial.replace(dest)
    await emit(high, f"{label}: {size / 1e6:.1f} MB downloaded",
               {"scanner": scanner, "stage": "downloading", "artifact": label,
                "done_bytes": size, "total_bytes": size, "estimated": False})
    return True


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
