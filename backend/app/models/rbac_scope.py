"""Image-level RBAC scoping.

  * :class:`RoleImageScope` — restricts a role's access to per-image data
    within one repository to names matching a shell-glob pattern (e.g.
    ``abrisham-frontend*``). A role with no scope rows for a given repo is
    unrestricted there; a role with one or more rows is limited to the union
    of its own patterns. See :mod:`app.core.image_scope` for the enforcement
    logic — a user's *effective* access is the union across their held
    roles, mirroring how effective permissions are already a union across
    roles (one unrestricted role is enough to grant full access).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class RoleImageScope(Base):
    __tablename__ = "role_image_scopes"
    __table_args__ = (UniqueConstraint("role_id", "repo", "pattern", name="uq_role_image_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    pattern: Mapped[str] = mapped_column(String(255), nullable=False)  # shell-glob, e.g. "abrisham-frontend*"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
