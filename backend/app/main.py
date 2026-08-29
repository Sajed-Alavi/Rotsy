"""FastAPI application entrypoint.

All wiring lives in the lifespan so connections are pooled and cleaned up:

  * a :class:`~app.modules.nexus.connector.NexusClient` and a Redis-backed cache,
  * a :class:`~app.core.jobs.JobRunner` consuming the job queue,
  * five background loops, each with a narrow remit:
      - ``_metric_loop``          snapshot repository metrics, evaluate alerts
      - ``_retention_scheduler``  daily retention sweep
      - ``_scanner_db_loop``      keep the vulnerability databases usable
      - ``_push_watch_loop``      notice newly pushed images (fallback trigger)
      - ``_backup_schedule_loop`` poll due BackupSchedule rows, enqueue their runs

**Startup does no scanning.** Nothing here walks existing images looking for
work: scans are triggered by a push or by an operator, and by nothing else. The
loop that previously re-enumerated every enabled repository every 60 seconds
with a 24-hour Redis dedupe (so every image was re-scanned daily, and everything
was re-scanned whenever Redis restarted) has been replaced by a ledger-backed
watcher that baselines a repository on first sight. See
:mod:`app.modules.nexus.events`.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, get_settings
from .core.cache import Cache
from .core.jobs import JobRunner
from .modules.nexus.connector import NexusClient
from .db.session import get_session_factory
from .routers import (
    access,
    alerts,
    audit,
    auth,
    blobstores,
    github,
    gitlab,
    health,
    jobs,
    metrics,
    projects,
    prometheus,
    repositories,
    retention,
    roles,
    scan,
    settings as settings_router,
    sonar,
    storage,
    system,
    tasks,
    telegram,
    users,
)

# Importing a module package registers it with app.core.integrations (see
# each module's __init__.py) — done here, once, before any router that reads
# the registry (routers/projects.py's connect_integration) is reachable.
from . import modules  # noqa: F401,E402
from .services.alerting import evaluate_alerts
from .services.metrics_collector import (
    collect_blobstore_metrics, collect_once, latest_blobstore_snapshot, latest_snapshot,
)
from .state import lifespan_handles


# Backwards-compat alias: job_handlers imports ``_lifespan_state`` from here.
_lifespan_state = lifespan_handles


@dataclass
class _SharedHandles:
    """Lightweight bag of shared objects passed into job handlers."""

    nexus: NexusClient | None
    cache: Cache | None
    max_concurrency: int
    retention_days: int


def _seconds_until(hour: int, minute: int) -> float:
    """Seconds from now until the next local ``hour:minute``, tomorrow if past.

    Shared by the daily schedulers below, which each grew their own copy.
    """
    now = _dt.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    return (target - now).total_seconds()


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
                await collect_blobstore_metrics(nexus, session)
                snapshot = await latest_snapshot(session)
                blobstore_snapshot = await latest_blobstore_snapshot(session)
                await evaluate_alerts(session, snapshot, blobstore_snapshot)
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


async def _fetch_missing_databases_at_startup(scanner_db, settings: Settings, enqueue, logger) -> None:
    """Startup: only fetch what is missing. Scans cannot run without a
    database, so this is a prerequisite, not a refresh."""
    missing = [name for name, check in scanner_db.readiness(settings.scanners_enabled).items()
               if not check.ready]
    if missing:
        logger.info("No vulnerability database for: %s — fetching now", ", ".join(missing))
        await enqueue("Startup (database missing)")
    else:
        logger.info("Vulnerability databases already present; leaving them to the schedule.")


def _next_scanner_db_delay(time_of_day: tuple[int, int] | None, interval: float) -> float:
    if time_of_day is not None:
        return max(30.0, _seconds_until(*time_of_day))
    return interval


async def _scanner_db_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Keep the vulnerability databases fresh.

    Two schedules, chosen by config:
      * **Time-of-day** — if ``SCANNER_DB_UPDATE_AT`` (HH:MM) is set, run once
        daily at that server-local time (like the retention sweep).
      * **Interval** — otherwise every ``SCANNER_DB_UPDATE_INTERVAL_HOURS``.

    On restricted or air-gapped networks set ``SCANNER_DB_OFFLINE_MODE=true`` so
    the scheduled run *imports* pre-downloaded archives instead of downloading.

    On startup this only acts when a database is actually **missing** — a
    database that is present is left to its schedule. Refreshing on every boot
    (which the old shell script did as well, so it happened twice) meant a
    redeploy cost hundreds of megabytes for content already on disk.
    """
    from .modules.nexus import db as scanner_db

    logger = logging.getLogger("scanner_db_loop")
    cache = _lifespan_state.get("cache")
    if cache is None:
        return

    job_type = "scanner_db_import" if settings.SCANNER_DB_OFFLINE_MODE else "scanner_db_update"
    time_of_day = settings.scanner_db_time_of_day
    interval = settings.SCANNER_DB_UPDATE_INTERVAL_HOURS * 3600

    async def enqueue(reason: str) -> None:
        try:
            # Goes through the tracking helper rather than JobQueue directly so
            # the Database Management page can find a refresh it did not start.
            from .modules.nexus.db.tracking import enqueue_db_job
            job_id = await enqueue_db_job(cache, job_type, {})
            logger.info("%s scanner database %s enqueued: job %s", reason,
                        "import" if settings.SCANNER_DB_OFFLINE_MODE else "update", job_id)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to enqueue the %s scanner database job", reason)

    try:
        await asyncio.wait_for(stop.wait(), timeout=30)
        return
    except asyncio.TimeoutError:
        pass

    await _fetch_missing_databases_at_startup(scanner_db, settings, enqueue, logger)

    while not stop.is_set():
        try:
            delay = _next_scanner_db_delay(time_of_day, interval)
        except Exception:  # noqa: BLE001 - see _retention_scheduler's identical guard
            logger.exception("Could not compute the next scanner-db refresh time; retrying in 5 minutes")
            delay = 300.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            pass
        await enqueue("Scheduled" if time_of_day is not None else "Periodic")


