"""Role + Permission schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    description: str


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    permission_keys: list[str] = Field(default_factory=list)
    image_scope_unrestricted: bool = Field(
        default=True,
        description="If false, this role never grants blanket image access for a repo it has "
                    "no image-scope rows for — it only contributes its own scope rows to a "
                    "user's effective access, even if the user also holds another role that "
                    "would otherwise leave that repo unrestricted.",
    )


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permission_keys: list[str] | None = None
    image_scope_unrestricted: bool | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    is_system: bool
    image_scope_unrestricted: bool
    created_at: datetime
    permissions: list[PermissionOut]


class ImageScopeCreate(BaseModel):
    repo: str = Field(..., min_length=1, max_length=255)
    pattern: str = Field(..., min_length=1, max_length=255, description="Shell-glob, e.g. 'abrisham-frontend*'")


class ImageScopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role_id: int
    repo: str
    pattern: str
    created_at: datetime
