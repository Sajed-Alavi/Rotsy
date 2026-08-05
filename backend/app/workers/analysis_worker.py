"""``clone_and_analyze`` job: the push -> Sonar -> dashboard loop.

Triggered by ``routers/github.py``'s webhook receiver. Clones the pushed
commit via the mapped repo's :class:`GitHubProvider`, runs sonar-scanner
against it, polls the resulting compute-engine task, and persists an
``AnalysisRun`` + ``QualityGateResult``. On completion (success or failure)
it reports a commit status back to GitHub and enqueues insight generation.

Runs outside any request scope, like every other handler in
``app.services.job_handlers`` — it owns its own DB session and reads shared
resources (settings, cache) from the lifespan-populated dict rather than
FastAPI dependency injection.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select

from ..config import Settings
from ..core.cache import Cache
from ..core.config_store import get_sonar_connection
from ..core.insights import evaluate_and_store
from ..core.jobs import Job
from ..core.source_provider import RepoRef
from ..db.session import get_session_factory
from ..models import AnalysisRun, GitHubInstallation, GitHubRepository, QualityGateResult, SonarProject
from ..modules.github.provider import GitHubProvider
from ..modules.sonar.connector import SonarClient, SonarError
from ..modules.sonar.quality_gates import (
    QualityGateFailedError,
    QualityGateTimeoutError,
    fetch_quality_gate,
    wait_for_analysis,
)
from ..modules.sonar.scanner import ScannerError, UnsupportedLanguageError, run_scanner, validate_language
from ..state import lifespan_handles as _lifespan_state

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., Awaitable[None]]


class _LifespanStateMissing(RuntimeError):
    pass


def _settings_and_cache() -> tuple[Settings, Cache]:
    settings = _lifespan_state.get("settings")
    cache = _lifespan_state.get("cache")
    if settings is None or cache is None:
        raise _LifespanStateMissing("lifespan state not initialised yet")
    return settings, cache


_MEASURE_KEYS = ["bugs", "vulnerabilities", "code_smells", "security_hotspots", "coverage", "duplicated_lines_density"]


async def handle_clone_and_analyze(job: Job, progress: ProgressCallback) -> dict:
    """The one and only analysis workflow — push-triggered and manual runs
    both enqueue this same job type with the same payload shape (``trigger``
    is the only thing that differs), so there is exactly one implementation
    to reason about, test, and fix."""
    payload = job.payload
    project_id = payload["project_id"]
    github_repository_id = payload["github_repository_id"]
    repo_full_name = payload["repo_full_name"]
    ref = payload["ref"]
    sha = payload["sha"]
    trigger = payload.get("trigger", "push")

    settings, cache = _settings_and_cache()
    factory = get_session_factory()

    await progress(2, "queued")
    async with factory() as session:
        github_repo = await session.get(GitHubRepository, github_repository_id)
        if github_repo is None:
            raise ValueError(f"GitHubRepository {github_repository_id} not found")
        installation = await session.get(GitHubInstallation, github_repo.installation_id)
        if installation is None:
            raise ValueError(f"GitHubInstallation for repo {github_repository_id} not found")

        sonar_project = await session.scalar(
            select(SonarProject).where(SonarProject.project_id == project_id)
        )
        if sonar_project is None:
            raise ValueError(
                f"project {project_id} has no Sonar project configured — "
                "connect one via POST /api/modules/sonar/projects before pushing"
            )
        validate_language(sonar_project.language)

        sonar_conn = await get_sonar_connection(session, settings)
        if not sonar_conn.is_configured():
            raise SonarError(
                "SonarQube is not configured. Set it up in Settings -> Integrations -> SonarQube."
            )

        # A manual re-run (or a retried webhook delivery that slipped past the
        # cache-based dedupe in routers/github.py) can target a commit that
        # already has a run — (sonar_project_id, commit_sha) is unique, so
        # reuse and reset that row instead of a second INSERT hitting the
        # constraint.
        run = await session.scalar(
            select(AnalysisRun).where(
                AnalysisRun.sonar_project_id == sonar_project.id, AnalysisRun.commit_sha == sha,
            )
        )
        if run is None:
            run = AnalysisRun(sonar_project_id=sonar_project.id, commit_sha=sha, ref=ref,
                               status="running", trigger=trigger)
            session.add(run)
        else:
            run.ref = ref
            run.status = "running"
            run.trigger = trigger
            run.issues_count = run.bugs = run.vulnerabilities = run.code_smells = run.security_hotspots = None
            run.coverage = run.duplication_pct = None
            run.finished_at = None
            run.error = None
        await session.commit()
        await session.refresh(run)

    provider = GitHubProvider(settings, cache)
    repo_ref = RepoRef(external_id=repo_full_name, name=repo_full_name.split("/")[-1],
                        default_branch=ref, private=True)

    await progress(10, "cloning repository")
    source_dir = tempfile.mkdtemp(prefix="rotsy-analysis-")
    try:
        await provider.fetch_source(str(installation.installation_id), repo_ref, ref, source_dir)

        await progress(30, "scanner started")
        sonar = SonarClient(sonar_conn.url, sonar_conn.token)
        analysis_token = await sonar.issue_analysis_token(sonar_project.sonar_project_key)

        await progress(40, "uploading analysis")
        task_id = await run_scanner(
            source_dir, sonar_project.sonar_project_key, sonar_conn.url, analysis_token, ref,
        )

        await progress(60, "waiting for quality gate")
        await wait_for_analysis(sonar, task_id)

        await progress(80, "collecting results")
        gate = await fetch_quality_gate(sonar, sonar_project.sonar_project_key)
        measures = await sonar.measures(sonar_project.sonar_project_key, _MEASURE_KEYS)

        bugs = int(measures.get("bugs", 0))
        vulnerabilities = int(measures.get("vulnerabilities", 0))
        code_smells = int(measures.get("code_smells", 0))
        security_hotspots = int(measures.get("security_hotspots", 0))
        issues_count = bugs + vulnerabilities + code_smells
        coverage = measures.get("coverage")
        duplication_pct = measures.get("duplicated_lines_density")
        gate_status = gate.get("status", "ERROR")

        async with factory() as session:
            run = await session.get(AnalysisRun, run.id)
            run.status = "success"
            run.issues_count = issues_count
            run.bugs = bugs
            run.vulnerabilities = vulnerabilities
            run.code_smells = code_smells
            run.security_hotspots = security_hotspots
            run.coverage = coverage
            run.duplication_pct = duplication_pct
            run.finished_at = datetime.now(timezone.utc)
            # A re-run (manual retry on the same commit) reuses the AnalysisRun
            # row rather than inserting a second one (unique on project+sha) —
            # drop any prior QualityGateResult for it first so exactly one
            # current result exists per run, not a growing history of stale ones.
            await session.execute(
                QualityGateResult.__table__.delete().where(QualityGateResult.analysis_run_id == run.id)
            )
            session.add(QualityGateResult(
                analysis_run_id=run.id, status=gate_status, conditions=gate.get("conditions", []),
            ))
            await session.commit()
            await session.refresh(run)

            await progress(90, "generating insights")
            insights = await evaluate_and_store(session, project_id, run, gate_status)
            if insights:
                logger.info("Generated %d insight(s) for project %s commit %s",
                             len(insights), project_id, sha[:8])

        await progress(95, "updating GitHub status")
        await provider.report_status(
            str(installation.installation_id), repo_ref, sha,
            state="success" if gate_status == "OK" else "failure",
            description=f"Quality gate {gate_status.lower()} — {issues_count} issues, coverage {coverage or 0:.0f}%",
            target_url="",
        )
        await progress(100, f"completed — quality gate {gate_status}")
        return {"analysis_run_id": run.id, "quality_gate": gate_status, "issues_count": issues_count}

    except (ScannerError, UnsupportedLanguageError, SonarError,
            QualityGateFailedError, QualityGateTimeoutError) as exc:
        await _mark_failed(factory, run.id, str(exc))
        try:
            await provider.report_status(
                str(installation.installation_id), repo_ref, sha,
                state="error", description=str(exc)[:140], target_url="",
            )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to report failure status back to GitHub for %s@%s", repo_full_name, sha)
        raise
    finally:
        shutil.rmtree(source_dir, ignore_errors=True)


async def _mark_failed(factory, run_id: int, error: str) -> None:
    async with factory() as session:
        run = await session.get(AnalysisRun, run_id)
        if run is not None:
            run.status = "failed"
            run.error = error[:2000]
            await session.commit()
