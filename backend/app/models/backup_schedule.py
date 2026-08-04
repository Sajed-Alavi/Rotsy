"""Scheduled backup configuration.

A :class:`BackupSchedule` combines a backup target (``mode``/``repos``, the
same vocabulary as the manual ``POST /system/backup/archive`` endpoint) with a
cadence (``frequency`` plus the fields it uses) and a retention rule for its
own archives. Multiple independently-enabled schedules are supported, the
same shape as :class:`~app.models.RetentionPolicy` — one client might want a
nightly full backup and a separate weekly selective one, each with its own
retention.

``next_run_at`` is precomputed and persisted (by
:func:`app.services.backup_schedule.compute_next_run`) rather than derived on
read, so the poll loop's query is a plain indexed comparison rather than a
cron evaluation per row per tick.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class BackupSchedule(Base):
    __tablename__ = "backup_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Backup target — same vocabulary as BackupArchiveRequest.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # "full" | "selective"
    repos: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list

    # Cadence. Exactly one of the frequency-specific field groups applies,
    # enforced by the API schema, not the DB — see
    # app.routers.system.BackupScheduleCreate.
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)  # daily|weekly|monthly|cron
    time_of_day: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM", daily/weekly/monthly
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Monday .. 6=Sunday, weekly
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-31, monthly (clamped to month length)
    cron_expression: Mapped[str | None] = mapped_column(String(128), nullable=True)  # standard 5-field cron

    # Retention of this schedule's own archives. Both apply when set, same
    # "both conditions apply" semantics as RetentionPolicy.
    retention_keep_last: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_max_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
