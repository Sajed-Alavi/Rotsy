"""Job handler implementations registered with the JobRunner.

Each handler matches a job ``type`` and receives ``(job, progress)``. Handlers
own their own DB session (from :mod:`app.db.session`) since they run outside a
request scope, and report progress so the UI can stream it over SSE.

Registered job types (see :func:`app.main.lifespan` for the registration):

  * ``collect_metrics``    — snapshot every repo, persist samples, evaluate alerts
  * ``analyze_repo``       — deep storage analysis of one repository
  * ``run_retention``      — execute retention policies (dry-run capable)
  * ``backup``             — trigger a Nexus backup task
  * ``sync``               — copy components to another Nexus
  * ``scan_image``         — statically scan one image with Trivy/Grype
  * ``scanner_db_update``  — refresh the vulnerability databases (network)
  * ``scanner_db_import``  — install them from offline archives (no network)
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ..core.jobs import Job
from ..db.session import get_session_factory
from ..state import lifespan_handles as _lifespan_state  # populated at startup
from . import registry as registry_discovery
from . import scan_events, scanner_db
from .alerting import evaluate_alerts
from .backup import trigger_backup
from .metrics_collector import collect_once, latest_snapshot
from .retention import run_all_enabled, run_policy
from .scanners import Credentials, scan_image
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
    """Statically scan one image with the configured (or payload) scanners.

    The registry endpoint is resolved from Nexus here rather than being carried
    in the payload, so a job queued minutes ago still targets the connector port
    the repository has *now*.
    """
    state = _shared_state()
    nexus = state.nexus
    if nexus is None:
        raise RuntimeError("Nexus client not available")
    payload = job.payload
    repo, image = payload.get("repo"), payload.get("image")
    if not repo or not image:
        raise ValueError("payload.repo and payload.image are required")
    scanners = payload.get("scanners") or _default_scanners()

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


def _default_scanners() -> list[str]:
    return _lifespan_state.get("scanners") or ["trivy", "grype"]


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


async def handle_scanner_db_update(job: Job, progress: ProgressCallback) -> dict:
    """Refresh vulnerability databases for the configured scanners."""
    scanners = _default_scanners()
    proxy = await _scanner_proxy()
    await progress(2, f"updating databases: {', '.join(scanners)}{' via proxy' if proxy else ''}")
    result = await scanner_db.update(
        scanners, on_progress=progress, proxy=proxy,
        force=bool(job.payload.get("force", False)),
    )
    await progress(100, "done")
    return result


async def handle_scanner_db_import(job: Job, progress: ProgressCallback) -> dict:
    """Install vulnerability databases from offline archives (no network).

    For restricted or air-gapped networks where the online update cannot reach
    Docker Hub / ghcr.io. The operator drops the archives into the mounted
    offline directory; this extracts/imports them into the scanner caches.
    """
    scanners = _default_scanners()
    await progress(2, f"importing offline databases: {', '.join(scanners)}")
    result = await scanner_db.import_offline(scanners, on_progress=progress)
    await progress(100, "done")
    return result
