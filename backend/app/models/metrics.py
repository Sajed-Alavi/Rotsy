"""Historical metric samples + alert rules.

One row per (timestamp, repo, metric_type). The periodic collector writes a
batch every few minutes; the metrics router reads them back as timeseries for
the dashboard charts. Retention is trimmed in-code to ``METRIC_RETENTION_DAYS``
(see config) to keep the table bounded.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Metric(Base):
    """A single metric sample at a point in time for a repository.

    ``value_json`` holds a JSON object so we can store multiple related fields
    (e.g. ``{"total_bytes": 123, "asset_count": 5}``) per row without a wide
    table. Routers unpack it for the frontend.
    """

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "storage"
    value_json: Mapped[str] = mapped_column(Text, nullable=False)


class AlertRule(Base):
    """A user-defined alert that fires when a metric crosses a threshold.

    Example: metric="storage.wasted", condition=">", threshold=5368709120
    → posts to webhook_url when wasted space in any matching repo exceeds 5GB.
    ``repo_filter`` is a SQL LIKE pattern (``%`` matches any repo when null).
    """

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "storage.wasted"
    condition: Mapped[str] = mapped_column(String(2), nullable=False)  # ">", "<", "=="
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    repo_filter: Mapped[str | None] = mapped_column(String(255), nullable=True)  # LIKE pattern; NULL = all
    webhook_url: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Last trigger tracking to avoid webhook spam.
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
