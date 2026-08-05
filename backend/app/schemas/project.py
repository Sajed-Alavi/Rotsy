"""Project schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
