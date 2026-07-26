"""Retention policy: rule-based cleanup rules.

A policy combines two optional conditions; BOTH are applied when set:
  * ``keep_last_n`` — keep only the most recent N components per repo.
  * ``delete_older_than_days`` — delete components older than X days.

Policies are evaluated by the periodic scheduler (and can be run on demand
as a background job). Deletion calls Nexus' component DELETE endpoint and
then triggers a compact task so physical blobs are reclaimed too.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    keep_last_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delete_older_than_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
