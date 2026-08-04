"""Repository- and image-level access rules attached to roles.

One :class:`RoleAccessRule` is a single statement of the form
``<effect> <actions> on <repo_pattern>/<image_pattern>``. See
:mod:`app.core.access_control` for the wildcard grammar and the evaluation
rules — in particular that denies are role-local and that a role with no
matching rule falls back to :attr:`app.models.Role.access_mode`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class RoleAccessRule(Base):
    __tablename__ = "role_access_rules"
    __table_args__ = (
        UniqueConstraint(
            "role_id", "effect", "repo_pattern", "image_pattern", name="uq_role_access_rule"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: "allow" | "deny" — a deny beats an allow within the same role.
    effect: Mapped[str] = mapped_column(String(8), default="allow", nullable=False)
    #: Ant-style glob over repository names, e.g. "*", "prod-*", "docker-hosted".
    repo_pattern: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: Ant-style glob over image display names, e.g. "abrisham*", "team/**".
    image_pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Sorted comma-separated subset of read/scan/delete. Stored as text so the
    #: same schema works on Postgres and on the SQLite used by the test suite.
    actions: Mapped[str] = mapped_column(String(64), default="read", nullable=False)
    #: Free-text note explaining why the rule exists — rules outlive their authors.
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
