"""``clone_and_analyze`` job: the push -> Sonar -> dashboard loop.

Provider-agnostic: the payload identifies a repository by
``source_module``/``credential_ref``/``repo_external_id`` rather than a
GitHub-specific row id, so this one handler serves GitHub pushes, GitLab
pushes, manual "Run Analysis" clicks, and automatic on-connect analysis alike
— there is exactly one analysis implementation, not one per source module.

Clones the pushed commit via whichever :class:`SourceProvider` matches
``source_module``, runs sonar-scanner against it, polls the resulting
compute-engine task, and persists an ``AnalysisRun`` + ``QualityGateResult``.
On completion (success or failure) it reports a commit status back to the
source provider and generates Smart Insights.

Runs outside any request scope, like every other handler in
``app.services.job_handlers`` — it owns its own DB session(s) and reads
shared resources (settings, cache) from the lifespan-populated dict rather
than FastAPI dependency injection.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core.cache import Cache
from ..core.config_store import get_github_app_config, get_sonar_connection
from ..core.insights import evaluate_and_store
from ..core.jobs import Job
from ..core.source_provider import RepoRef, SourceProvider
from ..db.session import get_session_factory
from ..models import AnalysisRun, QualityGateResult, SonarProject
from ..modules.github.provider import GitHubProvider
from ..modules.gitlab.provider import GitLabProvider
from ..modules.sonar.connector import SonarClient, SonarError
from ..modules.sonar.findings import sync_findings
from ..modules.sonar.provisioning import ensure_branch_project, sonar_branch_project_key
from ..modules.sonar.quality_gates import fetch_quality_gate, wait_for_analysis
from ..modules.sonar.scanner import run_scanner, validate_language
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


async def _build_provider(source_module: str, session: AsyncSession, settings: Settings, cache: Cache) -> SourceProvider:
    if source_module == "github":
        app_config = await get_github_app_config(session, settings)
        return GitHubProvider(app_config, cache)
    if source_module == "gitlab":
        return GitLabProvider(get_session_factory())
    raise ValueError(f"Unknown source_module {source_module!r}")


_MEASURE_KEYS = ["bugs", "vulnerabilities", "code_smells", "security_hotspots", "coverage", "duplicated_lines_density"]


async def _resolve_sonar_project(
    session: AsyncSession, *, github_repository_id: int | None, gitlab_repository_id: int | None,
    repo_external_id: str,
) -> SonarProject:
    """A Project can hold many repositories, each with its own SonarProject —
    looked up by *which repository* this job is for, never just by
    project_id (that would pick an arbitrary repo's analysis under the same
    Project)."""
    if github_repository_id:
        sonar_project = await session.scalar(
            select(SonarProject).where(SonarProject.github_repository_id == github_repository_id)
        )
    elif gitlab_repository_id:
        sonar_project = await session.scalar(
            select(SonarProject).where(SonarProject.gitlab_repository_id == gitlab_repository_id)
        )
    else:
        raise ValueError("clone_and_analyze payload is missing github_repository_id/gitlab_repository_id")

    if sonar_project is None:
        raise ValueError(
            f"{repo_external_id} has no Sonar project configured — "
            "connect one from the Project page before pushing"
        )
    validate_language(sonar_project.language)
    return sonar_project


async def _get_or_create_run(
    session: AsyncSession, *, sonar_project_id: int, sha: str, ref: str, trigger: str,
) -> AnalysisRun:
    """(sonar_project_id, commit_sha) is unique — a manual re-run (or a
    retried webhook delivery that slipped past the cache-based dedupe in the
    webhook receiver) can target a commit that already has a run, so this
    reuses and resets that row instead of a second INSERT hitting the
    constraint."""
    run = await session.scalar(
        select(AnalysisRun).where(
            AnalysisRun.sonar_project_id == sonar_project_id, AnalysisRun.commit_sha == sha,
        )
    )
    if run is None:
        run = AnalysisRun(sonar_project_id=sonar_project_id, commit_sha=sha, ref=ref,
                           status="running", trigger=trigger)
        session.add(run)
    else:
        run.ref = ref
        run.status = "running"
        run.trigger = trigger
        run.issues_count = run.bugs = run.vulnerabilities = run.code_smells = run.security_hotspots = None
        run.coverage = run.duplication_pct = None
        # Reset alongside everything else above: `started_at` only gets its
        # DB-side default on the INSERT path. Left alone here, a re-run of
        # the same commit kept the *original* run's start time while
        # `finished_at` moved to whenever this re-run actually finished —
        # every re-run made "duration" (computed in the UI as finished_at -
        # started_at) larger and more wrong, not just stale.
        run.started_at = datetime.now(timezone.utc)
        run.finished_at = None
        run.error = None
    return run


async def _store_findings_best_effort(
    session: AsyncSession, sonar: SonarClient, sonar_project_key: str, run_id: int,
) -> None:
    """Best-effort, like ensure_quality_gate in provisioning.py: the gate
    result and aggregate measures are recorded by the caller and are what
    "analysis succeeded" means. Per-issue detail is supplementary — a hiccup
    fetching it (a Sonar edition without the hotspots endpoint, a transient
    timeout on a huge issue list) must not undo an otherwise-successful run."""
    try:
        issue_count, hotspot_count = await sync_findings(session, sonar, sonar_project_key, run_id)
        logger.info("Stored %d issue(s) and %d hotspot(s) for analysis run %s",
                    issue_count, hotspot_count, run_id)
    except SonarError:
        logger.warning("Failed to fetch issues/hotspots for analysis run %s — "
                        "aggregate counts are still recorded.", run_id, exc_info=True)


async def _report_failure_status(
    provider: SourceProvider, credential_ref: str, repo_ref: RepoRef, sha: str, exc: Exception,
    source_module: str, repo_external_id: str,
) -> None:
    try:
        await provider.report_status(
            credential_ref, repo_ref, sha,
            state="error", description=str(exc)[:140], target_url="",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to report failure status back to %s for %s@%s",
                        source_module, repo_external_id, sha)


async def handle_clone_and_analyze(job: Job, progress: ProgressCallback) -> dict:
    """The one and only analysis workflow — push-triggered, manual, and
    automatic on-connect runs all enqueue this same job type with the same
    payload shape (``trigger`` is the only thing that differs), so there is
    exactly one implementation to reason about, test, and fix."""
    payload = job.payload
    project_id = payload["project_id"]
    source_module = payload["source_module"]
    credential_ref = payload["credential_ref"]
    repo_external_id = payload["repo_external_id"]
    repo_name = payload.get("repo_name") or repo_external_id.rsplit("/", 1)[-1]
    ref = payload["ref"]
    # Every enqueuer (routers/sonar.py, modules/sonar/provisioning.py) sets
    # this; falling back to `ref` only guards a payload from an older worker
    # version still sitting in the queue across a deploy.
    default_branch = payload.get("default_branch") or ref
    sha = payload["sha"]
    trigger = payload.get("trigger", "push")
    github_repository_id = payload.get("github_repository_id")
    gitlab_repository_id = payload.get("gitlab_repository_id")

    settings, cache = _settings_and_cache()
    factory = get_session_factory()
    repo_ref = RepoRef(external_id=repo_external_id, name=repo_name, default_branch=ref, private=True)

    await progress(2, "queued")
    async with factory() as session:
        provider = await _build_provider(source_module, session, settings, cache)
        sonar_project = await _resolve_sonar_project(
            session, github_repository_id=github_repository_id, gitlab_repository_id=gitlab_repository_id,
            repo_external_id=repo_external_id,
        )

        sonar_conn = await get_sonar_connection(session, settings)
        if not sonar_conn.is_configured():
            raise SonarError(
                "SonarQube is not configured. Set it up in Settings -> Integrations -> SonarQube."
            )

        run = await _get_or_create_run(
            session, sonar_project_id=sonar_project.id, sha=sha, ref=ref, trigger=trigger,
        )
        await session.commit()
        await session.refresh(run)

    await progress(10, "cloning repository")
    source_dir = tempfile.mkdtemp(prefix="rotsy-analysis-")
    try:
        await provider.fetch_source(credential_ref, repo_ref, ref, source_dir)

        await progress(30, "scanner started")
        sonar = SonarClient(sonar_conn.url, sonar_conn.token)
        # SonarQube Community Edition has no native multi-branch analysis
        # (see scanner.py) — every branch other than the repository's own
        # default gets its own independent Sonar project instead, created
        # here on first use. The default branch keeps the repo's own base
        # key, already provisioned at connect time — nothing new to create.
        sonar_project_key = sonar_branch_project_key(sonar_project.sonar_project_key, ref, default_branch)
        if sonar_project_key != sonar_project.sonar_project_key:
            await ensure_branch_project(sonar, sonar_project_key, f"{repo_external_id} ({ref})")
        analysis_token = await sonar.issue_analysis_token(sonar_project_key)

        await progress(40, "uploading analysis")
        task_id = await run_scanner(source_dir, sonar_project_key, sonar_conn.url, analysis_token)

        await progress(60, "waiting for quality gate")
        await wait_for_analysis(sonar, task_id)

        await progress(80, "collecting results")
        gate = await fetch_quality_gate(sonar, sonar_project_key)
        measures = await sonar.measures(sonar_project_key, _MEASURE_KEYS)

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

            await progress(85, "collecting issues and hotspots")
            await _store_findings_best_effort(session, sonar, sonar_project_key, run.id)

            await session.commit()
            await session.refresh(run)

            await progress(90, "generating insights")
            insights = await evaluate_and_store(session, project_id, run, gate_status)
            if insights:
                logger.info("Generated %d insight(s) for project %s commit %s",
                             len(insights), project_id, sha[:8])

        await progress(95, "updating commit status")
        await provider.report_status(
            credential_ref, repo_ref, sha,
            state="success" if gate_status == "OK" else "failure",
            description=f"Quality gate {gate_status.lower()} — {issues_count} issues, coverage {coverage or 0:.0f}%",
            target_url="",
        )
        await progress(100, f"completed — quality gate {gate_status}")
        try:
            await _notify_analysis_success(
                project_id=project_id, run=run, sonar_project=sonar_project, repo_name=repo_name, ref=ref,
                gate_status=gate_status, issues_count=issues_count, bugs=bugs,
                vulnerabilities=vulnerabilities, code_smells=code_smells, coverage=coverage,
            )
        except Exception:  # noqa: BLE001 - Telegram is a bonus delivery channel, never a reason to fail a completed analysis
            logger.exception("Telegram notify failed for analysis run %s", run.id)
        return {"analysis_run_id": run.id, "quality_gate": gate_status, "issues_count": issues_count}

    except Exception as exc:  # noqa: BLE001
        # Deliberately catches everything, not just the small set of
        # expected Sonar/scanner exceptions this used to list by name: this
        # is a background job with no caller waiting to see a traceback, so
        # any exception outside that narrower list used to skip
        # _mark_failed entirely and leave the AnalysisRun stuck at "running"
        # forever — indistinguishable in the UI from a job that's still
        # genuinely in progress. A bug here (e.g. a missing binary a
        # dependency change forgot to install into the image) should surface
        # as a failed run with a real error message, not a silent hang.
        logger.exception("clone_and_analyze failed for analysis run %s (%s@%s): %s",
                          run.id, repo_external_id, sha, exc)
        await _mark_failed(factory, run.id, str(exc) or f"{type(exc).__name__}: analysis failed unexpectedly.")
        await _report_failure_status(provider, credential_ref, repo_ref, sha, exc, source_module, repo_external_id)
        try:
            await _notify_analysis_failure(project_id=project_id, repo_name=repo_name, ref=ref, error=str(exc))
        except Exception:  # noqa: BLE001 - same reasoning as the success-path notify above
            logger.exception("Telegram notify failed for analysis run %s", run.id)
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


async def _notify_analysis_success(
    *, project_id: int, run: AnalysisRun, sonar_project: SonarProject, repo_name: str, ref: str,
    gate_status: str, issues_count: int, bugs: int, vulnerabilities: int, code_smells: int,
    coverage: float | None,
) -> None:
    """Ship the same PDF report ``GET .../report.pdf`` produces to every
    linked user with access to this project, as soon as the run finishes —
    best-effort, see notify.py's own docstring."""
    from ..modules.telegram.client import escape_html
    from ..modules.telegram.notify import notify_project
    from ..services.sonar_report_pdf import build_analysis_report_pdf

    async def _render_pdf() -> bytes:
        # Passed as a callable rather than pre-rendered bytes: building this
        # report queries every issue and hotspot of the run with no limit and
        # renders a multi-page document, and the overwhelmingly common case
        # is a deployment with no bot token configured at all. notify_project
        # only invokes this once it has at least one real recipient.
        factory = get_session_factory()
        async with factory() as session:
            return await build_analysis_report_pdf(session, run, sonar_project)

    icon = "✅" if gate_status == "OK" else "⚠️"
    text = (
        f"{icon} <b>Analysis complete</b> — {escape_html(repo_name)} ({escape_html(ref)})\n"
        f"Quality gate: {escape_html(gate_status)}\n"
        f"Issues: {issues_count} (bugs {bugs}, vulnerabilities {vulnerabilities}, code smells {code_smells})\n"
        f"Coverage: {f'{coverage:.0f}%' if coverage is not None else 'n/a'}"
    )
    await notify_project(
        project_id, text, pdf_factory=_render_pdf, filename=f"sonar-{run.commit_sha[:8]}.pdf",
    )


async def _notify_analysis_failure(*, project_id: int, repo_name: str, ref: str, error: str) -> None:
    from ..modules.telegram.client import escape_html
    from ..modules.telegram.notify import notify_project

    text = (
        f"❌ <b>Analysis failed</b> — {escape_html(repo_name)} ({escape_html(ref)})\n"
        f"{escape_html(error[:500])}"
    )
    await notify_project(project_id, text)
