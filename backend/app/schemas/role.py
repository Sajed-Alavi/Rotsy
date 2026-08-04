"""Role, permission and access-rule schemas."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.access_control import ACCESS_MODES, ACTIONS, EFFECTS, parse_actions

# Repository and image names are built from these characters; the wildcards are
# the grammar of app.core.access_control. ':' and '@' are included because the
# scan ledger matches against "name:tag" and digest-pinned references, not the
# bare image name. Rejecting anything else stops a stray space or quote from
# being stored as a rule that then silently never matches.
_PATTERN_CHARS = re.compile(r"^[A-Za-z0-9._:@+/*?-]+$")
_MAX_WILDCARDS = 20


def _clean_pattern(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} cannot be empty.")
    if not _PATTERN_CHARS.match(value):
        raise ValueError(
            f"{field} may contain only letters, digits, '.', '_', '-', '/', ':', '@', "
            f"'+' and the wildcards '*', '**' and '?'."
        )
    # Compiled patterns are alternation-free so backtracking stays tame, but an
    # unbounded wildcard count is still needless work on every match.
    if value.count("*") + value.count("?") > _MAX_WILDCARDS:
        raise ValueError(f"{field} may contain at most {_MAX_WILDCARDS} wildcards.")
    return value


def _clean_actions(value: list[str]) -> list[str]:
    unknown = sorted(set(value) - set(ACTIONS))
    if unknown:
        raise ValueError(f"Unknown actions: {unknown}. Valid actions: {list(ACTIONS)}.")
    if not value:
        raise ValueError("A rule must grant at least one action.")
    return [action for action in ACTIONS if action in set(value)]


def _clean_mode(value: str) -> str:
    if value not in ACCESS_MODES:
        raise ValueError(f"access_mode must be one of {list(ACCESS_MODES)}.")
    return value


def _clean_effect(value: str) -> str:
    if value not in EFFECTS:
        raise ValueError(f"effect must be one of {list(EFFECTS)}.")
    return value


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    description: str


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    permission_keys: list[str] = Field(default_factory=list)
    access_mode: str = Field(
        default="unrestricted",
        description="'unrestricted': repositories that none of this role's rules match stay "
                    "fully accessible — the default, and how roles behaved before access "
                    "rules existed. 'scoped': deny-by-default, so the role grants only what "
                    "its own rules allow and can never widen a user's access by accident.",
    )

    @field_validator("access_mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        return _clean_mode(value)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permission_keys: list[str] | None = None
    access_mode: str | None = None

    @field_validator("access_mode")
    @classmethod
    def _validate_mode(cls, value: str | None) -> str | None:
        return None if value is None else _clean_mode(value)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    is_system: bool
    access_mode: str
    created_at: datetime
    permissions: list[PermissionOut]


class AccessRuleCreate(BaseModel):
    effect: str = Field(default="allow", description="'allow' or 'deny'. A deny wins within a role.")
    repo_pattern: str = Field(..., max_length=255, description="e.g. '*', 'prod-*', 'docker-hosted'")
    image_pattern: str = Field(..., max_length=255, description="e.g. 'abrisham*', 'team/**'")
    actions: list[str] = Field(
        default_factory=lambda: ["read"], description=f"Any subset of {list(ACTIONS)}."
    )
    description: str = Field(default="", max_length=255)

    @field_validator("effect")
    @classmethod
    def _validate_effect(cls, value: str) -> str:
        return _clean_effect(value)

    @field_validator("repo_pattern")
    @classmethod
    def _validate_repo(cls, value: str) -> str:
        return _clean_pattern(value, "repo_pattern")

    @field_validator("image_pattern")
    @classmethod
    def _validate_image(cls, value: str) -> str:
        return _clean_pattern(value, "image_pattern")

    @field_validator("actions")
    @classmethod
    def _validate_actions(cls, value: list[str]) -> list[str]:
        return _clean_actions(value)


class AccessRuleUpdate(BaseModel):
    """Every field optional; omitted fields keep their stored value."""

    effect: str | None = None
    repo_pattern: str | None = Field(default=None, max_length=255)
    image_pattern: str | None = Field(default=None, max_length=255)
    actions: list[str] | None = None
    description: str | None = Field(default=None, max_length=255)

    @field_validator("effect")
    @classmethod
    def _validate_effect(cls, value: str | None) -> str | None:
        return None if value is None else _clean_effect(value)

    @field_validator("repo_pattern")
    @classmethod
    def _validate_repo(cls, value: str | None) -> str | None:
        return None if value is None else _clean_pattern(value, "repo_pattern")

    @field_validator("image_pattern")
    @classmethod
    def _validate_image(cls, value: str | None) -> str | None:
        return None if value is None else _clean_pattern(value, "image_pattern")

    @field_validator("actions")
    @classmethod
    def _validate_actions(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_actions(value)


class AccessRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role_id: int
    effect: str
    repo_pattern: str
    image_pattern: str
    actions: list[str]
    description: str
    created_at: datetime

    @field_validator("actions", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        """The column stores a comma-separated string; the API speaks lists."""
        if isinstance(value, str):
            held = parse_actions(value)
            return [action for action in ACTIONS if action in held]
        return value


class AccessRuleTest(BaseModel):
    """Ask what a role's rules do to one concrete repository/image pair."""

    repo: str = Field(..., min_length=1, max_length=255)
    image: str = Field(..., min_length=1, max_length=255)


class RuleMatchOut(BaseModel):
    """A rule whose repository pattern matched during a test."""

    rule_id: int
    role_id: int
    effect: str
    repo_pattern: str
    image_pattern: str
    actions: list[str]
    #: False when the rule applies to the repository but its image pattern missed.
    matched_image: bool


class AccessTestResult(BaseModel):
    repo: str
    image: str
    #: True when nothing restricts this repository, so the rules never get consulted.
    unrestricted: bool
    allowed_actions: list[str]
    matched_rules: list[RuleMatchOut]


class EffectiveAccessOut(AccessTestResult):
    """A whole user's decided access, plus the per-role reasoning behind it."""

    by_role: list[RoleAccessBreakdown]


class RoleAccessBreakdown(BaseModel):
    role_id: int
    role_name: str
    access_mode: str
    allowed_actions: list[str]
    matched_rules: list[RuleMatchOut]


EffectiveAccessOut.model_rebuild()
