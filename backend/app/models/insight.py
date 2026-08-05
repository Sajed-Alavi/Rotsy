"""Smart Insights output.

A row is a single, already-correlated finding produced by the
``core/insights`` rule engine (e.g. "quality gate failed", "new issues
introduced"). Insights are denormalized on write — ``evidence`` carries
whatever numbers the rule used to fire — so the dashboard never joins across
module tables to render the feed, and core never needs to understand a
module's schema to read one back.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Insight(Base):
    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # e.g. "quality_gate_failed" | "new_issues_introduced" | "coverage_dropped"
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # LOW|MEDIUM|HIGH|CRITICAL
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    related_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Which module's event produced this insight — "sonar", "nexus", ... Not a
    # foreign key: the module owning the source data can change independently.
    related_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
