"""What vulnerability database is on disk, and can each scanner run right now.

Read-only: nothing here downloads, extracts or mutates anything.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    GRYPE_CACHE_ROOT,
    STALE_AFTER,
    TRIVY_CACHE_ROOT,
    TRIVY_DB_DIR,
    TRIVY_JAVA_DB_DIR,
    which,
)


# Cache for the Grype load probe: (monotonic_time, ok, reason).
_DB_STATUS_CACHE: dict[str, tuple[float, bool, str]] = {}
_DB_STATUS_TTL = 30.0


def parse_iso(value: Any) -> str | None:
    """Normalise an RFC3339 timestamp, rejecting Go's zero time."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return str(value) or None
    # Trivy writes "0001-01-01T00:00:00Z" for unset fields such as DownloadedAt.
    return None if dt.year <= 1 else dt.isoformat()


def as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def dir_size(path: Path) -> int:
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
    return parse_iso(match.group(1)) if match else None


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
    built = parse_iso(meta.get("UpdatedAt") or meta.get("CreatedAt"))
    out.update({
        "version": meta.get("Version"),
        "built": built,
        "next_update": parse_iso(meta.get("NextUpdate")),
        "downloaded_at": parse_iso(meta.get("DownloadedAt")),
        "java_db_present": (TRIVY_JAVA_DB_DIR / "metadata.json").is_file(),
        "size_bytes": dir_size(TRIVY_CACHE_ROOT),
        "path": str(TRIVY_DB_DIR),
    })
    return out


def grype_status() -> dict[str, Any]:
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
                "built": parse_iso(meta.get("built") or meta.get("Built")),
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
    out["size_bytes"] = dir_size(GRYPE_CACHE_ROOT)
    return out


def status() -> dict[str, Any]:
    """Per-scanner database facts: version, build date, size, install state."""
    return {"trivy": _trivy_status(), "grype": grype_status()}


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


def grype_db_usable() -> tuple[bool, str]:
    """Ask the Grype binary whether it can actually load the database on disk.

    Everything else here inspects files. That is not the same question, and the
    difference has bitten: a Grype pinned to a version reading schema v5 sat
    beside a valid schema v6 database, so the file checks reported a healthy
    database while every single scan failed with "database metadata not found".
    The dashboard said READY and nothing worked.

    ``grype db status`` is the authoritative answer because it is the same load
    path a scan takes. Result is cached briefly — this is called from a request
    handler and a subprocess per page view would be rude.
    """
    grype = which("grype")
    if grype is None:
        return False, "the grype binary is not installed in this image"

    now = time.monotonic()
    cached = _DB_STATUS_CACHE.get("grype")
    if cached and now - cached[0] < _DB_STATUS_TTL:
        return cached[1], cached[2]

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [grype, "db", "status"], capture_output=True, text=True, timeout=30,
        )
        output = f"{proc.stdout}\n{proc.stderr}"
        ok = proc.returncode == 0 and "invalid" not in output.lower()
        reason = ""
        if not ok:
            # Surface Grype's own words; they name the path it looked in, which
            # is what makes a schema mismatch diagnosable at a glance.
            detail = next(
                (ln.strip() for ln in output.splitlines()
                 if "error" in ln.lower() or "invalid" in ln.lower()),
                "grype reports its database is unusable",
            )
            reason = (
                f"{detail} — the installed grype cannot read the database on disk. "
                "This is usually a schema mismatch between the grype binary and the "
                "database; update the database, or align the pinned grype version."
            )
    except (subprocess.SubprocessError, OSError) as exc:
        ok, reason = False, f"could not query grype database status: {exc}"

    _DB_STATUS_CACHE["grype"] = (now, ok, reason)
    return ok, reason


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
        built_dt = as_datetime(built)
        stale = built_dt is not None and (now - built_dt) > STALE_AFTER

        # Files being present is necessary but not sufficient: the binary has to
        # be able to load them. Only Grype exposes a cheap way to ask.
        if name == "grype":
            usable, why = grype_db_usable()
            if not usable:
                out[name] = Readiness(name, False, why, stale=stale, built=built)
                continue

        out[name] = Readiness(name, True, "", stale=stale, built=built)
    return out
