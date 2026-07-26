"""Auth-related schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class RoleBrief(BaseModel):
    """Minimal role info embedded in user responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_system: bool


class MeResponse(BaseModel):
    """Response shape for ``GET /auth/me`` — drives the whole frontend."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    is_active: bool
    roles: list[RoleBrief]
    permissions: list[str]  # flattened, deduplicated union across roles
