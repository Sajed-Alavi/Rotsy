"""Integration registry rows.

One row per (project, module) connection — "this project's source is
GitHub repo X", "this project's analysis engine is this Sonar server". The
row itself is vendor-agnostic: ``module_key`` and ``kind`` are plain strings
matched against the :class:`~app.core.integrations.ModuleManifest` registry,
and ``config`` holds only non-secret settings. Actual credentials live in the
existing :class:`~app.models.access_token.AccessToken` table (or a future
per-module secret store) and are referenced by ``credential_ref``, never
stored here in the clear.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("project_id", "module_key", name="uq_integrations_project_module"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "github" | "gitlab" | "sonar" | "artifact_registry" — matched against the module registry.
    module_key: Mapped[str] = mapped_column(String(32), nullable=False)
    # "source" | "analysis_engine" | "artifact_registry"
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
