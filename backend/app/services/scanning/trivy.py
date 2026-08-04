"""Trivy adapter: invoke the binary, parse its JSON report.

Registry-only by construction — ``--image-src remote`` keeps Trivy off the
local docker/containerd/podman daemons.
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import db as scanner_db
from .base import (
    TOOL_TIMEOUT,
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

    args = [
        binary, "image",
        "--quiet",
        "--format", "json",
        # OS packages + language dependencies. Excludes secret/misconfig
        # scanning, which needs neither and would slow every scan down.
        "--scanners", "vuln",
        # Read from the registry, never from a local container runtime.
        "--image-src", "remote",
        # The database is managed by app.services.scanning.db. Without this,
        # Trivy tries to download it mid-scan and fails the scan when it cannot.
        "--skip-db-update",
        "--cache-dir", str(scanner_db.TRIVY_CACHE_ROOT),
        "--timeout", TOOL_TIMEOUT,
    ]
    # The Java DB is optional (readiness() never requires it — see
    # db/status.py's java_db_present) so it can legitimately be absent even
    # when the core DB is present, e.g. its own download failed or was never
    # attempted. --skip-java-db-update is only valid once Trivy has downloaded
    # it at least once; passing it on a first run is a hard error ("cannot be
    # specified on the first run"), not a graceful skip. Only pass it when
    # there is actually something on disk to skip updating.
    if (scanner_db.TRIVY_JAVA_DB_DIR / "metadata.json").is_file():
        args.append("--skip-java-db-update")
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
