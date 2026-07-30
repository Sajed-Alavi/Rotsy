"""Vulnerability scanning models.

  * :class:`ScanTarget` — per-repository opt-in. ``auto_scan`` enables scanning
    of newly pushed images; ``baseline_at`` records when the repository's
    existing contents were adopted as history (and therefore deliberately left
    unscanned).
  * :class:`ScannedImage` — the durable ledger of every image the system knows
    about, and what has happened to it. This is what makes scanning
    event-driven: an image is scanned when it first appears *after* the baseline,
    or when an operator explicitly asks. Nothing is ever re-scanned because a
    cache expired or a process restarted.
  * :class:`ScanReport` — one row per (image, scanner) run, including failures,
    with the reason and the command detail attached.
  * :class:`Vulnerability` — one row per finding, for filtering and dashboards.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class ScanTarget(Base):
    """Repository-level scanning configuration."""

    __tablename__ = "scan_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Scan images that arrive from now on. Never a trigger to scan history.
    auto_scan: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    scanners: Mapped[str] = mapped_column(String(255), default="", nullable=False)  # csv: "trivy,grype"
    # When the repository's pre-existing images were recorded as history. Set
    # once, the first time the repository is observed; images already present at
    # that moment are never auto-scanned.
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ScannedImage(Base):
    """Durable per-image ledger — the source of truth for "has this been seen".

    Deduplication lives in Postgres rather than in a cache with a TTL. The
    previous implementation marked images scanned in Redis for 24 hours, so every
    image in every enabled repository was re-scanned once a day and again
    whenever Redis restarted.

    ``digest`` is the image's manifest digest when Nexus reports one. A tag that
    is re-pushed with new content gets a new digest, which is what distinguishes
    a genuine new push from the tag we already scanned.
    """

    __tablename__ = "scan_image_ledger"
    __table_args__ = (UniqueConstraint("repo", "image", name="uq_scan_ledger_repo_image"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    image: Mapped[str] = mapped_column(String(512), nullable=False)  # name:tag
    digest: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # baseline — present before scanning was enabled, intentionally never scanned
    # queued   — a scan job is in flight
    # scanned  — scanned successfully at least once
    # failed   — the last scan attempt failed
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="baseline", index=True)
    # baseline | push | webhook | manual — why this image entered the ledger
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="baseline")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_job_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    scan_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ScanReport(Base):
    """Result of a single scanner run against a single image."""

    __tablename__ = "scan_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    image: Mapped[str] = mapped_column(String(512), nullable=False)  # name:tag
    scanner: Mapped[str] = mapped_column(String(32), nullable=False)  # "trivy" | "grype"
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # running | success | failed
    # The exact reference the scanner was pointed at, as resolved by discovery.
    # Recorded so a failure can be diagnosed without re-running discovery.
    registry_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    critical: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    high: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    medium: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    low: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unknown: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Single-sentence failure reason, surfaced directly in the UI.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Command line, exit code and output tail for deeper diagnosis.
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class Vulnerability(Base):
    """One row per (report, CVE) — for filtering and dashboards."""

    __tablename__ = "scan_vulnerabilities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scan_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    scanner: Mapped[str] = mapped_column(String(32), nullable=False)
    cve: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # CRITICAL|HIGH|MEDIUM|LOW|UNKNOWN
    package: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    installed_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    fixed_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    cvss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
