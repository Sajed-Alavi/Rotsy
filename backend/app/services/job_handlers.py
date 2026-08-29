"""Job handler implementations registered with the JobRunner.

Each handler matches a job ``type`` and receives ``(job, progress)``. Handlers
own their own DB session (from :mod:`app.db.session`) since they run outside a
request scope, and report progress so the UI can stream it over SSE.

Registered job types (see :func:`app.main.lifespan` for the registration):

  * ``collect_metrics``      — snapshot every repo, persist samples, evaluate alerts
  * ``analyze_repo``         — deep storage analysis of one repository
  * ``run_retention``        — execute retention policies (dry-run capable)
  * ``backup``               — trigger a Nexus backup task
  * ``backup_archive``       — real byte-level backup (full or selective) to the backup volume
  * ``run_scheduled_backup`` — same, triggered by a BackupSchedule, then pruned to its retention rule
  * ``sync``                 — copy components to another Nexus
  * ``scan_image``           — statically scan one image with Trivy/Grype
  * ``scanner_db_update``    — refresh the vulnerability databases (network)
  * ``scanner_db_import``    — install them from offline archives (no network)
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ..config import get_settings
from ..core import config_store
from ..core.jobs import Job
from ..core.outbound import OutboundURLError, validate_outbound_url
from ..db.session import get_session_factory
from ..state import lifespan_handles as _lifespan_state  # populated at startup
from .alerting import evaluate_alerts
from .backup import trigger_backup
from .backup_archive import create_archive
from .metrics_collector import collect_blobstore_metrics, collect_once, latest_blobstore_snapshot, latest_snapshot
from .retention import run_all_enabled, run_policy
from ..modules.nexus import Credentials, scan_image
from ..modules.nexus import db as scanner_db
from ..modules.nexus import events as scan_events
from ..modules.nexus import registry as registry_discovery
from .sync import sync_repository

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], Awaitable[None]]

_NEXUS_CLIENT_NOT_AVAILABLE = "Nexus client not available"


class _LifespanStateMissing(RuntimeError):
    pass


def _shared_state() -> dict:
    """Return the lifespan-populated dict holding the nexus client + cache.

    ``main.py`` sets ``_lifespan_state`` to the live AppState-bearing dict at
    startup; this indirection avoids a hard import cycle.
    """
    state = _lifespan_state.get("state")
    if state is None:
        raise _LifespanStateMissing("lifespan state not initialised yet")
    return state


async def handle_collect_metrics(job: Job, progress: ProgressCallback) -> dict:
    """Collect a metric snapshot for every repo and evaluate alert rules."""
    del job  # every JobHandler takes (job, progress); this one needs no payload
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError(_NEXUS_CLIENT_NOT_AVAILABLE)

    factory = get_session_factory()
    async with factory() as session:
        summary = await collect_once(
            nexus, session, on_progress=progress, retention_days=state.retention_days
        )
        blobstore_summary = await collect_blobstore_metrics(nexus, session)
        # Re-read the latest snapshots so alerting sees fresh values.
        snapshot = await latest_snapshot(session)
        blobstore_snapshot = await latest_blobstore_snapshot(session)
        fired = await evaluate_alerts(session, snapshot, blobstore_snapshot)
    return {**summary, **blobstore_summary, "alerts_fired": fired}


async def handle_analyze_repo(job: Job, progress: ProgressCallback) -> dict:
    """Deep-analyze a single repo (used by 'analyze all' fan-out, or on-demand)."""
    from .storage_analyzer import StorageAnalyzer

    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError(_NEXUS_CLIENT_NOT_AVAILABLE)
    repo = job.payload.get("repo")
    if not repo:
        raise ValueError("payload.repo is required")

    analyzer = StorageAnalyzer(nexus, max_concurrency=state.max_concurrency)

    async def on_progress(event: dict) -> None:
        percent = event.get("percent") or 0
        message = event.get("message") or event.get("phase") or "working"
        await progress(min(99, percent), message)

    result = await analyzer.analyze_repo(repo, on_progress=on_progress)

    # Persist the result in the analyzer cache so GET /storage/{repo}/result works.
    if state.cache is not None:
        await state.cache.set_json(f"analysis:{repo}", result)
    return {"repo": repo, "stats": result["stats"]}


async def handle_run_retention(job: Job, progress: ProgressCallback) -> dict:
    """Run retention policies. Supports dry_run and a single policy id."""
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError(_NEXUS_CLIENT_NOT_AVAILABLE)
    dry_run = bool(job.payload.get("dry_run", False))
    policy_id = job.payload.get("policy_id")

    factory = get_session_factory()
    async with factory() as session:
        if policy_id is not None:
            from ..models import RetentionPolicy
            policy = await session.get(RetentionPolicy, int(policy_id))
            if policy is None:
                raise ValueError(f"Retention policy {policy_id} not found")
            return await run_policy(nexus, session, policy, dry_run=dry_run, on_progress=progress)
        results = await run_all_enabled(nexus, session, dry_run=dry_run, on_progress=progress)
    return {"policies_run": len(results), "results": results, "dry_run": dry_run}


async def handle_backup(job: Job, progress: ProgressCallback) -> dict:
    """Trigger a Nexus backup task."""
    del job  # every JobHandler takes (job, progress); this one needs no payload
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError(_NEXUS_CLIENT_NOT_AVAILABLE)
    await progress(10, "triggering backup task")
    result = await trigger_backup(nexus)
    await progress(100, "backup triggered")
    return result


async def _run_backup_archive(
    *,
    nexus,
    mode: str,
    repos: list[str] | None,
    triggered_by: str,
    compress: bool,
    schedule_id: int | None,
    progress: ProgressCallback,
) -> dict:
    """Shared body of a byte-level backup: create+track a BackupRun, run
    :func:`create_archive`, record the outcome. Used by both the manual
    ``backup_archive`` job and the scheduled ``run_scheduled_backup`` job so
    the two don't maintain two copies of the same bookkeeping.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from ..models import BackupRun

    settings = _lifespan_state.get("settings")
    output_dir = Path(getattr(settings, "BACKUP_OUTPUT_DIR", "/app/backups") if settings else "/app/backups")
    min_free_bytes = getattr(settings, "BACKUP_MIN_FREE_BYTES", 512 * 1024 * 1024) if settings else 512 * 1024 * 1024

    factory = get_session_factory()
    async with factory() as session:
        run = BackupRun(
            mode=mode, repos=json.dumps(repos or []), status="running",
            triggered_by=triggered_by, schedule_id=schedule_id,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    async def on_progress(pct: int, message: str) -> None:
        await progress(pct, message)

    try:
        result = await create_archive(
            nexus, output_dir=output_dir, mode=mode, repos=repos,
            min_free_bytes=min_free_bytes, on_progress=on_progress, compress=compress,
        )
    except Exception as exc:  # noqa: BLE001
        async with factory() as session:
            run = await session.get(BackupRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = datetime.now(timezone.utc)
                await session.commit()
        try:
            from ..modules.telegram.client import escape_html
            from ..modules.telegram.notify import notify_admins
            await notify_admins(f"❌ <b>Backup failed</b> ({escape_html(mode)})\n{escape_html(str(exc)[:500])}")
        except Exception:  # noqa: BLE001 - Telegram is a bonus delivery channel, never a reason to fail the backup job's own error handling
            logger.exception("Telegram notify failed for backup run %s", run_id)
        raise

    async with factory() as session:
        run = await session.get(BackupRun, run_id)
        if run is not None:
            run.status = "success"
            run.repos = json.dumps(result["repos"])
            run.output_path = result["output_path"]
            run.total_bytes = result["total_bytes"]
            run.asset_count = result["asset_count"]
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()

    return result


async def handle_backup_archive(job: Job, progress: ProgressCallback) -> dict:
    """Real byte-level backup archive (full or selective) to the backup volume.

    Distinct from ``handle_backup`` (Nexus scheduler-task trigger, unchanged) —
    this one actually downloads and persists asset bytes, tracked in a
    :class:`~app.models.BackupRun` row so the archive has a durable history
    beyond the job's own 7-day Redis TTL. Manual runs are never compressed and
    never auto-pruned — see ``run_scheduled_backup`` for the scheduled path.
    """
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError(_NEXUS_CLIENT_NOT_AVAILABLE)

    return await _run_backup_archive(
        nexus=nexus,
        mode=job.payload.get("mode", "full"),
        repos=job.payload.get("repos") or None,
        triggered_by=job.payload.get("triggered_by", ""),
        compress=False,
        schedule_id=None,
        progress=progress,
    )


async def handle_run_scheduled_backup(job: Job, progress: ProgressCallback) -> dict:
    """Run one BackupSchedule: create a compressed archive, then prune old ones.

    Payload: ``{"schedule_id": int}``. Unlike a manual backup, the archive is
    written as a single ``.tar.gz`` (see ``backup_archive.create_archive``'s
    ``compress`` flag) and the schedule's own retention rule
    (``retention_keep_last``/``retention_max_age_days``) is applied to its
    prior runs afterward.
    """
    from ..models import BackupSchedule
    from .backup_schedule import prune_old_archives

    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError(_NEXUS_CLIENT_NOT_AVAILABLE)

    schedule_id = job.payload.get("schedule_id")
    if schedule_id is None:
        raise ValueError("payload.schedule_id is required")

    factory = get_session_factory()
    async with factory() as session:
        schedule = await session.get(BackupSchedule, int(schedule_id))
        if schedule is None:
            raise ValueError(f"BackupSchedule {schedule_id} not found")
        mode, repos, name = schedule.mode, None, schedule.name
        if schedule.repos:
            import json
            repos = json.loads(schedule.repos) or None

    result = await _run_backup_archive(
        nexus=nexus, mode=mode, repos=repos, triggered_by=f"schedule:{name}",
        compress=True, schedule_id=int(schedule_id), progress=progress,
    )

    async with factory() as session:
        schedule = await session.get(BackupSchedule, int(schedule_id))
        if schedule is not None:
            pruned = await prune_old_archives(session, schedule)
            result["pruned"] = pruned

    return result


async def handle_sync(job: Job, progress: ProgressCallback) -> dict:
    """Sync one or more source repos to a target Nexus instance.

    ``job.payload["repos"]`` is a list of ``{"source_repo", "target_repo"}``
    pairs (selective — the operator picks exactly which repos to sync);
    each pair still goes through the unchanged single-repo
    :func:`sync_repository` primitive, run one after another with progress
    scaled across the whole batch.
    """
    state = _shared_state()
    source = state.nexus
    if source is None:
        raise RuntimeError("Source Nexus client not available")
    p = job.payload
    required = ("target_base_url", "target_username", "target_password_enc", "repos")
    missing = [k for k in required if not p.get(k)]
    if missing:
        raise ValueError(f"Missing payload fields: {missing}")

    settings = get_settings()
    # Re-validate the destination at dispatch time, not just at enqueue time:
    # jobs queued before this guard existed have never been checked, and DNS
    # can change between the two points (rebinding).
    try:
        validate_outbound_url(p["target_base_url"], settings)
    except OutboundURLError as exc:
        raise ValueError(f"sync target rejected: {exc}") from exc

    target_password = config_store.decrypt_password(p["target_password_enc"], settings)
    if not target_password:
        raise ValueError(
            "Could not decrypt the sync target password. This job was most "
            "likely queued under a different NEXUS_CONFIG_ENCRYPTION_KEY — "
            "re-submit the sync."
        )

    mappings = p["repos"]
    if not mappings:
        raise ValueError("payload.repos must be a non-empty list")

    total = len(mappings)
    results = []
    for i, mapping in enumerate(mappings):
        source_repo = mapping["source_repo"]
        target_repo = mapping["target_repo"]

        # i, source_repo and target_repo all captured as defaults, not read
        # from the enclosing scope directly: Python closures bind by name, not
        # by value, so without this every on_progress would see whatever the
        # loop variables hold *when called*, not when defined — correct today
        # only because sync_repository calls back synchronously before the
        # next iteration reassigns them; capturing removes that fragility.
        async def on_progress(percent: int, message: str, i=i, source_repo=source_repo, target_repo=target_repo) -> None:
            # Scale this repo's own 0-100 progress into its slice of the batch.
            scaled = int((i + percent / 100) / total * 100)
            await progress(scaled, f"[{source_repo} -> {target_repo}] {message}")

        result = await sync_repository(
            source,
            source_repo,
            target_base_url=p["target_base_url"],
            target_username=p["target_username"],
            target_password=target_password,
            target_repo=target_repo,
            verify_ssl=bool(p.get("verify_ssl", True)),
            on_progress=on_progress,
        )
        results.append(result)

    return {"repos_synced": total, "results": results}


async def handle_scan_image(job: Job, progress: ProgressCallback) -> dict:
    """Statically scan one image with the configured (or payload) scanners.

    The registry endpoint is resolved from Nexus here rather than being carried
    in the payload, so a job queued minutes ago still targets the connector port
    the repository has *now*.
    """
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError(_NEXUS_CLIENT_NOT_AVAILABLE)
    payload = job.payload
    repo, image = payload.get("repo"), payload.get("image")
    if not repo or not image:
        raise ValueError("payload.repo and payload.image are required")
    scanners = payload.get("scanners") or await _default_scanners()

    await progress(5, f"resolving the Docker registry endpoint for '{repo}'")
    try:
        target_registry = await registry_discovery.resolve(nexus, repo, state.cache)
    except registry_discovery.RegistryUnavailable as exc:
        raise RuntimeError(f"cannot scan {repo}/{image}: {exc}") from exc

    username, password = nexus.credentials
    await progress(15, f"scanning {target_registry.image_ref(image)} with {', '.join(scanners)}")

    factory = get_session_factory()
    async with factory() as session:
        reports = await scan_image(
            session, target_registry, image, scanners,
            Credentials(username, password), verify_tls=nexus.verify_ssl,
        )
        succeeded = any(r.status == "success" for r in reports)
        await scan_events.record_scan_outcome(
            session, payload.get("ledger_id"), repo, image, succeeded,
        )

    summary = [
        {"scanner": r.scanner, "status": r.status, "error": r.error,
         "critical": r.critical, "high": r.high, "medium": r.medium,
         "low": r.low, "unknown": r.unknown, "duration_ms": r.duration_ms}
        for r in reports
    ]
    failures = [f"{r['scanner']}: {r['error']}" for r in summary if r["status"] != "success"]
    await progress(100, "; ".join(failures) if failures else "scan complete")
    return {
        "repo": repo, "image": image, "registry": target_registry.base_url,
        "trigger": payload.get("trigger", "manual"), "reports": summary,
    }


async def _default_scanners() -> list[str]:
    """Enabled scanners — the dashboard toggle first, then the env default
    this process booted with."""
    from .scanner_config import get_enabled_scanners
    settings = _lifespan_state.get("settings")
    if settings is None:
        return _lifespan_state.get("scanners") or ["trivy", "grype"]
    return await get_enabled_scanners(settings)


async def _scanner_proxy() -> str:
    """Proxy for database downloads: dashboard value first, then env."""
    import json
    settings = _lifespan_state.get("settings")
    factory = get_session_factory()
    try:
        async with factory() as session:
            from sqlalchemy import select
            from ..models import SystemConfig
            row = await session.scalar(select(SystemConfig).where(SystemConfig.key == "scanner_proxy"))
            if row:
                configured = json.loads(row.value_json).get("proxy", "")
                if configured:
                    return configured
    except Exception:  # noqa: BLE001 - the env fallback below still applies
        logger.debug("could not read the dashboard scanner proxy", exc_info=True)
    return getattr(settings, "SCANNER_PROXY", "") if settings else ""


async def _requested_scanners(job: Job) -> list[str]:
    """The scanners a job payload asked for, scoped to what's actually enabled.

    ``scanners`` lets a caller (the per-scanner "Update"/"Force" buttons) ask
    for just one; an empty/missing list falls back to every enabled scanner
    (the "Update all" button). Anything requested that isn't in the enabled
    set is dropped rather than trusted outright — the payload comes from an
    API caller, not from this process.
    """
    enabled = await _default_scanners()
    requested = job.payload.get("scanners") or []
    if not isinstance(requested, list):
        return enabled
    scoped = [s for s in requested if s in enabled]
    return scoped or enabled


async def handle_scanner_db_update(job: Job, progress: ProgressCallback) -> dict:
    """Refresh vulnerability databases for the requested (or all enabled) scanners."""
    scanners = await _requested_scanners(job)
    proxy = await _scanner_proxy()
    await progress(2, f"updating databases: {', '.join(scanners)}{' via proxy' if proxy else ''}",
                   {"stage": "connecting", "scanners": scanners})
    try:
        result = await scanner_db.update(
            scanners, on_progress=progress, proxy=proxy,
            force=bool(job.payload.get("force", False)),
        )
    except Exception as exc:  # noqa: BLE001
        try:
            from ..modules.telegram.client import escape_html
            from ..modules.telegram.notify import notify_admins
            await notify_admins(
                f"❌ <b>Scanner database update failed</b> ({escape_html(', '.join(scanners))})\n"
                f"{escape_html(str(exc)[:500])}"
            )
        except Exception:  # noqa: BLE001 - Telegram is a bonus delivery channel, never a reason to mask the real failure
            logger.exception("Telegram notify failed for scanner_db_update")
        raise
    await progress(100, "done", {"stage": "done", "results": result})
    return result


async def handle_scanner_db_import(job: Job, progress: ProgressCallback) -> dict:
    """Install vulnerability databases from offline archives (no network).

    For restricted or air-gapped networks where the online update cannot reach
    Docker Hub / ghcr.io. The operator drops the archives into the mounted
    offline directory; this extracts/imports them into the scanner caches.
    """
    scanners = await _requested_scanners(job)
    await progress(2, f"importing offline databases: {', '.join(scanners)}",
                   {"stage": "importing", "scanners": scanners})
    result = await scanner_db.import_offline(scanners, on_progress=progress)
    await progress(100, "done", {"stage": "done", "results": result})
    return result
