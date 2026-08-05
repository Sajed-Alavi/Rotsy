"""sonar-scanner subprocess execution.

MVP language boundary, enforced here as well as at the API layer
(``routers/sonar.py``): only Python/JavaScript/TypeScript are accepted.
sonar-scanner's CLI can analyze these directly from source with no build
step; coverage is only reported if the repo already contains a coverage
report Sonar knows how to read (e.g. ``coverage.xml``, ``lcov.info``) — Rotsy
never runs a test suite to produce one.

Reuses :mod:`app.modules.nexus.base` for subprocess execution rather than a
second implementation of "run a tool, capture stdout/stderr, enforce a
timeout" — the two adapters (Trivy/Grype vs. sonar-scanner) are different
tools with the same shape of problem.
"""

from __future__ import annotations

import re

from ..nexus.base import exec_scanner, redact, tail
from ...models.sonar import SUPPORTED_LANGUAGES

SCAN_TIMEOUT = 900.0  # sonar-scanner + server-side processing can be slow on first analysis

# The compute-engine task id sonar-scanner prints on success, e.g.
# "More about the report processing at http://.../api/ce/task?id=AYx1..."
_TASK_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")


class ScannerError(Exception):
    pass


class UnsupportedLanguageError(ScannerError):
    pass


def validate_language(language: str) -> None:
    if language not in SUPPORTED_LANGUAGES:
        raise UnsupportedLanguageError(
            f"{language!r} is not analyzable without a build step. "
            f"Supported for MVP: {', '.join(SUPPORTED_LANGUAGES)}."
        )


async def run_scanner(
    source_dir: str,
    project_key: str,
    sonar_url: str,
    analysis_token: str,
    branch: str,
) -> str:
    """Run sonar-scanner against ``source_dir``; return the compute-engine task id.

    The task id is what the caller polls via
    ``SonarClient.task_status``/``quality_gate`` — analysis itself finishes
    server-side, asynchronously, after the CLI exits.
    """
    args = [
        "sonar-scanner",
        f"-Dsonar.projectKey={project_key}",
        f"-Dsonar.sources={source_dir}",
        f"-Dsonar.host.url={sonar_url}",
        f"-Dsonar.token={analysis_token}",
        f"-Dsonar.branch.name={branch}" if branch else "",
        "-Dsonar.scm.disabled=true",  # source is a shallow clone; SCM blame data isn't available
    ]
    args = [a for a in args if a]

    returncode, stdout, stderr = await exec_scanner(
        args, env={}, timeout=SCAN_TIMEOUT,
    )
    if returncode != 0:
        raise ScannerError(
            f"sonar-scanner exited {returncode}: {tail(stderr or stdout)} "
            f"(command: {redact(args, [analysis_token])})"
        )

    match = _TASK_ID_RE.search(stdout)
    if not match:
        raise ScannerError(f"sonar-scanner succeeded but no compute-engine task id was found: {tail(stdout)}")
    return match.group(1)
