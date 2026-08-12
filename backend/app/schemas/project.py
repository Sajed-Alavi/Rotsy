"""Project schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.project_access import PROJECT_ROLES


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    updated_at: datetime


class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    module_key: str
    kind: str
    status: str
    created_at: datetime


class IntegrationConnect(BaseModel):
    module_key: str = Field(..., min_length=1, max_length=32)
    config: dict = Field(default_factory=dict)
    credential_ref: str | None = None


class HealthScoreOut(BaseModel):
    score: int
    factors: list[str]
    has_data: bool


class InsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    kind: str
    severity: str
    title: str
    evidence: dict
    related_commit_sha: str | None
    related_source: str | None
    created_at: datetime


def _validate_project_role(v: str) -> str:
    if v not in PROJECT_ROLES:
        raise ValueError(f"project_role must be one of {PROJECT_ROLES}")
    return v


class ProjectMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    user_id: int
    username: str
    email: str
    project_role: str
    created_at: datetime


class ProjectMemberCreate(BaseModel):
    user_id: int
    project_role: str = "viewer"

    _validate_role = field_validator("project_role")(_validate_project_role)


class ProjectMemberUpdate(BaseModel):
    project_role: str

    _validate_role = field_validator("project_role")(_validate_project_role)


class UserCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
