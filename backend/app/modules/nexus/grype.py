"""Grype adapter: invoke the binary, parse its JSON report.

Registry-only by construction — the explicit ``registry:`` reference plus
``GRYPE_DEFAULT_IMAGE_PULL_SOURCE=registry`` keep Grype off the local container
runtimes, which it would otherwise try first.
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import db as scanner_db
from .base import (
    Credentials,
    ScanOutcome,
    assert_static_ref,
    exec_scanner,
    first_error_line,
    parse_json_report,
    tail,
)
from .registry import DockerRegistry


# Grype's severity vocabulary is not quite the shared one. "Negligible" sits
# below Low and has no NVD equivalent, so it does not appear in SEVERITIES —
# and an unrecognised band is bucketed as UNKNOWN downstream. That would report
# a finding we have graded as one we could not grade, which is the wrong
# direction to be wrong in: UNKNOWN means "unclassified, look at it", and these
# are classified. Fold it into LOW, the nearest band we do carry.
_SEVERITY_ALIASES = {"NEGLIGIBLE": "LOW"}


def _normalise_severity(value: str | None) -> str:
    raw = (value or "UNKNOWN").upper()
    return _SEVERITY_ALIASES.get(raw, raw)


def _highest_cvss(match: dict[str, Any]) -> float:
    """The highest CVSS base score across every related vulnerability entry
    a match carries — Grype lists CVSS per source (NVD, GHSA, ...), and the
    highest one is the more conservative finding to surface."""
    cvss = 0.0
    for related in match.get("relatedVulnerabilities") or []:
        for score in related.get("cvss") or []:
            try:
                cvss = max(cvss, float((score.get("metrics") or {}).get("baseScore") or score.get("score") or 0))
            except (TypeError, ValueError):
                continue
    return cvss


def parse(raw: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for match in raw.get("matches") or []:
        vuln = match.get("vulnerability") or {}
        artifact = match.get("artifact") or {}
        fixed = ((vuln.get("fix") or {}).get("versions") or [])
        findings.append({
            "cve": vuln.get("id") or "UNKNOWN",
            "severity": _normalise_severity(vuln.get("severity")),
            "package": artifact.get("name") or "",
            "installed_version": artifact.get("version") or "",
            "fixed_version": fixed[0] if fixed else "",
            "title": (vuln.get("description") or "")[:200],
            "cvss": _highest_cvss(match),
        })
    return findings


async def run(
    registry: DockerRegistry, image_ref: str, creds: Credentials, *, verify_tls: bool,
) -> ScanOutcome:
    """Scan ``image_ref`` with Grype, reading it from the registry only."""
    binary = scanner_db.which("grype")
    if binary is None:
        return ScanOutcome("grype", False, error="grype binary not installed in this image")
    assert_static_ref(image_ref)

    # The explicit "registry:" scheme is what keeps Grype off the local
    # container runtimes, which it would otherwise try first.
    args = [binary, f"registry:{image_ref}", "-o", "json"]
    env = {
        "GRYPE_REGISTRY_AUTH_USERNAME": creds.username,
        "GRYPE_REGISTRY_AUTH_PASSWORD": creds.password,
        "GRYPE_DEFAULT_IMAGE_PULL_SOURCE": "registry",
        # The database is managed by app.modules.nexus.db. Auto-update would
        # fail the scan on a restricted network, and validate-age makes Grype
        # refuse any database older than five days — a slightly stale database
        # is far better than no scan.
        "GRYPE_DB_AUTO_UPDATE": "false",
        "GRYPE_DB_VALIDATE_AGE": "false",
        "GRYPE_CHECK_FOR_APP_UPDATE": "false",
    }
    if registry.is_plaintext:
        env["GRYPE_REGISTRY_INSECURE_USE_HTTP"] = "true"
    if registry.is_plaintext or not verify_tls:
        env["GRYPE_REGISTRY_INSECURE_SKIP_TLS_VERIFY"] = "true"

    started = time.monotonic()
    try:
        code, stdout, stderr = await exec_scanner(args, env)
    except TimeoutError as exc:
        return ScanOutcome("grype", False, error=str(exc), detail=" ".join(args),
                           duration_ms=int((time.monotonic() - started) * 1000))
    elapsed = int((time.monotonic() - started) * 1000)
    detail = f"$ {' '.join(args)}\nexit {code}\n{tail(stderr or stdout)}"

    if code != 0:
        return ScanOutcome("grype", False, error=first_error_line(stderr) or f"grype exited {code}",
                           detail=detail, duration_ms=elapsed)
    try:
        raw = parse_json_report(stdout)
    except json.JSONDecodeError as exc:
        return ScanOutcome("grype", False, error=f"could not parse grype JSON output: {exc}",
                           detail=detail, duration_ms=elapsed)
    return ScanOutcome("grype", True, parse(raw), detail=detail, duration_ms=elapsed)
