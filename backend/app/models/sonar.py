"""SonarQube analysis state.

Owned by ``modules/sonar``. ``analysis_runs.commit_sha`` is a plain string,
not a foreign key into ``github_repositories`` — an analysis run must be
describable without knowing which source module produced the commit (GitLab
will produce commits too, later).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

# Language allowlist — see app/modules/sonar/scanner.py. Rotsy never runs a
# build (no compiler, no bytecode, no build-wrapper), so this is limited to
# languages SonarQube's analyzers can process directly from source. That
# reliably covers this list; it deliberately excludes Java, C#, C/C++,
# Kotlin, and Scala, which need a real build (bytecode or a build-wrapper
# trace) for SonarQube to analyze correctly — claiming to "support" those
# without a build step would silently produce incomplete or misleading
# results, not just a smaller feature set.
SUPPORTED_LANGUAGES = (
    "python", "javascript", "typescript", "go", "php", "ruby", "css", "html",
)


class SonarProject(Base):
    """One per connected *repository*, not one per Rotsy Project.

    A Project is a grouping — it can hold many repositories (17, 1000,
    whatever), each independently analyzed, potentially in entirely
    different languages. ``project_id`` says which Project this repo's
    analysis is grouped under; ``github_repository_id`` /
    ``gitlab_repository_id`` (exactly one populated) says which repository
    it actually is. Each is individually unique, so a repository gets at
    most one SonarProject no matter how many times it's reconnected.
    """

    __tablename__ = "sonar_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    github_repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("github_repositories.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    gitlab_repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("gitlab_repositories.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    sonar_project_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    # Whether a push (via GitHub App webhook / GitLab per-repo webhook)
    # triggers analysis at all for this repository. Independent of whether
    # the webhook mechanism itself exists (a GitHub App installation, or a
    # registered GitLab webhook) — that only says a push *can* be delivered,
    # this says whether Rotsy should act on it. Defaults on so existing
    # behavior (every push analyzed) doesn't change for anyone who never
    # touches this setting.
    auto_analyze_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Branch names a push must match to trigger analysis. Empty list means
    # "the repository's own default branch only" — the safe default: every
    # other branch requires SonarQube Developer Edition+ (see scanner.py),
    # so defaulting to "watch everything" would silently start failing
    # analysis runs for anyone on Community Edition the first time a
    # feature branch was pushed.
    auto_analyze_branches: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
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
    # "push" | "pull_request" | "manual" | "connect" — how this run was started. Runs
    # go through the exact same job handler; this column only records which
    # one asked for it, for the analysis-history view.
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="push")
    issues_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bugs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vulnerabilities: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code_smells: Mapped[int | None] = mapped_column(Integer, nullable=True)
    security_hotspots: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class SonarIssue(Base):
    """One row per (analysis run, Sonar issue) — bugs, vulnerabilities, and
    code smells alike, distinguished by ``type``. Mirrors
    :class:`~app.models.scans.Vulnerability`'s per-finding-row shape so the
    two modules' findings tables/filters/exports work the same way.

    Replaced wholesale on every (re-)analysis of the same commit — see
    ``workers/analysis_worker.py`` — rather than diffed against the previous
    run, since Sonar's own issue ``key`` is not guaranteed stable across a
    project's lifetime (e.g. across a gate/rule-set change) and Rotsy has no
    need to track an issue's history across commits, only what the latest
    analyzed commit found.
    """

    __tablename__ = "sonar_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issue_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rule: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO", index=True)  # BLOCKER|CRITICAL|MAJOR|MINOR|INFO
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="CODE_SMELL", index=True)  # BUG|VULNERABILITY|CODE_SMELL
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    component: Mapped[str] = mapped_column(String(1024), nullable=False, default="")  # file path (component key, project prefix stripped)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    assignee: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    effort: Mapped[str] = mapped_column(String(32), nullable=False, default="")  # e.g. "5min" — Sonar's own formatted effort
    debt: Mapped[str] = mapped_column(String(32), nullable=False, default="")  # technical debt, same formatted-duration shape
    clean_code_attribute: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    update_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SonarHotspot(Base):
    """One row per (analysis run, Sonar security hotspot).

    Kept separate from :class:`SonarIssue` because Sonar's own API keeps
    hotspots on a distinct endpoint (``/api/hotspots/search``) with a
    different shape — a hotspot is "needs a human security review", not a
    severity-ranked defect, so it doesn't have ``severity``/``type`` at all.
    """

    __tablename__ = "sonar_hotspots"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hotspot_key: Mapped[str] = mapped_column(String(64), nullable=False)
    component: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="")  # TO_REVIEW|REVIEWED
    vulnerability_probability: Mapped[str] = mapped_column(String(16), nullable=False, default="")  # HIGH|MEDIUM|LOW
    security_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    creation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    update_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
