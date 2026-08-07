"""Sonar module schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models.sonar import SUPPORTED_LANGUAGES


class SonarProjectCreate(BaseModel):
    project_id: int
    language: str = Field(..., description=f"One of: {', '.join(SUPPORTED_LANGUAGES)}")
    quality_gate: str | None = Field(
        default=None,
        description="Name of an existing SonarQube quality gate to assign. Omit to use Rotsy's default gate.",
    )
    github_repository_id: int | None = Field(
        default=None, description="Which repository under the Project this Sonar project is for."
    )
    gitlab_repository_id: int | None = Field(
        default=None, description="Which repository under the Project this Sonar project is for."
    )


class SonarProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    github_repository_id: int | None
    gitlab_repository_id: int | None
    sonar_project_key: str
    language: str
    auto_analyze_enabled: bool
    auto_analyze_branches: list[str]


class SonarProjectUpdate(BaseModel):
    auto_analyze_enabled: bool | None = Field(
        default=None, description="Whether a push should trigger analysis at all for this repository.",
    )
    auto_analyze_branches: list[str] | None = Field(
        default=None,
        description="Branch names a push must match to trigger analysis. Empty list means "
                    "\"the repository's default branch only\". Omit to leave unchanged.",
    )


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


class SonarIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_run_id: int
    issue_key: str
    rule: str
    severity: str
    type: str
    message: str
    component: str
    line: int | None
    status: str
    assignee: str
    author: str
    tags: list
    effort: str
    debt: str
    clean_code_attribute: str | None
    creation_date: datetime | None
    update_date: datetime | None


class SonarIssuePage(BaseModel):
    items: list[SonarIssueOut]
    total: int


class SonarHotspotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_run_id: int
    hotspot_key: str
    component: str
    line: int | None
    message: str
    status: str
    vulnerability_probability: str
    security_category: str
    author: str
    creation_date: datetime | None
    update_date: datetime | None


class SonarHotspotPage(BaseModel):
    items: list[SonarHotspotOut]
    total: int
