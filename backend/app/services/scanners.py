"""Static vulnerability scanning of Nexus-hosted images with Trivy and Grype.

**Static analysis only.** Neither scanner is allowed to touch a container
runtime: images are read over the Docker Registry v2 API (manifest → config →
layer blobs) and analysed as data. Nothing is ever started, run or spun up. This
is enforced, not merely intended:

  * Trivy runs with ``--image-src remote``, so it never probes the local
    docker/containerd/podman daemons.
  * Grype is given an explicit ``registry:`` reference and
    ``GRYPE_DEFAULT_IMAGE_PULL_SOURCE=registry``; by default it would try the
    container runtimes first.
  * :func:`_assert_static_ref` rejects any reference carrying a runtime scheme.
  * The backend container mounts no Docker socket (see docker-compose.yml).

The registry endpoint is discovered from Nexus at scan time (see
:mod:`app.services.registry`) — there is no configured registry URL or port.

Root causes of the ``FAILED`` reports this module previously produced, and what
fixes them here:

1. *Wrong endpoint.* The reference was built from a hand-configured registry
   URL, falling back to ``{nexus-host}:8081/{repo}/{image}`` — a path Nexus does
   not serve the v2 API on, so every scan 404'd. Now resolved by discovery.
2. *TLS conflated with the REST connection.* Plaintext/TLS handling was driven by
   ``NEXUS_VERIFY_SSL`` (which describes the REST API), so a plaintext connector
   behind an HTTPS Nexus was probed over TLS. Now driven by the connector the
   repository actually declares.
3. *Mid-scan database updates.* Both tools try to refresh their database when
   asked to scan, and Grype outright refuses a database older than five days.
   On a restricted network that download fails and takes the scan down with it.
   Updates are now disabled during scans and owned by
   :mod:`app.services.scanner_db`, with a preflight that reports a missing
   database as such.
4. *Unusable diagnostics.* Failures were truncated to 500 characters and dropped
   into a JSON blob. The command line, exit code and output tail are now
   persisted on the report and surfaced in the UI.
5. *Credentials on the command line.* ``--username``/``--password`` put the Nexus
   password in the process table. Both scanners now take credentials from the
   environment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ScanReport, Vulnerability
from . import scanner_db
from .registry import DockerRegistry

logger = logging.getLogger(__name__)

# Wall-clock ceiling for one scanner invocation. Comfortably above the internal
# timeout each tool is given so we see the tool's own error rather than a kill.
SCAN_TIMEOUT = 600.0
_TOOL_TIMEOUT = "8m"

# Stereoscope/Trivy source prefixes that would read from a container runtime or
# the local filesystem instead of the registry.
_RUNTIME_SCHEMES = (
    "docker:", "podman:", "containerd:", "docker-archive:", "oci-archive:",
    "oci-dir:", "singularity:", "dir:", "file:", "sbom:",
)

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


@dataclass
class Credentials:
    """Registry credentials — the Nexus service account."""

    username: str
    password: str


@dataclass
class ScanOutcome:
    """Result of one scanner invocation against one image."""

    scanner: str
    ok: bool
    vulnerabilities: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    detail: str = ""  # command + exit code + output tail, for the operator
    duration_ms: int = 0


def _assert_static_ref(image_ref: str) -> None:
    """Guard the no-runtime invariant at the last possible moment."""
    lowered = image_ref.lower()
    for scheme in _RUNTIME_SCHEMES:
        if lowered.startswith(scheme):
            raise ValueError(
                f"refusing to scan '{image_ref}': the '{scheme.rstrip(':')}' source reads from a "
                "container runtime or the local filesystem. This system performs registry-only "
                "static analysis and never starts containers."
            )


def _redact(args: list[str], secrets: list[str]) -> str:
    """Render a command line for operator display with secrets removed."""
    rendered = " ".join(args)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "***")
    return rendered


async def _exec(
    args: list[str], env: dict[str, str], timeout: float = SCAN_TIMEOUT,
) -> tuple[int, str, str]:
    """Run a scanner, capturing stdout and stderr separately.

    stdout carries the JSON report, so unlike the database helpers it must not
    be merged with the log stream.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **env},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"scanner exceeded {timeout:.0f}s")
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _tail(text: str, limit: int = 2000) -> str:
    """Last ``limit`` characters of scanner output — the part that explains why."""
    cleaned = text.strip()
    return cleaned if len(cleaned) <= limit else "…" + cleaned[-limit:]


def _parse_json_report(text: str) -> dict[str, Any]:
    """Parse a scanner's JSON report, tolerating log lines printed before it.

    Grype logs warnings to stdout when talking to a plaintext registry (e.g.
    ``[0000] WARN registry communication is insecure``), ahead of the document.
    """
    if not text or not text.strip():
        return {}
    start = text.find("{")
    if start <= 0:
        return json.loads(text)
    return json.loads(text[start:])


