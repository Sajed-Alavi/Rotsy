"""FastAPI application entrypoint (v3).

Adds on top of v2:
  * a background :class:`JobRunner` (Redis queue) registered with metric +
    analyze handlers.
  * a periodic metric-collection loop that snapshots every repo on an
    interval and evaluates alert rules.
  * new routers: jobs, metrics, alerts, settings.

All wiring lives in the lifespan so connections are pooled and cleaned up.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, AsyncIterator

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, get_settings
from .core.cache import Cache
from .core.jobs import JobRunner
from .core.nexus_client import NexusClient
from .db.session import get_session_factory
from .routers import (
    access,
    alerts,
    analytics,
    audit,
    auth,
    blobstores,
    health,
    jobs,
    metrics,
    prometheus,
    repositories,
    retention,
    roles,
    scan,
    settings as settings_router,
    storage,
    system,
    users,
)
from .services.alerting import evaluate_alerts
from .services.metrics_collector import collect_once, latest_snapshot
from .state import app_state, lifespan_handles


# Backwards-compat alias: job_handlers imports ``_lifespan_state`` from here.
_lifespan_state = lifespan_handles


@dataclass
class _SharedHandles:
    """Lightweight bag of shared objects passed into job handlers."""

    nexus: NexusClient | None
    cache: Cache | None
    max_concurrency: int
    retention_days: int


async def _metric_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Periodically collect metrics + evaluate alerts.

    The first run happens quickly after startup; subsequent runs on the
    configured interval. Stops cleanly on shutdown.
    """
    logger = logging.getLogger("metric_loop")
    nexus = _lifespan_state.get("nexus")
    if nexus is None:
        return
    interval = settings.METRIC_COLLECTION_INTERVAL_SECONDS
    factory = get_session_factory()

    async def tick() -> None:
        try:
            async with factory() as session:
                await collect_once(nexus, session, retention_days=settings.METRIC_RETENTION_DAYS)
                snapshot = await latest_snapshot(session)
                await evaluate_alerts(session, snapshot)
        except Exception:  # noqa: BLE001
            logger.exception("metric collection cycle failed")

    # First collect after a short delay (let Nexus settle), then on interval.
    await asyncio.sleep(10)
    await tick()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            await tick()


