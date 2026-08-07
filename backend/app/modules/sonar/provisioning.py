"""Automatic SonarQube project provisioning + first analysis on connect.

Shared by every source module (GitHub, GitLab, ...) so "connect a repository"
means the same thing regardless of which Git provider it came from: create
the Sonar project if one doesn't exist yet (auto-detecting a supported
language from the provider's own language-breakdown API, no clone required
just to guess), then immediately queue the same ``clone_and_analyze`` job a
push or a manual "Run Analysis" click would use.

Lives in ``modules/sonar`` rather than ``core`` because it talks to
``SonarClient`` directly — core must never import a module. It takes a
:class:`~app.core.source_provider.SourceProvider` and a plain
``credential_ref`` rather than a concrete ``GitHubProvider``/``GitLabProvider``,
so this file never imports ``modules.github`` or ``modules.gitlab`` either
(no module-to-module import).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...core import projects as projects_core
from ...core.cache import Cache
from ...core.config_store import get_sonar_connection
from ...core.jobs import JobQueue
from ...core.source_provider import RepoRef, SourceProvider
from ...models import AnalysisRun, Integration, SonarProject
from ...models.sonar import SUPPORTED_LANGUAGES
from .connector import SonarClient, SonarError

logger = logging.getLogger(__name__)


async def reap_stale_analysis_runs(session: AsyncSession) -> int:
    """Close out ``AnalysisRun`` rows left in ``running`` by a worker that
    went away — same reasoning as :func:`app.modules.nexus.reap_stale_reports`
    for scan reports: a row is written to ``running`` before the clone/scan
    actually starts, so if the worker process dies mid-analysis (a restart,
    a crash) that row sits at ``running`` forever and the project looks
    permanently mid-analysis. The job-queue reaper (``JobQueue.reap_stranded``)
    only fixes the *Redis* job status — this is the separate DB-row status
    the UI actually reads, and nothing else fixes it. Called once at
    startup, DB-only, starts no scans.
    """
    result = await session.execute(
        sql_update(AnalysisRun)
        .where(AnalysisRun.status == "running")
        .values(
            status="failed",
            error="Interrupted: the backend restarted while this analysis was running. Run it again to retry.",
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("Marked %d interrupted analysis run(s) as failed at startup", count)
    return count

# GitHub/GitLab language names -> our lowercase Sonar language keys. Both
# providers' language-breakdown APIs use the human display name ("Python",
# "TypeScript"); everything not in this map is simply not auto-provisionable.
_LANGUAGE_ALIASES = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "php": "php",
    "ruby": "ruby",
    "css": "css",
    "scss": "css",
    "html": "html",
}


def sonar_project_key_for(project_id: int, repo_external_id: str) -> str:
    """Same key format used everywhere a Sonar project key is built — kept
    in one place so automatic and manual provisioning never diverge. Keyed
    by repository, not by Project name: a Project can hold many
    repositories, so the Project's own name can't be part of a *unique*
    per-repository key."""
    slug = repo_external_id.lower().replace("/", "-").replace(" ", "-")
    return f"rotsy-{project_id}-{slug}"


def sonar_branch_project_key(base_key: str, branch: str, default_branch: str) -> str:
    """The Sonar-side project key actually analyzed for ``branch``.

    SonarQube Community Edition has no native multi-branch analysis —
    ``sonar.branch.name`` is rejected outright for anything but the one
    branch already being analyzed with no branch parameter at all (see
    ``scanner.py``). Rather than needing Developer Edition, or limiting
    Rotsy to one branch per repository, every branch other than the
    repository's own default gets its own independent Sonar project: as
    far as Sonar is concerned each is simply "the only branch" of its own
    project, so this works on every edition and scales to however many
    branches get analyzed, with nothing beyond what's already free.

    The default branch keeps using the repository's own base key
    unchanged, so existing default-branch analyses/history are untouched.
    """
    if branch == default_branch:
        return base_key
    slug = branch.lower().replace("/", "-").replace(" ", "-")
    return f"{base_key}--{slug}"


# ---------------------------------------------------------------------------
# Custom Quality Gate — "block on what matters, report everything else"
#
# Sonar's own "Sonar way" default gate fails on *any* new issue regardless of
# severity, which makes it a blunt instrument: a single new minor code smell
# blocks the same as a new critical vulnerability. Rotsy provisions its own
# gate instead, assigned to every project it creates, that only fails on
# genuinely serious regressions — everything else (minor/major issues, code
# smells, technical debt) is still fully detected and reported through
# AnalysisRun's issue counts and Smart Insights, just not gate-blocking.
#
# Conditions, and why each one is here:
#   new_blocker_violations  > 0   any new Blocker-severity issue (bug, vuln, or smell)
#   new_critical_violations > 0   any new Critical-severity issue
#   new_coverage            < 60  new code below 60% covered
#   new_duplicated_lines_density > 10   more than 10% of new code duplicated
#
# These are the legacy severity-based metric keys, kept for broad version
# compatibility across the supported range (MIN_SUPPORTED_MAJOR in
# routers/sonar.py) — if a future SonarQube major removes them in favor of
# the Clean Code taxonomy's impact-based metrics exclusively, this condition
# set needs revisiting, not just re-running.
QUALITY_GATE_NAME = "Rotsy Standard"
_QUALITY_GATE_CONDITIONS: list[tuple[str, str, str]] = [
    ("new_blocker_violations", "GT", "0"),
    ("new_critical_violations", "GT", "0"),
    ("new_coverage", "LT", "60"),
    ("new_duplicated_lines_density", "GT", "10"),
]


async def ensure_quality_gate(client: SonarClient) -> None:
    """Create the "Rotsy Standard" gate if it isn't already there, then
    reconcile its conditions against :data:`_QUALITY_GATE_CONDITIONS` every
    time — not just at creation.

    Reconciliation is the part that matters: SonarQube auto-populates
    ``POST /api/qualitygates/create`` with its own CAYC ("Clean as You Code")
    conditions — ``new_violations>0``, ``new_security_hotspots_reviewed<100``,
    ``new_duplicated_lines_density>3``, ``new_coverage<80`` — regardless of
    what the caller asked for. A one-shot "create + add our conditions" left
    the gate stuck on *Sonar's* defaults, not Rotsy's, if anything about that
    first attempt was ever interrupted (a transient error partway through the
    condition loop, an older code path that didn't add them all) — and
    "already exists, do nothing" on every later call meant nothing ever
    noticed or repaired it. In particular ``new_violations>0`` alone makes
    the gate fail on any single new minor code smell, exactly the "blunt
    instrument" behavior this custom gate exists to avoid (see the module
    comment above) — so it's not just wrong thresholds, it silently defeated
    the entire point of having a custom gate. Idempotent: only touches a
    condition that's actually missing or actually has the wrong threshold.
    """
    existing = await client.get_quality_gate_by_name(QUALITY_GATE_NAME)
    if existing is None:
        await client.create_quality_gate(QUALITY_GATE_NAME)
        logger.info("Created SonarQube quality gate %r.", QUALITY_GATE_NAME)

    current_by_metric = {c["metric"]: c for c in await client.get_quality_gate_conditions(QUALITY_GATE_NAME)}
    intended_metrics = {metric for metric, _, _ in _QUALITY_GATE_CONDITIONS}

    for metric, op, threshold in _QUALITY_GATE_CONDITIONS:
        current = current_by_metric.get(metric)
        if current is None:
            await client.add_quality_gate_condition(QUALITY_GATE_NAME, metric, op, threshold)
        elif current.get("op") != op or current.get("error") != threshold:
            await client.update_quality_gate_condition(current["id"], metric, op, threshold)

    # Conditions Sonar added on its own that aren't part of Rotsy's intended
    # set — left in place they'd gate-block on things Rotsy Standard is
    # explicitly designed not to (see docstring above).
    for metric, condition in current_by_metric.items():
        if metric not in intended_metrics:
            await client.delete_quality_gate_condition(condition["id"])


async def ensure_branch_project(client: SonarClient, project_key: str, name: str) -> None:
    """Create the Sonar-side project for one branch-derived key
    (:func:`sonar_branch_project_key`) if it doesn't exist yet, and assign
    it the default "Rotsy Standard" gate — idempotent, meant to be called
    before every analysis run of a non-default branch, unlike
    :func:`ensure_quality_gate`/the repository's own base-key project,
    which are only ever set up once at connect time (an operator's explicit
    choice of a *different* gate there is never touched again). A
    branch-derived project has no such choice to preserve — it didn't exist
    until this call, so there's nothing to overwrite.
    """
    await client.ensure_project(project_key, name)
    try:
        await ensure_quality_gate(client)
        await client.assign_quality_gate(QUALITY_GATE_NAME, project_key)
    except SonarError:
        logger.warning("Failed to assign the %r quality gate to %s — it will use Sonar's default gate instead.",
                        QUALITY_GATE_NAME, project_key, exc_info=True)


def pick_supported_language(languages: dict[str, float]) -> str | None:
    """Highest-ranked language from a provider's breakdown that Rotsy can
    actually analyze without a build step, or ``None`` if none qualify."""
    ranked = sorted(languages.items(), key=lambda kv: kv[1], reverse=True)
    for name, _ in ranked:
        key = _LANGUAGE_ALIASES.get(name.strip().lower())
        if key in SUPPORTED_LANGUAGES:
            return key
    return None


async def create_sonar_project_row(
    session: AsyncSession, settings: Settings, project_id: int, repo_external_id: str, language: str,
    quality_gate: str | None = None,
    github_repository_id: int | None = None, gitlab_repository_id: int | None = None,
) -> SonarProject:
    """Create the Sonar-side project (idempotent) and the local ``SonarProject``
    row for one repository. Shared by the manual connect endpoint and
    automatic provisioning below — one implementation, not two. Exactly one
    of ``github_repository_id``/``gitlab_repository_id`` should be set.

    ``quality_gate``: name of an *existing* Sonar quality gate to assign —
    including one the operator created or edited directly in SonarQube's own
    UI, since Rotsy never locks the project into a Rotsy-managed gate.
    Omitted (the default) means "Rotsy Standard" (see
    :data:`QUALITY_GATE_NAME`), created once per instance if it doesn't
    already exist. Either way this only runs at project-creation time —
    nothing here re-asserts a gate on later analyses, so whatever the
    operator does to the gate afterward (in Sonar directly) sticks.
    """
    conn = await get_sonar_connection(session, settings)
    if not conn.is_configured():
        raise SonarError("SonarQube is not configured")

    sonar_project_key = sonar_project_key_for(project_id, repo_external_id)
    client = SonarClient(conn.url, conn.token)
    await client.ensure_project(sonar_project_key, repo_external_id)

    if quality_gate:
        # An explicit choice — a typo here should surface as an error, not
        # silently fall back to whatever Sonar assigns by default.
        existing_gate = await client.get_quality_gate_by_name(quality_gate)
        if existing_gate is None:
            raise SonarError(f"Quality gate {quality_gate!r} does not exist on this SonarQube instance.")
        await client.assign_quality_gate(quality_gate, sonar_project_key)
    else:
        try:
            await ensure_quality_gate(client)
            await client.assign_quality_gate(QUALITY_GATE_NAME, sonar_project_key)
        except SonarError:
            # Non-fatal only for the default path: the project still gets
            # analyzed under whatever gate Sonar assigns by default (its own
            # "Sonar way"). Logged, not swallowed silently, since it means
            # gate results won't match what Rotsy's UI describes.
            logger.warning("Failed to assign the %r quality gate to %s — it will use Sonar's default gate instead.",
                            QUALITY_GATE_NAME, sonar_project_key, exc_info=True)

    # The project-scoped "sonar" Integration row is a marker that *some*
    # repository under this Project has Sonar analysis. Checked directly
    # against Integration rather than gating on "does a SonarProject already
    # exist" (which used to be the same question when there was one
    # SonarProject per Project): a SonarProject can now disappear on its own
    # — dropped by the 20260811 orphaned-row cleanup, or cascade-deleted with
    # its repository — while the Integration marker survives, since nothing
    # links their lifetimes together. Re-deriving "should I create the
    # marker" from SonarProject existence in that state called
    # ``connect_integration`` again, which 409s on the marker that's still
    # there — permanently blocking every future connection attempt (and
    # every ``run-analysis`` call, since no SonarProject ever got created).
    # Checking Integration itself makes this idempotent regardless of why a
    # SonarProject went away.
    existing_integration = await session.scalar(
        select(Integration).where(Integration.project_id == project_id, Integration.module_key == "sonar")
    )

    row = SonarProject(
        project_id=project_id, sonar_project_key=sonar_project_key, language=language,
        github_repository_id=github_repository_id, gitlab_repository_id=gitlab_repository_id,
    )
    session.add(row)

    if existing_integration is None:
        await projects_core.connect_integration(
            session, project_id, "sonar", "analysis_engine", config={}, credential_ref=None,
        )
    await session.commit()
    await session.refresh(row)
    return row


async def auto_provision_and_analyze(
    session: AsyncSession,
    cache: Cache,
    settings: Settings,
    project_id: int,
    provider: SourceProvider,
    credential_ref: str,
    repo: RepoRef,
    source_module: str,
    trigger: str = "connect",
    github_repository_id: int | None = None,
    gitlab_repository_id: int | None = None,
) -> None:
    """Called right after a repository is mapped to a Project. Best-effort
    and silent-but-logged on any failure — connecting a repository must never
    fail the mapping request itself just because Sonar isn't reachable yet or
    the language can't be guessed; the operator can still connect Sonar and
    run analysis manually from the project page afterward.

    Exactly one of ``github_repository_id``/``gitlab_repository_id`` must be
    set — this identifies *which repository* under the Project is being
    provisioned, since a Project can hold many.
    """
    if not github_repository_id and not gitlab_repository_id:
        raise ValueError("auto_provision_and_analyze requires a github_repository_id or gitlab_repository_id")

    existing = await session.scalar(
        select(SonarProject).where(
            SonarProject.github_repository_id == github_repository_id
            if github_repository_id else SonarProject.gitlab_repository_id == gitlab_repository_id
        )
    )
    if existing is None:
        try:
            languages = await provider.get_repository_languages(credential_ref, repo)
        except Exception:  # noqa: BLE001
            logger.warning("Could not fetch language breakdown for %s; skipping automatic Sonar provisioning.",
                            repo.external_id, exc_info=True)
            return

        language = pick_supported_language(languages)
        if language is None:
            logger.info(
                "No auto-detectable supported language (%s) for %s — Sonar project not created "
                "automatically. Connect it manually from the project page once ready.",
                ", ".join(SUPPORTED_LANGUAGES), repo.external_id,
            )
            return

        try:
            await create_sonar_project_row(
                session, settings, project_id, repo.external_id, language,
                github_repository_id=github_repository_id, gitlab_repository_id=gitlab_repository_id,
            )
        except SonarError:
            logger.info(
                "SonarQube is not configured yet — %s was connected but its Sonar project was not "
                "created automatically. Configure SonarQube in Settings, then it will be created on "
                "the next push or manual analysis.", repo.external_id,
            )
            return

    try:
        sha = await provider.get_latest_commit_sha(credential_ref, repo, repo.default_branch)
    except Exception:  # noqa: BLE001
        logger.warning("Could not resolve the latest commit on %s@%s; skipping the automatic first analysis.",
                        repo.external_id, repo.default_branch, exc_info=True)
        return

    queue = JobQueue(cache)
    await queue.enqueue("clone_and_analyze", {
        "project_id": project_id,
        "source_module": source_module,
        "credential_ref": credential_ref,
        "repo_external_id": repo.external_id,
        "repo_name": repo.name,
        "default_branch": repo.default_branch,
        "ref": repo.default_branch,
        "sha": sha,
        "trigger": trigger,
        "github_repository_id": github_repository_id,
        "gitlab_repository_id": gitlab_repository_id,
    })