async def _push_watch_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Notice images pushed to enabled repositories, for deployments without webhooks.

    This is the *fallback* trigger; the Nexus webhook
    (``POST /api/scan/events/nexus``) is the primary one and reacts in seconds.

    It compares repository contents against the durable ledger and queues a scan
    only for images the ledger has never seen. A repository observed for the
    first time is baselined — its existing images are recorded as history and
    **nothing is scanned**. That is what stops a restart, a redeploy or a new
    project from triggering a mass re-scan.

    Set ``SCAN_PUSH_POLL_SECONDS=0`` to disable this entirely and rely purely on
    webhooks.
    """
    from sqlalchemy import select

    from .db.session import get_session_factory
    from .models import ScanTarget
    from .modules.nexus import events as scan_events

    logger = logging.getLogger("push_watcher")
    cache = _lifespan_state.get("cache")
    nexus = _lifespan_state.get("nexus")
    interval = settings.SCAN_PUSH_POLL_SECONDS
    if cache is None or nexus is None:
        return
    if interval <= 0:
        logger.info("New-image watcher disabled (SCAN_PUSH_POLL_SECONDS=0); webhooks only.")
        return

    try:
        await asyncio.wait_for(stop.wait(), timeout=20)
        return
    except asyncio.TimeoutError:
        pass

    from .services.scanner_config import get_enabled_scanners

    factory = get_session_factory()
    while not stop.is_set():
        try:
            async with factory() as session:
                enabled_scanners = await get_enabled_scanners(settings, session)
                targets = (await session.execute(
                    select(ScanTarget).where(
                        ScanTarget.enabled.is_(True), ScanTarget.auto_scan.is_(True),
                    )
                )).scalars().all()
                for target in targets:
                    try:
                        await scan_events.observe_target(
                            nexus, session, cache, target, enabled_scanners,
                        )
                    except Exception:  # noqa: BLE001 - one repo must not stop the others
                        logger.exception("Could not observe repository '%s'", target.repo)
        except Exception:  # noqa: BLE001
            logger.exception("New-image watcher cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _retention_scheduler(settings: Settings, stop: asyncio.Event) -> None:
    """Daily retention sweep.

    Enqueues a ``run_retention`` job every day at RETENTION_RUN_AT (HH:MM,
    server-local). The job itself executes every enabled policy and reclaims
    blob space. This loop only computes time-to-next-run and sleeps.
    """
    logger = logging.getLogger("retention_scheduler")
    cache = _lifespan_state.get("cache")
    if cache is None:
        return
    target_hh, target_mm = settings.retention_time_of_day

    # Wait one full minute after startup so the metric loop and the runner are
    # ready, then enter the daily scheduling loop.
    try:
        await asyncio.wait_for(stop.wait(), timeout=60)
        return
    except asyncio.TimeoutError:
        pass

    while not stop.is_set():
        try:
            delay = max(30.0, _seconds_until(target_hh, target_mm))
        except Exception:  # noqa: BLE001 - a broken delay calculation must not silently kill this loop forever
            # It did, once (a refactor left _seconds_until referencing a name
            # that was never actually in its scope — NameError, uncaught,
            # here, ~60s after every restart, with nothing logged until an
            # already-discarded exception at shutdown). This loop enqueuing
            # nothing, ever, with no visible error, is the failure mode this
            # whole except clause exists to rule out.
            logger.exception("Could not compute the next retention run time; retrying in 5 minutes")
            delay = 300.0
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


async def _retention_interval_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Poll RetentionPolicy rows with their own ``interval_minutes`` set.

    The original daily sweep (``_retention_scheduler`` above) is a single
    sleep-until-one-shared-time timer, which only works because every policy
    used to share it. A policy that opts into its own cadence instead needs
    polling on a short fixed interval, the same shape as
    ``_backup_schedule_loop``/``_scanner_db_loop``/``_push_watch_loop``.
    """
    logger = logging.getLogger("retention_interval_loop")
    cache = _lifespan_state.get("cache")
    if cache is None:
        return
    interval = settings.RETENTION_SCHEDULER_POLL_SECONDS
    factory = get_session_factory()

    while not stop.is_set():
        try:
            from .services import retention as retention_service
            async with factory() as session:
                enqueued = await retention_service.poll_due_policies(cache, session)
            if enqueued:
                logger.info("Enqueued %d interval-scheduled retention run(s)", len(enqueued))
        except Exception:  # noqa: BLE001
            logger.exception("Retention interval poll cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _backup_schedule_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Poll BackupSchedule rows whose next_run_at is due, every
    BACKUP_SCHEDULER_POLL_SECONDS.

    Unlike ``_retention_scheduler``'s single sleep-until-next-time timer (which
    only works because there's one shared daily time), independent schedules
    can each have their own cadence — including arbitrary cron expressions —
    so this polls the DB on a short fixed interval instead, the same shape as
    ``_scanner_db_loop``/``_push_watch_loop``.
    """
    logger = logging.getLogger("backup_schedule_loop")
    cache = _lifespan_state.get("cache")
    if cache is None:
        return
    interval = settings.BACKUP_SCHEDULER_POLL_SECONDS
    factory = get_session_factory()

    while not stop.is_set():
        try:
            from .services import backup_schedule as backup_schedule_service
            async with factory() as session:
                enqueued = await backup_schedule_service.poll_due_schedules(cache, session)
            if enqueued:
                logger.info("Enqueued %d scheduled backup job(s)", len(enqueued))
        except Exception:  # noqa: BLE001
            logger.exception("Backup schedule poll cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _telegram_poll_loop(settings: Settings, stop: asyncio.Event) -> None:
    """Long-poll Telegram's ``getUpdates`` for the configured bot, dispatching
    each update through ``modules/telegram/dispatcher.py``.

    Long-polling, not a webhook — this deployment has no guaranteed public
    HTTPS endpoint (the same reasoning documented at length around the
    GitHub App's own webhook requirement — see ``WEBHOOK_BASE_URL`` in
    ``config.py``), and ``getUpdates`` needs nothing reachable from outside
    at all.

    Skipped (re-checked every 60s) whenever no bot token is configured, so
    turning one on from Settings takes effect within a minute, not just at
    restart. Unlike every other loop here, this one calls an external API in
    a tight cycle, so a run of consecutive failures (bad token, Telegram
    unreachable) backs off exponentially (5s -> 60s) instead of hammering
    ``getUpdates`` every iteration.
    """
    from .core.config_store import get_telegram_connection
    from .modules.telegram.client import TelegramClient, TelegramError
    from .modules.telegram.dispatcher import handle_update
    from .state import AppState

    logger = logging.getLogger("telegram_poll_loop")
    cache = _lifespan_state.get("cache")
    if cache is None:
        return

    factory = get_session_factory()
    app_state_obj = AppState(nexus=_lifespan_state.get("nexus"), cache=cache)
    last_update_id: int | None = None
    consecutive_failures = 0

    while not stop.is_set():
        try:
            async with factory() as session:
                conn = await get_telegram_connection(session, settings)
            if not conn.is_configured():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                    return  # stop signaled
                except asyncio.TimeoutError:
                    continue

            client = TelegramClient(conn.token, proxy=settings.TELEGRAM_PROXY_URL)
            offset = None if last_update_id is None else last_update_id + 1
            updates = await client.get_updates(offset, timeout=25)
            consecutive_failures = 0
        except TelegramError:
            consecutive_failures += 1
            # Exponent capped at 10 (backoff already caps at 60s via min()
            # below, but 5.0 * 2**(consecutive_failures - 1) is computed as a
            # Python int first and overflows converting to float long before
            # that min() ever gets a chance to clamp it).
            backoff = min(60.0, 5.0 * (2 ** min(consecutive_failures - 1, 10)))
            logger.warning(
                "Telegram getUpdates failed (%d in a row); retrying in %.0fs",
                consecutive_failures, backoff, exc_info=True,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
                return  # stop signaled
            except asyncio.TimeoutError:
                continue
        except Exception:  # noqa: BLE001 - e.g. a DB hiccup fetching the connection; must not silently kill this loop forever (see _retention_scheduler's own comment on this exact failure mode)
            consecutive_failures += 1
            backoff = min(60.0, 5.0 * (2 ** min(consecutive_failures - 1, 10)))
            logger.exception(
                "Telegram poll loop iteration failed (%d in a row); retrying in %.0fs",
                consecutive_failures, backoff,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
                return  # stop signaled
            except asyncio.TimeoutError:
                continue

        for update in updates:
            last_update_id = update.get("update_id", last_update_id)
            try:
                async with factory() as session:
                    await handle_update(session, settings, app_state_obj, client, update)
            except Exception:  # noqa: BLE001 - one bad update must not stop the loop
                logger.exception("Failed to handle Telegram update %s", update.get("update_id"))


async def _load_nexus_connection_from_db(nexus: NexusClient, settings: Settings, logger: logging.Logger) -> None:
    """Load the Nexus connection from the dashboard DB if an admin has saved
    one; otherwise the env-provided defaults remain in effect."""
    try:
        from .core.config_store import get_nexus_connection
        factory = get_session_factory()
        async with factory() as session:
            conn = await get_nexus_connection(session, settings)
        # Reconfigure the live client whenever the dashboard config differs from
        # the env defaults. Docker registry endpoints are not part of this —
        # they are discovered per repository at scan time.
        if conn.is_configured() and conn.url != settings.NEXUS_URL:
            await nexus.reconfigure(conn.url, conn.username, conn.password, conn.verify_ssl)
            logger.info("Loaded Nexus connection from dashboard config.")
        elif not conn.is_configured() and not settings.NEXUS_URL:
            logger.warning("No Nexus connection configured — set it via Settings in the UI.")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to load Nexus connection from DB; using env defaults.")


async def _reap_stale_startup_state(cache: Cache, logger: logging.Logger) -> None:
    """Database hygiene, not work: close out scan reports, jobs, and analysis
    runs left mid-flight by a previous process. Each step is independent and
    never blocks startup on failure."""
    try:
        from .modules.nexus import reap_stale_reports
        factory = get_session_factory()
        async with factory() as session:
            await reap_stale_reports(session)
    except Exception:  # noqa: BLE001 - never block startup on housekeeping
        logger.exception("Could not reap interrupted scan reports")

    try:
        # cache was constructed unconditionally by the caller (Cache(settings)),
        # never reassigned since — always non-None here, unlike the
        # module-scoped `_lifespan_state.get("cache")` used elsewhere in this file.
        from .core.jobs import JobQueue
        await JobQueue(cache).reap_stranded()
    except Exception:  # noqa: BLE001 - never block startup on housekeeping
        logger.exception("Could not reap stranded jobs")

    try:
        from .modules.sonar.provisioning import reap_stale_analysis_runs
        factory = get_session_factory()
        async with factory() as session:
            await reap_stale_analysis_runs(session)
    except Exception:  # noqa: BLE001 - never block startup on housekeeping
        logger.exception("Could not reap interrupted analysis runs")


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
    nexus.start()
    await cache.start()

    await _load_nexus_connection_from_db(nexus, settings, logger)

    app.state.nexus = nexus
    app.state.cache = cache

    # Database hygiene, not work: close out scan reports, jobs, and analysis
    # runs left mid-flight by a previous process. This inspects rows only and
    # starts no scans. Also covers the job queue (a job whose worker died
    # stays "running" forever) and the Sonar analysis_runs table specifically
    # (JobQueue.reap_stranded() only fixes the Redis job's own status, not
    # this separate DB row the UI actually reads for the Analysis tab /
    # Project Overview — without it an interrupted analysis shows RUNNING
    # forever).
    await _reap_stale_startup_state(cache, logger)

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
    runner.register("backup_archive", job_handlers.handle_backup_archive)
    runner.register("run_scheduled_backup", job_handlers.handle_run_scheduled_backup)
    runner.register("sync", job_handlers.handle_sync)
    runner.register("scan_image", job_handlers.handle_scan_image)
    runner.register("scanner_db_update", job_handlers.handle_scanner_db_update)
    runner.register("scanner_db_import", job_handlers.handle_scanner_db_import)
    from .workers.analysis_worker import handle_clone_and_analyze
    runner.register("clone_and_analyze", handle_clone_and_analyze)
    from .workers.provisioning_worker import handle_provision_repository
    runner.register("provision_repository", handle_provision_repository)
    runner.start()
    app.state.runner = runner

    # Start the periodic metric-collection loop.
    stop_metric = asyncio.Event()
    metric_task = asyncio.create_task(_metric_loop(settings, stop_metric))

    # Start the daily retention scheduler.
    stop_retention = asyncio.Event()
    retention_task = asyncio.create_task(_retention_scheduler(settings, stop_retention))

    # Start the scanner database maintenance loop.
    stop_scanner_db = asyncio.Event()
    scanner_db_task = asyncio.create_task(_scanner_db_loop(settings, stop_scanner_db))

    # Start the new-image watcher (fallback push trigger; see _push_watch_loop).
    stop_push_watch = asyncio.Event()
    push_watch_task = asyncio.create_task(_push_watch_loop(settings, stop_push_watch))

    # Start the scheduled-backup poll loop.
    stop_backup_schedule = asyncio.Event()
    backup_schedule_task = asyncio.create_task(_backup_schedule_loop(settings, stop_backup_schedule))

    # Start the interval-scheduled retention poll loop (policies with their
    # own interval_minutes, separate from the daily sweep above).
    stop_retention_interval = asyncio.Event()
    retention_interval_task = asyncio.create_task(_retention_interval_loop(settings, stop_retention_interval))

    # Start the Telegram bot's long-poll loop (no-op until a bot token is configured).
    stop_telegram = asyncio.Event()
    telegram_task = asyncio.create_task(_telegram_poll_loop(settings, stop_telegram))

    logger.info("Rotsy v%s started.", __version__)
    try:
        yield
    finally:
        background = (
            metric_task, retention_task, scanner_db_task, push_watch_task,
            backup_schedule_task, retention_interval_task, telegram_task,
        )
        for event in (
            stop_metric, stop_retention, stop_scanner_db, stop_push_watch,
            stop_backup_schedule, stop_retention_interval, stop_telegram,
        ):
            event.set()
        for task in background:
            task.cancel()
        for task in background:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await runner.stop()
        await cache.close()
        await nexus.close()
        logger.info("Rotsy shut down cleanly.")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    app = FastAPI(
        title="Rotsy",
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
            {"name": "scan", "description": "Trivy/Grype static image scanning. Triggered on push "
                                            "(Nexus webhook) or on request — never as a sweep."},
            {"name": "blobstores", "description": "Blobstore management (scaffolded)."},
            {"name": "system", "description": "Backup + Nexus-to-Nexus sync + scripts."},
            {"name": "access", "description": "API tokens, webhooks and anonymous repository access."},
            {"name": "tasks", "description": "Nexus scheduled tasks: list, run, stop."},
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
    app.include_router(tasks.router, prefix="/api")
    app.include_router(metrics.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(github.router, prefix="/api")
    app.include_router(gitlab.router, prefix="/api")
    app.include_router(sonar.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(prometheus.router)  # no prefix — serves at /metrics/export
    app.include_router(alerts.router, prefix="/api")
    app.include_router(audit.router, prefix="/api")
    app.include_router(telegram.router, prefix="/api")
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
