"""Job handler implementations registered with the JobRunner.

Each handler matches a job ``type`` and receives ``(job, progress)``. Handlers
own their own DB session (from :mod:`app.db.session`) since they run outside a
request scope.

Registered job types:
  * ``collect_metrics`` — snapshot all repos, persist samples, evaluate alerts.
  * ``analyze_repo`` — deep-analyze a single repo (delegates to StorageAnalyzer).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ..core.jobs import Job
from ..db.session import get_session_factory
from ..state import lifespan_handles as _lifespan_state  # populated at startup
from .alerting import evaluate_alerts
from .backup import export_metadata, trigger_backup
from .metrics_collector import collect_once, latest_snapshot
from .retention import run_all_enabled, run_policy
from .scanners import import_offline_dbs, scan_image, update_scanner_dbs
from .sync import sync_repository

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], Awaitable[None]]


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
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError("Nexus client not available")

    factory = get_session_factory()
    async with factory() as session:
        summary = await collect_once(
            nexus, session, on_progress=progress, retention_days=state.retention_days
        )
        # Re-read the latest snapshot so alerting sees fresh values.
        snapshot = await latest_snapshot(session)
        fired = await evaluate_alerts(session, snapshot)
    return {**summary, "alerts_fired": fired}


async def handle_analyze_repo(job: Job, progress: ProgressCallback) -> dict:
    """Deep-analyze a single repo (used by 'analyze all' fan-out, or on-demand)."""
    from .storage_analyzer import StorageAnalyzer

    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError("Nexus client not available")
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
        raise RuntimeError("Nexus client not available")
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
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError("Nexus client not available")
    await progress(10, "triggering backup task")
    result = await trigger_backup(nexus)
    await progress(100, "backup triggered")
    return result


async def handle_sync(job: Job, progress: ProgressCallback) -> dict:
    """Sync a source repo to a target Nexus repository."""
    state = _shared_state()
    source = state.nexus
    if source is None:
        raise RuntimeError("Source Nexus client not available")
    p = job.payload
    required = ("target_base_url", "target_username", "target_password", "target_repo", "source_repo")
    missing = [k for k in required if not p.get(k)]
    if missing:
        raise ValueError(f"Missing payload fields: {missing}")

    async def on_progress(percent: int, message: str) -> None:
        await progress(percent, message)

    return await sync_repository(
        source,
        p["source_repo"],
        target_base_url=p["target_base_url"],
        target_username=p["target_username"],
        target_password=p["target_password"],
        target_repo=p["target_repo"],
        verify_ssl=bool(p.get("verify_ssl", True)),
        on_progress=on_progress,
    )


async def handle_scan_image(job: Job, progress: ProgressCallback) -> dict:
    """Scan a single image with the configured (or payload) scanners."""
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError("Nexus client not available")
    p = job.payload
    repo = p.get("repo")
    image = p.get("image")
    if not repo or not image:
        raise ValueError("payload.repo and payload.image are required")
    # Default to globally-enabled scanners; payload may override.
    scanners = p.get("scanners") or _lifespan_state.get("scanners", ["trivy", "grype"])

    await progress(10, f"scanning {image} with {','.join(scanners)}")
    factory = get_session_factory()
    async with factory() as session:
        reports = await scan_image(nexus, session, repo, image, scanners)
    await progress(100, "done")
    return {
        "repo": repo, "image": image,
        "reports": [
            {"scanner": r.scanner, "status": r.status,
             "critical": r.critical, "high": r.high, "medium": r.medium, "low": r.low}
            for r in reports
        ],
    }


async def handle_scanner_db_update(job: Job, progress: ProgressCallback) -> dict:
    """Refresh vulnerability databases for the configured scanners."""
    import json
    settings = _lifespan_state.get("settings")
    scanners = (settings.SCANNERS_ENABLED.split(",") if settings else ["trivy", "grype"])
    scanners = [s.strip().lower() for s in scanners if s.strip()]

    # Read proxy: first from DB (dashboard-managed), fall back to env.
    proxy = ""
    cache = _lifespan_state.get("cache")
    factory = get_session_factory()
    try:
        async with factory() as session:
            from sqlalchemy import select
            from ..models import SystemConfig
            row = await session.scalar(select(SystemConfig).where(SystemConfig.key == "scanner_proxy"))
            if row:
                proxy = json.loads(row.value_json).get("proxy", "")
    except Exception:  # noqa: BLE001
        pass
    if not proxy:
        proxy = getattr(settings, "SCANNER_PROXY", "") if settings else ""

    await progress(2, f"updating DBs: {','.join(scanners)}{' via proxy' if proxy else ''}")

    async def on_progress(percent: int, message: str) -> None:
        await progress(percent, message)

    force = bool(job.payload.get("force", False))
    result = await update_scanner_dbs(scanners, on_progress=on_progress, proxy=proxy, force=force)
    await progress(100, "done")
    return result


async def handle_scanner_db_import(job: Job, progress: ProgressCallback) -> dict:
    """Import vulnerability DBs from pre-downloaded offline archives (no network).

    For restricted/air-gapped networks where ``scanner_db_update`` can't reach
    Docker Hub / ghcr.io. The operator drops the archives into the mounted
    offline dir; this handler extracts/imports them into the scanner caches.
    """
    settings = _lifespan_state.get("settings")
    scanners = (settings.SCANNERS_ENABLED.split(",") if settings else ["trivy", "grype"])
    scanners = [s.strip().lower() for s in scanners if s.strip()]

    await progress(2, f"importing offline DBs: {','.join(scanners)}")

    async def on_progress(percent: int, message: str) -> None:
        await progress(percent, message)

    result = await import_offline_dbs(scanners, on_progress=on_progress)
    await progress(100, "done")
    return result
