"""Sonar module schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models.sonar import SUPPORTED_LANGUAGES


class SonarProjectCreate(BaseModel):
    project_id: int
    language: str = Field(..., description=f"One of: {', '.join(SUPPORTED_LANGUAGES)}")


class SonarProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    sonar_project_key: str
    language: str


class AnalysisRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sonar_project_id: int
    commit_sha: str
    ref: str
    status: str
    trigger: str
    issues_count: int | None
    bugs: int | None
    vulnerabilities: int | None
    code_smells: int | None
    security_hotspots: int | None
    coverage: float | None
    duplication_pct: float | None
    started_at: datetime
    finished_at: datetime | None
    error: str | None


class QualityGateResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_run_id: int
    status: str
    conditions: list