async def _scanner_db_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Periodically refresh vulnerability databases for enabled scanners.

    Two schedules, chosen by config:
      * **Time-of-day** — if ``SCANNER_DB_UPDATE_AT`` (HH:MM) is set, run once
        daily at that server-local time (like the retention sweep).
      * **Interval** — otherwise every ``SCANNER_DB_UPDATE_INTERVAL_HOURS``.

    On restricted/air-gapped networks set ``SCANNER_DB_OFFLINE_MODE=true`` so
    the scheduled run *imports* pre-downloaded archives (``scanner_db_import``)
    instead of downloading (``scanner_db_update``).
    """
    import datetime as _dt

    logger = logging.getLogger("scanner_db_loop")
    cache = _lifespan_state.get("cache")
    if cache is None:
        return

    job_type = "scanner_db_import" if settings.SCANNER_DB_OFFLINE_MODE else "scanner_db_update"
    tod = settings.scanner_db_time_of_day
    interval = settings.SCANNER_DB_UPDATE_INTERVAL_HOURS * 3600

    def seconds_until_next_run() -> float:
        now = _dt.datetime.now()
        hh, mm = tod  # type: ignore[misc]
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += _dt.timedelta(days=1)
        return (target - now).total_seconds()

    async def enqueue(reason: str) -> None:
        try:
            from .core.jobs import JobQueue
            jid = await JobQueue(cache).enqueue(job_type, {})
            logger.info("%s scanner DB %s enqueued: job %s",
                        reason, "import" if settings.SCANNER_DB_OFFLINE_MODE else "update", jid)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to enqueue %s scanner DB job", reason)

    # First update a bit after startup so the wrapper is usable while DBs warm.
    try:
        await asyncio.wait_for(stop.wait(), timeout=30)
        return
    except asyncio.TimeoutError:
        pass

    # Initial refresh (both schedules do an initial warm-up).
    await enqueue("Initial")

    while not stop.is_set():
        delay = max(30.0, seconds_until_next_run()) if tod is not None else interval
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            pass
        await enqueue("Scheduled" if tod is not None else "Periodic")


async def _auto_scan_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Scan new components pushed to enabled repos.

    Polls each enabled :class:`ScanTarget` for components newer than the last
    scan and enqueues a ``scan_image`` job for each new image. Docker format
    only for now; other formats are skipped.
    """
    import datetime as _dt
    from sqlalchemy import select
    from .core.jobs import JobQueue
    from .db.session import get_session_factory
    from .models import ScanTarget
    from .core.nexus_client import NexusClient  # noqa: F401

    logger = logging.getLogger("auto_scan_loop")
    cache = _lifespan_state.get("cache")
    nexus = _lifespan_state.get("nexus")
    if cache is None or nexus is None:
        return

    poll_interval = 60  # seconds between sweeps
    try:
        await asyncio.wait_for(stop.wait(), timeout=20)
        return
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            factory = get_session_factory()
            async with factory() as session:
                targets = (await session.execute(
                    select(ScanTarget).where(ScanTarget.enabled.is_(True), ScanTarget.auto_scan.is_(True))
                )).scalars().all()
                for t in targets:
                    try:
                        resp = await nexus.client.get(
                            "/service/rest/v1/components", params={"repository": t.repo}
                        )
                        if resp.status_code != 200:
                            continue
                        for c in (resp.json() or {}).get("items", []) or []:
                            if c.get("format") != "docker":
                                continue
                            name = c.get("name")
                            version = c.get("version")
                            if not name or not version:
                                continue
                            image_ref = f"{name}:{version}"
                            cache_key = f"scanned:{t.repo}:{image_ref}"
                            if await cache.redis.get(cache_key):
                                continue  # already scanned
                            jid = await JobQueue(cache).enqueue(
                                "scan_image",
                                {"repo": t.repo, "image": image_ref, "scanners": (t.scanners.split(",") if t.scanners else settings.scanners_enabled)},
                            )
                            # Mark scanned for 24h to dedupe.
                            await cache.redis.set(cache_key, jid, ex=86400)
                            logger.info("Auto-scan enqueued for %s/%s: job %s", t.repo, image_ref, jid)
                    except Exception:  # noqa: BLE001
                        logger.exception("auto-scan sweep failed for repo %s", t.repo)
        except Exception:  # noqa: BLE001
            logger.exception("auto-scan loop cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass


async def _retention_scheduler(settings: Settings, stop: asyncio.Event) -> None:
    """Daily retention sweep.

    Enqueues a ``run_retention`` job every day at RETENTION_RUN_AT (HH:MM,
    server-local). The job itself executes every enabled policy and reclaims
    blob space. This loop only computes time-to-next-run and sleeps.
    """
    import datetime as _dt

    logger = logging.getLogger("retention_scheduler")
    cache = _lifespan_state.get("cache")
    if cache is None:
        return
    target_hh, target_mm = settings.retention_time_of_day

    def seconds_until_next_run() -> float:
        now = _dt.datetime.now()
        target = now.replace(hour=target_hh, minute=target_mm, second=0, microsecond=0)
        if target <= now:
            target += _dt.timedelta(days=1)
        return (target - now).total_seconds()

    # Wait one full minute after startup so the metric loop and the runner are
    # ready, then enter the daily scheduling loop.
    try:
        await asyncio.wait_for(stop.wait(), timeout=60)
        return
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        delay = max(30.0, seconds_until_next_run())
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
            return  # stop signaled
        except asyncio.TimeoutError:
            pass
        # It's time. Enqueue the sweep.
        try:
            from .core.jobs import JobQueue
            jid = await JobQueue(cache).enqueue("run_retention", {"dry_run": False})
            logger.info("Daily retention sweep enqueued: job %s", jid)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to enqueue daily retention sweep")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown lifecycle."""
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    logger = logging.getLogger("nexus_wrapper")

    nexus = NexusClient(settings)
    cache = Cache(settings)
    await nexus.start()
    await cache.start()

    # Load the Nexus connection from the dashboard DB if an admin has saved
    # one; otherwise the env-provided defaults remain in effect.
    try:
        from .core.config_store import get_nexus_connection
        factory = get_session_factory()
        async with factory() as session:
            conn = await get_nexus_connection(session, settings)
        if conn.is_configured() and conn.url != settings.NEXUS_URL:
            await nexus.reconfigure(conn.url, conn.username, conn.password, conn.verify_ssl)
            logger.info("Loaded Nexus connection from dashboard config.")
        elif not conn.is_configured() and not settings.NEXUS_URL:
            logger.warning("No Nexus connection configured — set it via Settings in the UI.")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to load Nexus connection from DB; using env defaults.")

    app.state.nexus = nexus
    app.state.cache = cache

    # Populate the shared bag for job handlers.
    _lifespan_state.clear()
    _lifespan_state["nexus"] = nexus
    _lifespan_state["cache"] = cache
    _lifespan_state["max_concurrency"] = settings.ANALYZER_MAX_CONCURRENCY
    _lifespan_state["retention_days"] = settings.METRIC_RETENTION_DAYS
    _lifespan_state["scanners"] = settings.scanners_enabled
    _lifespan_state["settings"] = settings
    _lifespan_state["state"] = _SharedHandles(
        nexus=nexus,
        cache=cache,
        max_concurrency=settings.ANALYZER_MAX_CONCURRENCY,
        retention_days=settings.METRIC_RETENTION_DAYS,
    )

    # Start the job runner + register handlers.
    runner = JobRunner(cache)
    from .services import job_handlers
    runner.register("collect_metrics", job_handlers.handle_collect_metrics)
    runner.register("analyze_repo", job_handlers.handle_analyze_repo)
    runner.register("run_retention", job_handlers.handle_run_retention)
    runner.register("backup", job_handlers.handle_backup)
    runner.register("sync", job_handlers.handle_sync)
    runner.register("scan_image", job_handlers.handle_scan_image)
    runner.register("scanner_db_update", job_handlers.handle_scanner_db_update)
    runner.register("scanner_db_import", job_handlers.handle_scanner_db_import)
    await runner.start()

    # Start the periodic metric-collection loop.
    stop_metric = asyncio.Event()
    metric_task = asyncio.create_task(_metric_loop(settings, stop_metric))

    # Start the daily retention scheduler.
    stop_retention = asyncio.Event()
    retention_task = asyncio.create_task(_retention_scheduler(settings, stop_retention))

    # Start the periodic scanner DB refresh loop.
    stop_scanner_db = asyncio.Event()
    scanner_db_task = asyncio.create_task(_scanner_db_loop(settings, stop_scanner_db))

    # Start the auto-scan loop (scans new images in enabled repos).
    stop_auto_scan = asyncio.Event()
    auto_scan_task = asyncio.create_task(_auto_scan_loop(settings, stop_auto_scan))

    logger.info("Nexus Advanced Wrapper v%s started.", __version__)
    try:
        yield
    finally:
        stop_metric.set()
        stop_retention.set()
        stop_scanner_db.set()
        stop_auto_scan.set()
        metric_task.cancel()
        retention_task.cancel()
        scanner_db_task.cancel()
        auto_scan_task.cancel()
        for t in (metric_task, retention_task, scanner_db_task, auto_scan_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await runner.stop()
        await cache.close()
        await nexus.close()
        logger.info("Nexus Advanced Wrapper shut down cleanly.")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    app = FastAPI(
        title="Nexus Repository Manager — Advanced Web Wrapper",
        description=(
            "Advanced management wrapper around Sonatype Nexus Repository Manager. "
            "Auth is cookie-based JWT (login at /api/auth/login). All endpoints "
            "except /api/auth/login and /api/auth/refresh require authentication + "
            "the listed permission.\n\n"
            "Interactive docs are available at /docs and /redoc."
        ),
        version=__version__,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "auth", "description": "Login, logout, refresh, current user."},
            {"name": "health", "description": "Service reachability probes."},
            {"name": "users", "description": "User administration (users:manage)."},
            {"name": "roles", "description": "Role + permission administration (roles:manage)."},
            {"name": "settings", "description": "Self-service profile + password."},
            {"name": "repositories", "description": "Repo list + asset browse + proxy download."},
            {"name": "storage", "description": "Deep storage analyzer (docker + generic)."},
            {"name": "retention", "description": "Rule-based cleanup + daily scheduler."},
            {"name": "metrics", "description": "Real-time + historical metrics."},
            {"name": "alerts", "description": "Webhook alert rules + evaluator."},
            {"name": "jobs", "description": "Background job queue + live progress."},
            {"name": "scan", "description": "Trivy/Grype vulnerability scanning."},
            {"name": "blobstores", "description": "Blobstore management (scaffolded)."},
            {"name": "system", "description": "Backup + Nexus-to-Nexus sync + scripts."},
            {"name": "access", "description": "CI/CD tokens + webhooks (scaffolded)."},
            {"name": "analytics", "description": "Bandwidth + top downloads (scaffolded)."},
        ],
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(roles.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(repositories.router, prefix="/api")
    app.include_router(storage.router, prefix="/api")
    app.include_router(retention.router, prefix="/api")
    app.include_router(blobstores.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(scan.router, prefix="/api")
    app.include_router(access.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    app.include_router(metrics.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(prometheus.router)  # no prefix — serves at /metrics/export
    app.include_router(alerts.router, prefix="/api")
    app.include_router(audit.router, prefix="/api")
    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        reload=False,
    )
