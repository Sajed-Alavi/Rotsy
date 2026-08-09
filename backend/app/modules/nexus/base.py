"""Shared plumbing for the scanner adapters: types, subprocess exec, parsing.

The Trivy and Grype adapters (:mod:`.trivy`, :mod:`.grype`) both need the same
handful of primitives — a redacted command line for the operator, a bounded
subprocess call that keeps stdout and stderr apart, a JSON parser tolerant of
log noise, and the guard that enforces the no-runtime invariant. They live here
so neither adapter owns them and they cannot drift apart.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

# Wall-clock ceiling for one scanner invocation. Comfortably above the internal
# timeout each tool is given so we see the tool's own error rather than a kill.
SCAN_TIMEOUT = 600.0
TOOL_TIMEOUT = "8m"

# Stereoscope/Trivy source prefixes that would read from a container runtime or
# the local filesystem instead of the registry.
_RUNTIME_SCHEMES = (
    "docker:", "podman:", "containerd:", "docker-archive:", "oci-archive:",
    "oci-dir:", "singularity:", "dir:", "file:", "sbom:",
)

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")

# Trivy's cache directory holds a BoltDB file that only one process can open at
# a time. Two trivy invocations against the same --cache-dir at once — two
# scans, or a scan overlapping a database update's own-downloader fallback —
# collide on that lock ("cache may be in use by another process: timeout"), and
# a scan stuck waiting on it can run past even its own --timeout, surfacing as
# our SCAN_TIMEOUT kill ("scanner exceeded 600s") instead of the real cause.
# Every trivy binary invocation that touches the cache dir takes this lock
# first, so at most one runs at a time; grype has no such shared-lock file and
# does not need one.
TRIVY_LOCK = asyncio.Lock()


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


def assert_static_ref(image_ref: str) -> None:
    """Guard the no-runtime invariant at the last possible moment."""
    lowered = image_ref.lower()
    for scheme in _RUNTIME_SCHEMES:
        if lowered.startswith(scheme):
            raise ValueError(
                f"refusing to scan '{image_ref}': the '{scheme.rstrip(':')}' source reads from a "
                "container runtime or the local filesystem. This system performs registry-only "
                "static analysis and never starts containers."
            )


def redact(args: list[str], secrets: list[str]) -> str:
    """Render a command line for operator display with secrets removed."""
    rendered = " ".join(args)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "***")
    return rendered


async def exec_scanner(
    args: list[str], env: dict[str, str], timeout: float = SCAN_TIMEOUT, cwd: str | None = None,  # NOSONAR
) -> tuple[int, str, str]:
    """Run a scanner, capturing stdout and stderr separately.

    ``timeout`` is deliberately part of this function's own signature, not
    left to callers to enforce with their own ``asyncio.timeout()`` (a
    linter's generic preference): the timeout and the subprocess kill/wait
    cleanup on expiry are one unit here — the caller has no handle on
    ``proc`` to clean it up itself, so pushing the timeout out to the call
    site would either leak the subprocess or force every caller to duplicate
    this cleanup.

    stdout carries the JSON report, so unlike the database helpers it must not
    be merged with the log stream.

    ``cwd``: Trivy/Grype scan a remote registry reference and never needed
    one (``None`` preserves their existing behavior — inherit this process's
    cwd). sonar-scanner does: its own launcher script sets Java's
    ``project.home`` from `pwd` at invoke time, independently of
    ``-Dsonar.sources``, and several sensors (git-dirty-file detection for
    text/secrets scanning, CPD, others) key off *that*, not the sources path.
    Leaving this unset when running sonar-scanner had it silently launching
    from the backend's own ``/app`` working directory instead of the cloned
    repository: SonarQube still reported "ANALYSIS SUCCESSFUL" and a valid
    compute-engine task (``sonar.sources`` indexing partly still worked), but
    almost every sensor found "no files to be analyzed" against the wrong
    project root — a real repository's analysis silently came back with
    near-zero bugs/vulnerabilities/code smells, indistinguishable from a
    successful run of a genuinely clean project.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **env},
        cwd=cwd,
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"scanner exceeded {timeout:.0f}s")
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def tail(text: str, limit: int = 2000) -> str:
    """Last ``limit`` characters of scanner output — the part that explains why."""
    cleaned = text.strip()
    return cleaned if len(cleaned) <= limit else "…" + cleaned[-limit:]


def parse_json_report(text: str) -> dict[str, Any]:
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


def first_error_line(stderr: str) -> str:
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
