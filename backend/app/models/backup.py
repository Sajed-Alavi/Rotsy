"""Backup archive models.

  * :class:`BackupRun` — one row per archive run (full or selective), tracking
    which repos were included, where the output landed, and its outcome. This
    is what makes a backup a real "archive" rather than a job whose progress
    disappears once its Redis record expires (7 days, see ``core/jobs.py``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class BackupRun(Base):
    """One backup/archive run."""

    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # "full" | "selective"
    repos: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list actually included
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", index=True)  # running|success|failed
    output_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    asset_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