# ---------------------------------------------------------------------------
# Trivy
# ---------------------------------------------------------------------------
def _trivy_cvss(entry: dict[str, Any]) -> float:
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


def _trivy_parse(raw: dict[str, Any]) -> list[dict[str, Any]]:
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
                "cvss": _trivy_cvss(entry),
            })
    return findings


async def run_trivy(
    registry: DockerRegistry, image_ref: str, creds: Credentials, *, verify_tls: bool,
) -> ScanOutcome:
    """Scan ``image_ref`` with Trivy, reading it from the registry only."""
    binary = scanner_db.which("trivy")
    if binary is None:
        return ScanOutcome("trivy", False, error="trivy binary not installed in this image")
    _assert_static_ref(image_ref)

    args = [
        binary, "image",
        "--quiet",
        "--format", "json",
        # OS packages + language dependencies. Excludes secret/misconfig
        # scanning, which needs neither and would slow every scan down.
        "--scanners", "vuln",
        # Read from the registry, never from a local container runtime.
        "--image-src", "remote",
        # The database is managed by app.services.scanner_db. Without these,
        # Trivy tries to download it mid-scan and fails the scan when it cannot.
        "--skip-db-update",
        "--skip-java-db-update",
        "--cache-dir", str(scanner_db.TRIVY_CACHE_ROOT),
        "--timeout", _TOOL_TIMEOUT,
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
        code, stdout, stderr = await _exec(args, env)
    except TimeoutError as exc:
        return ScanOutcome("trivy", False, error=str(exc),
                           detail=_redact(args, [creds.password]),
                           duration_ms=int((time.monotonic() - started) * 1000))
    elapsed = int((time.monotonic() - started) * 1000)
    detail = f"$ {_redact(args, [creds.password])}\nexit {code}\n{_tail(stderr or stdout)}"

    if code != 0:
        return ScanOutcome("trivy", False, error=_first_error_line(stderr) or f"trivy exited {code}",
                           detail=detail, duration_ms=elapsed)
    try:
        raw = _parse_json_report(stdout)
    except json.JSONDecodeError as exc:
        return ScanOutcome("trivy", False, error=f"could not parse trivy JSON output: {exc}",
                           detail=detail, duration_ms=elapsed)
    return ScanOutcome("trivy", True, _trivy_parse(raw), detail=detail, duration_ms=elapsed)


# ---------------------------------------------------------------------------
# Grype
# ---------------------------------------------------------------------------
def _grype_parse(raw: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for match in raw.get("matches") or []:
        vuln = match.get("vulnerability") or {}
        artifact = match.get("artifact") or {}
        cvss = 0.0
        for related in match.get("relatedVulnerabilities") or []:
            for score in related.get("cvss") or []:
                try:
                    cvss = max(cvss, float((score.get("metrics") or {}).get("baseScore")
                                           or score.get("score") or 0))
                except (TypeError, ValueError):
                    continue
        fixed = ((vuln.get("fix") or {}).get("versions") or [])
        findings.append({
            "cve": vuln.get("id") or "UNKNOWN",
            "severity": (vuln.get("severity") or "UNKNOWN").upper(),
            "package": artifact.get("name") or "",
            "installed_version": artifact.get("version") or "",
            "fixed_version": fixed[0] if fixed else "",
            "title": (vuln.get("description") or "")[:200],
            "cvss": cvss,
        })
    return findings


async def run_grype(
    registry: DockerRegistry, image_ref: str, creds: Credentials, *, verify_tls: bool,
) -> ScanOutcome:
    """Scan ``image_ref`` with Grype, reading it from the registry only."""
    binary = scanner_db.which("grype")
    if binary is None:
        return ScanOutcome("grype", False, error="grype binary not installed in this image")
    _assert_static_ref(image_ref)

    # The explicit "registry:" scheme is what keeps Grype off the local
    # container runtimes, which it would otherwise try first.
    args = [binary, f"registry:{image_ref}", "-o", "json"]
    env = {
        "GRYPE_REGISTRY_AUTH_USERNAME": creds.username,
        "GRYPE_REGISTRY_AUTH_PASSWORD": creds.password,
        "GRYPE_DEFAULT_IMAGE_PULL_SOURCE": "registry",
        # The database is managed by app.services.scanner_db. Auto-update would
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
        code, stdout, stderr = await _exec(args, env)
    except TimeoutError as exc:
        return ScanOutcome("grype", False, error=str(exc), detail=" ".join(args),
                           duration_ms=int((time.monotonic() - started) * 1000))
    elapsed = int((time.monotonic() - started) * 1000)
    detail = f"$ {' '.join(args)}\nexit {code}\n{_tail(stderr or stdout)}"

    if code != 0:
        return ScanOutcome("grype", False, error=_first_error_line(stderr) or f"grype exited {code}",
                           detail=detail, duration_ms=elapsed)
    try:
        raw = _parse_json_report(stdout)
    except json.JSONDecodeError as exc:
        return ScanOutcome("grype", False, error=f"could not parse grype JSON output: {exc}",
                           detail=detail, duration_ms=elapsed)
    return ScanOutcome("grype", True, _grype_parse(raw), detail=detail, duration_ms=elapsed)


def _first_error_line(stderr: str) -> str:
    """Most explanatory single line of a scanner's stderr.

    Both tools print a wall of log lines; the operator wants the one that names
    the actual problem, not the first or last line.
    """
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if any(token in lowered for token in ("error", "fatal", "failed", "denied", "refused",
                                              "unauthorized", "not found", "no such host")):
            return line[:500]
    return lines[-1][:500] if lines else ""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
_RUNNERS = {"trivy": run_trivy, "grype": run_grype}


async def scan_image(
    session: AsyncSession,
    registry: DockerRegistry,
    image: str,
    scanners: list[str],
    creds: Credentials,
    *,
    verify_tls: bool = True,
) -> list[ScanReport]:
    """Scan one image with each requested scanner and persist the reports.

    ``image`` is ``name:tag`` within ``registry.repo``. One :class:`ScanReport`
    is written per scanner, including on failure — a failed scan is a result the
    operator needs to see, with the reason attached, not a silent gap.
    """
    image_ref = registry.image_ref(image)
    _assert_static_ref(image_ref)
    ready = scanner_db.readiness(scanners)
    reports: list[ScanReport] = []

    for name in scanners:
        name = name.lower()
        report = ScanReport(
            target_repo=registry.repo, image=image, scanner=name, status="running",
            registry_ref=image_ref,
        )
        session.add(report)
        await session.flush()

        outcome = await _run_one(name, registry, image_ref, creds, ready, verify_tls=verify_tls)
        _apply_outcome(session, report, outcome, registry.repo)
        reports.append(report)

    await session.commit()
    return reports


async def reap_stale_reports(session: AsyncSession) -> int:
    """Close out reports left in ``running`` by a worker that went away.

    A report row is written before the scanner is invoked so an in-flight scan
    is visible. If the process dies mid-scan that row would otherwise sit at
    ``running`` for ever and the image would look permanently in progress.
    Called once at startup — this inspects the database only and starts no scans.
    """
    from sqlalchemy import update

    result = await session.execute(
        update(ScanReport)
        .where(ScanReport.status == "running")
        .values(
            status="failed",
            error="Interrupted: the backend restarted while this scan was running. "
                  "Scan the image again to retry.",
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("Marked %d interrupted scan report(s) as failed at startup", count)
    return count


async def _run_one(
    name: str,
    registry: DockerRegistry,
    image_ref: str,
    creds: Credentials,
    ready: dict[str, scanner_db.Readiness],
    *,
    verify_tls: bool,
) -> ScanOutcome:
    """Preflight, then invoke one scanner, converting exceptions into outcomes."""
    check = ready.get(name)
    if check is not None and not check.ready:
        return ScanOutcome(name, False, error=check.reason)
    runner = _RUNNERS.get(name)
    if runner is None:
        return ScanOutcome(name, False, error=f"unknown scanner '{name}' (expected trivy or grype)")
    try:
        return await runner(registry, image_ref, creds, verify_tls=verify_tls)
    except Exception as exc:  # noqa: BLE001 - one scanner must not sink the others
        logger.exception("%s scan of %s failed unexpectedly", name, image_ref)
        return ScanOutcome(name, False, error=f"{type(exc).__name__}: {exc}")


def _apply_outcome(
    session: AsyncSession, report: ScanReport, outcome: ScanOutcome, repo: str,
) -> None:
    """Write an outcome onto its report row, with severity counts and findings."""
    counts = dict.fromkeys(SEVERITIES, 0)
    rows: list[Vulnerability] = []
    for finding in outcome.vulnerabilities:
        severity = (finding.get("severity") or "UNKNOWN").upper()
        if severity not in counts:
            severity = "UNKNOWN"
        counts[severity] += 1
        rows.append(Vulnerability(
            report_id=report.id, repo=repo, scanner=outcome.scanner,
            cve=finding.get("cve") or "UNKNOWN", severity=severity,
            package=finding.get("package") or "",
            installed_version=finding.get("installed_version") or "",
            fixed_version=finding.get("fixed_version") or "",
            title=finding.get("title") or "",
            cvss=float(finding.get("cvss") or 0.0),
        ))
    if rows:
        session.add_all(rows)

    report.status = "success" if outcome.ok else "failed"
    report.critical = counts["CRITICAL"]
    report.high = counts["HIGH"]
    report.medium = counts["MEDIUM"]
    report.low = counts["LOW"]
    report.unknown = counts["UNKNOWN"]
    report.error = outcome.error or None
    report.duration_ms = outcome.duration_ms
    report.finished_at = datetime.now(timezone.utc)
    report.raw_json = json.dumps({
        "findings": len(outcome.vulnerabilities),
        "error": outcome.error or None,
        "detail": outcome.detail,
    }, default=str)
