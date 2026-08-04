"""Scheduled backup cadence + retention.

Times are handled in UTC throughout (unlike the daily retention sweep, which
runs at a single server-local HH:MM) because ``next_run_at`` is persisted and
compared across process restarts and poll ticks — mixing naive server-local
and aware UTC datetimes across that boundary is the kind of bug that only
shows up once, at a timezone change. The API/UI convert to the operator's
local time for display only.

Called by:
  * :func:`app.services.job_handlers.handle_run_scheduled_backup` — via
    :func:`prune_old_archives`, after the archive itself is created.
  * :func:`app.main._backup_schedule_loop` — via :func:`poll_due_schedules`,
    once per poll tick.
  * ``app.routers.system`` — via :func:`compute_next_run`, both when
    persisting a new/edited schedule's ``next_run_at`` and for the live
    "preview" endpoint.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BackupRun, BackupSchedule

logger = logging.getLogger(__name__)

FREQUENCIES = ("daily", "weekly", "monthly", "cron")


def _parse_time_of_day(value: str | None) -> tuple[int, int]:
    if not value:
        raise ValueError("time_of_day is required for this frequency")
    hh, mm = value.split(":")
    return int(hh), int(mm)


def _next_daily(after: datetime, hour: int, minute: int) -> datetime:
    target = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= after:
        target += timedelta(days=1)
    return target


def _next_weekly(after: datetime, hour: int, minute: int, day_of_week: int) -> datetime:
    target = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (day_of_week - target.weekday()) % 7
    target += timedelta(days=days_ahead)
    if target <= after:
        target += timedelta(days=7)
    return target


def _next_monthly(after: datetime, hour: int, minute: int, day_of_month: int) -> datetime:
    """Next occurrence of ``day_of_month`` (clamped to the month's length).

    A schedule set for day 31 runs on the last day of a shorter month rather
    than being skipped — the alternative (skip months without that day) would
    silently turn a "monthly" schedule into a "sometimes" one.
    """
    def build(year: int, month: int) -> datetime:
        last_day = calendar.monthrange(year, month)[1]
        day = min(day_of_month, last_day)
        return after.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)

    candidate = build(after.year, after.month)
    if candidate <= after:
        year, month = after.year, after.month + 1
        if month > 12:
            month = 1
            year += 1
        candidate = build(year, month)
    return candidate


def compute_next_run(schedule: BackupSchedule, *, after: datetime | None = None) -> datetime:
    """Return the next UTC run time for ``schedule``, strictly after ``after``."""
    after = after or datetime.now(timezone.utc)
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)

    if schedule.frequency == "cron":
        if not schedule.cron_expression:
            raise ValueError("cron frequency requires cron_expression")
        return croniter(schedule.cron_expression, after).get_next(datetime)
    if schedule.frequency == "daily":
        hour, minute = _parse_time_of_day(schedule.time_of_day)
        return _next_daily(after, hour, minute)
    if schedule.frequency == "weekly":
        if schedule.day_of_week is None:
            raise ValueError("weekly frequency requires day_of_week (0=Monday..6=Sunday)")
        hour, minute = _parse_time_of_day(schedule.time_of_day)
        return _next_weekly(after, hour, minute, schedule.day_of_week)
    if schedule.frequency == "monthly":
        if schedule.day_of_month is None:
            raise ValueError("monthly frequency requires day_of_month (1-31)")
        hour, minute = _parse_time_of_day(schedule.time_of_day)
        return _next_monthly(after, hour, minute, schedule.day_of_month)
    raise ValueError(f"unknown frequency {schedule.frequency!r}")


async def poll_due_schedules(cache, session: AsyncSession) -> list[str]:
    """Enqueue a ``run_scheduled_backup`` job for every due, enabled schedule.

    ``next_run_at`` is recomputed and persisted immediately after enqueuing —
    before the job itself has run — so a slow poll tick (or a job that takes
    longer than the poll interval) can't cause the same schedule to fire twice.
    """
    from ..core.jobs import JobQueue

    now = datetime.now(timezone.utc)
    rows = (await session.execute(
        select(BackupSchedule).where(
            BackupSchedule.enabled.is_(True),
            BackupSchedule.next_run_at.is_not(None),
            BackupSchedule.next_run_at <= now,
        )
    )).scalars().all()

    enqueued: list[str] = []
    queue = JobQueue(cache)
    for schedule in rows:
        try:
            job_id = await queue.enqueue("run_scheduled_backup", {"schedule_id": schedule.id})
        except Exception:  # noqa: BLE001 - one schedule must not block the others
            logger.exception("Failed to enqueue scheduled backup for schedule %s", schedule.id)
            continue
        enqueued.append(job_id)
        schedule.last_run_at = now
        try:
            schedule.next_run_at = compute_next_run(schedule, after=now)
        except ValueError:
            logger.exception("Could not compute next run for schedule %s; disabling it", schedule.id)
            schedule.enabled = False
            schedule.next_run_at = None

    if rows:
        await session.commit()
    return enqueued


async def prune_old_archives(session: AsyncSession, schedule: BackupSchedule) -> dict:
    """Delete this schedule's own archives beyond its retention rule.

    Mirrors :mod:`app.services.retention`'s "both conditions apply when set"
    semantics: an archive is deleted if it's older than
    ``retention_max_age_days`` *or* falls outside the newest
    ``retention_keep_last`` runs. Manual/on-demand runs (``schedule_id`` NULL)
    are never touched here — a schedule only ever prunes archives it created.
    """
    if schedule.retention_keep_last is None and schedule.retention_max_age_days is None:
        return {"deleted": 0}

    rows = (await session.execute(
        select(BackupRun)
        .where(BackupRun.schedule_id == schedule.id, BackupRun.status == "success")
        .order_by(BackupRun.finished_at.desc())
    )).scalars().all()

    to_delete: dict[int, BackupRun] = {}
    if schedule.retention_max_age_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=schedule.retention_max_age_days)
        for run in rows:
            if run.finished_at is not None and run.finished_at < cutoff:
                to_delete[run.id] = run
    if schedule.retention_keep_last is not None and schedule.retention_keep_last >= 0:
        for run in rows[schedule.retention_keep_last:]:
            to_delete[run.id] = run

    for run in to_delete.values():
        if run.output_path:
            path = Path(run.output_path)
            try:
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    import shutil
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                logger.exception("Could not remove pruned backup archive at %s", path)
        await session.delete(run)

    if to_delete:
        await session.commit()
    return {"deleted": len(to_delete)}
