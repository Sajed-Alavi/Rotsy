"""Trivy adapter: invoke the binary, parse its JSON report.

Registry-only by construction — ``--image-src remote`` keeps Trivy off the
local docker/containerd/podman daemons.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

from ...config import get_settings
from . import db as scanner_db
from .base import (
    TOOL_TIMEOUT,
    TRIVY_LOCK,
    Credentials,
    ScanOutcome,
    assert_static_ref,
    exec_scanner,
    first_error_line,
    parse_json_report,
    redact,
    tail,
)
from .registry import DockerRegistry

# Trivy's cache is a BoltDB file, and only one process can hold it open at a
# time — concurrent scans against one shared --cache-dir collide on that lock
# (see TRIVY_LOCK's docstring in base.py). Instead each scan checks out one of
# a small pool of private cache-dir replicas — plain copies of the canonical
# database — so up to SCANNER_MAX_CONCURRENCY scans run genuinely in parallel
# with no file shared between them. A replica is refreshed lazily, the first
# time a scan checks it out after the canonical database has moved on; TRIVY_
# LOCK is held for that refresh (and by db/update.py's writes to the canonical
# dir) so a copy never reads it mid-write.
_REPLICA_ROOT = scanner_db.TRIVY_SCAN_REPLICAS_DIR
_replica_slots: asyncio.Queue[int] | None = None
_METADATA_FILENAME = "metadata.json"


def _prune_orphaned_replicas(n: int) -> None:
    """Drop replica directories left over from a higher SCANNER_MAX_CONCURRENCY
    in a previous run — this pool's own slots only ever populate 0..n-1, so
    without this, turning the setting back down would leave the old, higher-
    numbered replicas (each a full copy of the database) on disk forever."""
    if not _REPLICA_ROOT.exists():
        return
    for entry in _REPLICA_ROOT.iterdir():
        if entry.is_dir() and entry.name.isdigit() and int(entry.name) >= n:
            shutil.rmtree(entry, ignore_errors=True)


def _slots() -> asyncio.Queue[int]:
    global _replica_slots
    if _replica_slots is None:
        n = max(1, get_settings().SCANNER_MAX_CONCURRENCY)
        _prune_orphaned_replicas(n)
        _replica_slots = asyncio.Queue()
        for i in range(n):
            _replica_slots.put_nowait(i)
    return _replica_slots


def _replica_dir(slot: int) -> Path:
    return _REPLICA_ROOT / str(slot)


def _replica_stale(slot: int) -> bool:
    canonical = scanner_db.TRIVY_DB_DIR / "trivy.db"
    if not canonical.is_file():
        return False  # nothing to copy — readiness() already gates scans on this
    replica = _replica_dir(slot) / "db" / "trivy.db"
    if not replica.is_file() or canonical.stat().st_mtime > replica.stat().st_mtime:
        return True
    # Checked independently of the main db: the Java DB can go from absent to
    # present on its own (e.g. a later background update job finally reaches
    # it via the fallback mirror — see db/update.py) without the main db ever
    # changing, and a replica that never notices would keep forcing every scan
    # through the inline first-run fetch this pool exists to avoid.
    canonical_java = scanner_db.TRIVY_JAVA_DB_DIR / _METADATA_FILENAME
    if canonical_java.is_file():
        replica_java = _replica_dir(slot) / "java-db" / _METADATA_FILENAME
        if not replica_java.is_file() or canonical_java.stat().st_mtime > replica_java.stat().st_mtime:
            return True
    return False


def _refresh_replica(slot: int) -> None:
    """Copy the canonical database into one replica slot.

    Runs off the event loop via ``asyncio.to_thread`` at the call site — the
    on-disk database can be well over a gigabyte, and copying it inline would
    stall every other coroutine in this single-process backend for however
    long that takes.
    """
    dest = _replica_dir(slot)
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(scanner_db.TRIVY_DB_DIR, dest / "db", dirs_exist_ok=True)
    if scanner_db.TRIVY_JAVA_DB_DIR.is_dir():
        shutil.copytree(scanner_db.TRIVY_JAVA_DB_DIR, dest / "java-db", dirs_exist_ok=True)


async def _checkout_replica() -> int:
    slot = await _slots().get()
    try:
        if _replica_stale(slot):
            async with TRIVY_LOCK:
                if _replica_stale(slot):  # a concurrent checkout may have just refreshed it
                    await asyncio.to_thread(_refresh_replica, slot)
    except Exception:
        _slots().put_nowait(slot)  # don't leak the slot on a failed refresh
        raise
    return slot


def _checkin_replica(slot: int) -> None:
    _slots().put_nowait(slot)


def cvss(entry: dict[str, Any]) -> float:
    """Highest CVSS score any vendor assigned, preferring v3 over v2."""
    best = 0.0
    for vendor in (entry.get("CVSS") or {}).values():
        if not isinstance(vendor, dict):
            continue
        for key in ("V3Score", "V2Score"):
            try:
                best = max(best, float(vendor.get(key) or 0))
            except (TypeError, ValueError):
                continue
    return best


def parse(raw: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for result in raw.get("Results") or []:
        for entry in result.get("Vulnerabilities") or []:
            findings.append({
                "cve": entry.get("VulnerabilityID") or entry.get("CVE") or "UNKNOWN",
                "severity": (entry.get("Severity") or "UNKNOWN").upper(),
                "package": entry.get("PkgName") or "",
                "installed_version": entry.get("InstalledVersion") or "",
                "fixed_version": entry.get("FixedVersion") or "",
                "title": entry.get("Title") or (entry.get("Description") or "")[:200],
                "cvss": cvss(entry),
            })
    return findings


async def run(
    registry: DockerRegistry, image_ref: str, creds: Credentials, *, verify_tls: bool,
) -> ScanOutcome:
    """Scan ``image_ref`` with Trivy, reading it from the registry only."""
    binary = scanner_db.which("trivy")
    if binary is None:
        return ScanOutcome("trivy", False, error="trivy binary not installed in this image")
    assert_static_ref(image_ref)

    slot = await _checkout_replica()
    try:
        cache_dir = _replica_dir(slot)
        args = [
            binary, "image",
            "--quiet",
            "--format", "json",
            # OS packages + language dependencies. Excludes secret/misconfig
            # scanning, which needs neither and would slow every scan down.
            "--scanners", "vuln",
            # Read from the registry, never from a local container runtime.
            "--image-src", "remote",
            # The database is managed by app.modules.nexus.db (via the replica
            # pool above). Without this, Trivy tries to download it mid-scan
            # and fails the scan when it cannot.
            "--skip-db-update",
            "--cache-dir", str(cache_dir),
            "--timeout", TOOL_TIMEOUT,
        ]
        # The Java DB is optional (readiness() never requires it — see
        # db/status.py's java_db_present) so it can legitimately be absent even
        # when the core DB is present, e.g. its own download failed or was
        # never attempted. --skip-java-db-update is only valid once Trivy has
        # downloaded it at least once into *this* cache dir; passing it on a
        # first run is a hard error ("cannot be specified on the first run"),
        # not a graceful skip. Only pass it when there is actually something
        # on disk (in this replica) to skip updating.
        if (cache_dir / "java-db" / _METADATA_FILENAME).is_file():
            args.append("--skip-java-db-update")
        else:
            # A first run for this replica: Trivy fetches the Java DB inline
            # regardless of what we pass, and its default registry (ghcr.io,
            # falling back to mirror.gcr.io) throttles anonymous pulls hard
            # enough in practice to stall the whole scan for many minutes
            # rather than fail fast (github.com/aquasecurity/trivy/discussions/8224,
            # /issues/7938). Giving Trivy the same second mirror our own
            # background update job uses (see db/update.py) means a throttled
            # primary doesn't leave the scan with nowhere else to go.
            args += [
                "--java-db-repository", scanner_db.TRIVY_JAVA_DB_IMAGE,
                "--java-db-repository", scanner_db.TRIVY_JAVA_DB_IMAGE_FALLBACK,
            ]
        # A plaintext connector needs Trivy to talk HTTP; a TLS connector we cannot
        # verify needs certificate checks relaxed. Both are covered by --insecure.
        insecure = registry.is_plaintext or not verify_tls
        if insecure:
            args.append("--insecure")
        args.append(image_ref)

        env = {
            # Credentials via the environment so they stay out of the process table.
            "TRIVY_USERNAME": creds.username,
            "TRIVY_PASSWORD": creds.password,
            # Belt and braces across Trivy versions: some releases key plaintext
            # registry access off these variables rather than off --insecure.
            # Unrecognised TRIVY_* variables are ignored by Trivy.
            **({"TRIVY_INSECURE": "true", "TRIVY_NON_SSL": "true"} if registry.is_plaintext else {}),
        }

        started = time.monotonic()
        try:
            code, stdout, stderr = await exec_scanner(args, env)
        except TimeoutError as exc:
            return ScanOutcome("trivy", False, error=str(exc),
                               detail=redact(args, [creds.password]),
                               duration_ms=int((time.monotonic() - started) * 1000))
        elapsed = int((time.monotonic() - started) * 1000)
        detail = f"$ {redact(args, [creds.password])}\nexit {code}\n{tail(stderr or stdout)}"

        if code != 0:
            return ScanOutcome("trivy", False, error=first_error_line(stderr) or f"trivy exited {code}",
                               detail=detail, duration_ms=elapsed)
        try:
            raw = parse_json_report(stdout)
        except json.JSONDecodeError as exc:
            return ScanOutcome("trivy", False, error=f"could not parse trivy JSON output: {exc}",
                               detail=detail, duration_ms=elapsed)
        return ScanOutcome("trivy", True, parse(raw), detail=detail, duration_ms=elapsed)
    finally:
        _checkin_replica(slot)
