"""Vulnerability scanning models.

  * ``ScanTarget`` — per-repository enable toggle. When enabled, every new
    image/component pushed to that repo is auto-scanned. The user can also
    re-scan on demand from the UI.
  * ``ScanReport`` — one per (repo, target, scanner) run. Holds the severity
    counts (critical/high/medium/low) + the raw parsed vulnerabilities.
  * ``Vulnerability`` — flattened per-finding row for fast filtering / dashboards.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class ScanTarget(Base):
    """Repository + auto-scan configuration."""

    __tablename__ = "scan_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_scan: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # scan on push
    scanners: Mapped[str] = mapped_column(String(255), default="", nullable=False)  # csv: "trivy,grype"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ScanReport(Base):
    """Result of a single scan run."""

    __tablename__ = "scan_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    image: Mapped[str] = mapped_column(String(512), nullable=False)  # repo/name:tag or component id
    scanner: Mapped[str] = mapped_column(String(32), nullable=False)  # "trivy" | "grype"
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # "success" | "failed" | "running"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    critical: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    high: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    medium: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    low: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unknown: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class Vulnerability(Base):
    """One row per (report, cve) — for filtering + dashboards."""

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
