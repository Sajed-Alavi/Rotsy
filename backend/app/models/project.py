"""Core project entity.

A :class:`Project` is the anchor that :class:`~app.models.integration.Integration`
rows (GitHub/GitLab/Sonar/Nexus connections) and, later, module-owned tables
(e.g. ``github_repositories.project_id``) hang off. Core owns this table and
knows nothing about what a GitHub repository or a Sonar project key look like
— those live in their respective modules.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
