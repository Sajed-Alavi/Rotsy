"""Per-project access grants.

A :class:`ProjectMember` is the only thing that decides whether a user can
see or act on a given :class:`~app.models.project.Project`. The global
``projects:read``/``projects:write`` permissions (see
``core/permissions.py``) answer "may this user touch projects at all" —
this table answers "which ones". See :mod:`app.core.project_access` for the
role hierarchy and enforcement.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # "viewer" | "member" | "admin" — see app.core.project_access.PROJECT_ROLES
    project_role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
