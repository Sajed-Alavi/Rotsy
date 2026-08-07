"""sonar-scanner subprocess execution.

Language boundary, enforced here as well as at the API layer
(``routers/sonar.py``) and documented in ``models.sonar.SUPPORTED_LANGUAGES``:
only languages sonar-scanner can analyze directly from source, with no
compile/build/bytecode step, are accepted. sonar-scanner auto-detects which
of its analyzers apply per file by extension under ``sonar.sources`` — no
per-language flag is passed here, so supporting a new no-build language is a
``SUPPORTED_LANGUAGES``/alias-map change, not a scanner change. Coverage is
only reported if the repo already contains a coverage report Sonar knows how
to read (e.g. ``coverage.xml``, ``lcov.info``) — Rotsy never runs a test
suite to produce one.

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

# The compute-engine task id sonar-scanner prints on success, from the line
# "More about the report processing at http://.../api/ce/task?id=AYx1...".
# Anchored to the `api/ce/task` path specifically: a successful run also
# prints an earlier "you can find the results at .../dashboard?id=<project
# key>" line, whose query param is also named `id` — a bare `[?&]id=`
# pattern matches that dashboard link first (`.search` returns the first
# match), silently handing the *project key* to every caller as if it were
# the compute-engine task id. That value isn't rejected until the very next
# API call (`GET /api/ce/task?id=<project key>` 404s: "No activity found for
# task '<project key>'") — a real failure, but from the wrong line entirely.
_TASK_ID_RE = re.compile(r"api/ce/task\?id=([A-Za-z0-9_-]+)")


class ScannerError(Exception):
    pass


class UnsupportedLanguageError(ScannerError):
    pass


def validate_language(language: str) -> None:
    if language not in SUPPORTED_LANGUAGES:
        raise UnsupportedLanguageError(
            f"{language!r} is not analyzable without a build step. "
            f"Supported: {', '.join(SUPPORTED_LANGUAGES)}."
        )


async def run_scanner(
    source_dir: str,
    project_key: str,
    sonar_url: str,
    analysis_token: str,
    branch: str,
    default_branch: str,
) -> str:
    """Run sonar-scanner against ``source_dir``; return the compute-engine task id.

    The task id is what the caller polls via
    ``SonarClient.task_status``/``quality_gate`` — analysis itself finishes
    server-side, asynchronously, after the CLI exits.

    ``sonar.branch.name`` is passed only when ``branch`` differs from
    ``default_branch``. SonarQube Community Edition rejects that property
    outright — "Developer Edition or above is required" — regardless of
    what value it's given, even the repository's own default branch. Every
    push and every manual "Run Analysis" targets the default branch, so
    omitting the property there is what makes Community Edition (Rotsy's
    own reference/dev SonarQube — see docker-compose-sonar/) work at all;
    only an explicit non-default ``ref`` override hits this, and on
    Community Edition it always will — that is a real Sonar licensing limit
    to surface, not a Rotsy bug to route around.
    """
    args = [
        "sonar-scanner",
        f"-Dsonar.projectKey={project_key}",
        f"-Dsonar.sources={source_dir}",
        f"-Dsonar.host.url={sonar_url}",
        f"-Dsonar.token={analysis_token}",
        f"-Dsonar.branch.name={branch}" if branch and branch != default_branch else "",
        "-Dsonar.scm.disabled=true",  # source is a shallow clone; SCM blame data isn't available
    ]
    args = [a for a in args if a]

    returncode, stdout, stderr = await exec_scanner(
        args, env={}, timeout=SCAN_TIMEOUT, cwd=source_dir,
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
