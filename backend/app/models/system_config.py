"""System-level configuration stored in the database.

Currently holds the Nexus connection (URL, username, password) that admins
edit from the dashboard. The password is stored encrypted
(:mod:`app.core.config_store`). This table is a simple key→JSON-blob store so
we can add more dashboard-managed settings later without a new migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class SystemConfig(Base):
    """A single configuration row, addressed by ``key``."""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
