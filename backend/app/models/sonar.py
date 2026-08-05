"""SonarQube analysis state.

Owned by ``modules/sonar``. ``analysis_runs.commit_sha`` is a plain string,
not a foreign key into ``github_repositories`` — an analysis run must be
describable without knowing which source module produced the commit (GitLab
will produce commits too, later).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

# MVP language allowlist — see app/modules/sonar/scanner.py. No build step is
# run, so only languages sonar-scanner can analyze from source alone qualify.
SUPPORTED_LANGUAGES = ("python", "javascript", "typescript")


class SonarProject(Base):
    __tablename__ = "sonar_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    sonar_project_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("sonar_project_id", "commit_sha", name="uq_analysis_runs_project_sha"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sonar_project_id: Mapped[int] = mapped_column(
        ForeignKey("sonar_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|running|success|failed
    issues_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    duplication_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class QualityGateResult(Base):
    __tablename__ = "quality_gate_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # OK|ERROR|WARN
    conditions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
