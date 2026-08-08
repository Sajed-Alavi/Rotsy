"""Best-effort test coverage generation for a Python repository being analyzed.

Rotsy never *requires* a coverage report — SonarQube reads one if it exists,
otherwise coverage stays unmeasured (see ``scanner.py``). This module makes
one real one when it reasonably can, instead of leaving coverage at 0% for
every Python repository just because Rotsy itself never ran the tests: if the
cloned repo has a ``pytest.ini`` (or ``pyproject.toml``/``setup.cfg`` with a
``[tool:pytest]``/``[pytest]`` section), its own test suite is run with
coverage, producing a ``coverage.xml`` alongside the source for
``sonar.python.coverage.reportPaths`` to pick up.

This does mean executing code from the repository being analyzed — a
deliberate, narrow exception to "static analysis only", which is a rule about
never running a *container image* pulled from a registry (untrusted binary
data), not about a repository the operator explicitly connected and asked
Rotsy to analyze. Running that repository's own declared test suite is
exactly what every CI coverage tool does; there is no way to measure real
coverage without it.

Any failure here — no test config found, install failure, tests failing,
timeout — just means no coverage report gets passed to sonar-scanner, exactly
as if Rotsy had never attempted this. It never fails or blocks the analysis
itself.
"""

from __future__ import annotations

import logging
import os

from ..nexus.base import exec_scanner

logger = logging.getLogger(__name__)

COVERAGE_TIMEOUT = 300.0  # generous, but bounded — a slow/hanging test suite must not stall analysis

_PYTEST_INI_NAMES = ("pytest.ini",)
_PYTEST_CONFIG_MARKERS = ("pyproject.toml", "setup.cfg", "tox.ini")


def _find_pytest_config(source_dir: str) -> str | None:
    """The first ``pytest.ini`` (or a ``pyproject.toml``/``setup.cfg``/``tox.ini``
    that plausibly configures pytest) found under ``source_dir``, shallowest
    first — a repo's real test config is never buried several directories
    deep in something unrelated, and stopping at the first match avoids
    picking up a vendored/example project's own pytest config instead."""
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and d not in ("node_modules", "venv", ".venv")]
        for name in _PYTEST_INI_NAMES:
            if name in files:
                return os.path.join(root, name)
        for name in _PYTEST_CONFIG_MARKERS:
            path = os.path.join(root, name)
            if name in files and _looks_like_pytest_config(path):
                return path
        # Breadth-first-ish: don't descend arbitrarily deep hunting for config.
        if root.count(os.sep) - source_dir.count(os.sep) >= 3:
            dirs[:] = []
    return None


def _looks_like_pytest_config(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return False
    return "[tool:pytest]" in content or "[pytest]" in content or "[tool.pytest.ini_options]" in content


def _cov_target(source_dir: str, config_dir: str) -> str:
    """What to pass to ``--cov=``, relative to ``source_dir`` (so coverage.xml's
    file paths line up with ``sonar.sources``): the config directory's own
    ``app`` package if one exists there (this repo's own layout, and a common
    one), else the config directory itself."""
    app_dir = os.path.join(config_dir, "app")
    target = app_dir if os.path.isdir(app_dir) else config_dir
    return os.path.relpath(target, source_dir)


async def generate_python_coverage(source_dir: str) -> str | None:
    """Run the repo's own pytest suite with coverage if it has one.

    Returns the coverage report's path relative to ``source_dir`` (what
    ``run_scanner`` needs for ``sonar.python.coverage.reportPaths``), or
    ``None`` if there's nothing to run or the run didn't produce a report.
    """
    config_path = _find_pytest_config(source_dir)
    if config_path is None:
        return None
    config_dir = os.path.dirname(config_path)
    rel_config = os.path.relpath(config_path, source_dir)
    cov_target = _cov_target(source_dir, config_dir)
    report_rel_path = "coverage.xml"

    args = [
        "python3", "-m", "pytest",
        "-c", rel_config,
        f"--cov={cov_target}",
        f"--cov-report=xml:{report_rel_path}",
        "-q", "--no-header",
    ]
    try:
        returncode, stdout, stderr = await exec_scanner(
            args, env={}, timeout=COVERAGE_TIMEOUT, cwd=source_dir,
        )
    except OSError:  # TimeoutError is a subclass of OSError
        logger.warning("Coverage generation timed out or failed to start for %s", source_dir, exc_info=True)
        return None

    report_abs_path = os.path.join(source_dir, report_rel_path)
    if not os.path.exists(report_abs_path):
        # Not necessarily a problem worth surfacing to the caller — a repo's
        # tests can legitimately fail (pytest still writes the XML for
        # whatever ran before a failure in many cases, but not always) or the
        # suite may need dependencies/services Rotsy doesn't have. Either way
        # analysis proceeds without coverage, same as before this existed.
        logger.info("No coverage.xml produced for %s (exit %d): %s",
                    source_dir, returncode, (stderr or stdout)[-500:])
        return None
    return report_rel_path
