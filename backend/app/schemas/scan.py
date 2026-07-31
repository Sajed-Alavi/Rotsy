"""Request/response models for the scanning endpoints.

These lived inline in ``routers/scan.py`` while every other feature kept its
models here (``auth.py``, ``user.py``, ``role.py``). They are now in the same
place as the rest, so the router modules carry routing and the schemas carry
shape.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- Scan targets (per-repository opt-in) -----------------------------------
class TargetCreate(BaseModel):
    repo: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    auto_scan: bool = Field(default=True, description="Scan images pushed from now on")
    scanners: str = Field(default="", max_length=255, description="csv; empty = global default")


class TargetUpdate(BaseModel):
    enabled: bool | None = None
    auto_scan: bool | None = None
    scanners: str | None = Field(default=None, max_length=255)


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repo: str
    enabled: bool
    auto_scan: bool
    scanners: str
    baseline_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --- Manual scan -------------------------------------------------------------
class ScanRequest(BaseModel):
    repo: str = Field(..., min_length=1, description="Nexus Docker repository name")
    image: str = Field(..., min_length=1, description="Image reference within the repo, e.g. nginx:1.25")
    scanners: list[str] | None = Field(default=None, description="Override the enabled scanners")


# --- Reports + findings ------------------------------------------------------
class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_repo: str
    image: str
    scanner: str
    status: str
    registry_ref: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int
    critical: int
    high: int
    medium: int
    low: int
    unknown: int
    error: str | None


class VulnerabilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    repo: str
    scanner: str
    cve: str
    severity: str
    package: str
    installed_version: str
    fixed_version: str
    title: str
    cvss: float


class VulnerabilityPage(BaseModel):
    items: list[VulnerabilityOut]
    total: int
