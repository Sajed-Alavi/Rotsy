"""API tokens, webhooks and anonymous repository access.

Was three endpoints returning 501. The pieces were mostly present but scattered:
the scan webhook lived under Settings, alert webhooks under Alerts, and
anonymous access existed only as a checkbox on the repository-create form with
no way to see or change it afterwards. This router is where they meet.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config_store import get_or_create_webhook_secret
from ..core.nexus_client import NexusClient
from ..dependencies import RequirePermission, get_current_user, get_session
from ..models import User
from ..services import access_tokens as token_service
from ..services import nexus_security
from ..state import app_state

router = APIRouter(prefix="/access", tags=["access"])

# Ceiling on token lifetime. A token that never expires is a credential nobody
# ever revisits; a year is long enough for a real pipeline and short enough that
# forgotten tokens age out.
_MAX_TOKEN_DAYS = 365


async def _nexus(request: Request) -> NexusClient:
    client = app_state(request).nexus
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not initialised")
    return client


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
class TokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128,
                      description="What this token is for, e.g. 'gitlab-ci scan trigger'")
    scopes: list[str] = Field(
        default_factory=list,
        description="Permission keys this token may use. Empty means the owner's full permission "
                    "set. Scopes can only narrow, never widen — the effective set is the "
                    "intersection with the owner's current permissions, resolved per request.",
    )
    expires_in_days: int | None = Field(
        default=90, ge=1, le=_MAX_TOKEN_DAYS,
        description=f"Lifetime in days (1-{_MAX_TOKEN_DAYS}). Null issues a non-expiring token.",
    )

    @field_validator("scopes")
    @classmethod
    def _clean_scopes(cls, value: list[str]) -> list[str]:
        return sorted({s.strip() for s in value if s and s.strip()})


class TokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    scopes: str
    owner_id: int
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked: bool
    revoked_at: datetime | None


@router.post("/tokens", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("access:write"))])
async def create_token(
    body: TokenCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Mint an API token.

    The response carries the plaintext token. This is the **only** time it is
    ever available — only a SHA-256 hash is stored, so it cannot be recovered
    or re-displayed later, only replaced.
    """
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        if body.expires_in_days else None
    )
    token, plaintext = await token_service.create_token(
        session, name=body.name, owner_id=user.id, scopes=body.scopes, expires_at=expires_at,
    )
    return {
        "token": plaintext,
        "warning": "Copy this now — it is not stored and cannot be shown again.",
        "record": TokenOut.model_validate(token).model_dump(),
    }


@router.get("/tokens", dependencies=[Depends(RequirePermission("access:read"))])
async def list_tokens(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TokenOut]:
    """Tokens visible to the caller.

    Admins (``users:manage``) see every token so that a leaver's credentials are
    discoverable; everyone else sees only their own.
    """
    perms = getattr(user, "_effective_permissions", [])
    owner = None if "users:manage" in perms else user.id
    rows = await token_service.list_tokens(session, owner_id=owner)
    return [TokenOut.model_validate(r) for r in rows]


@router.delete("/tokens/{token_id}", dependencies=[Depends(RequirePermission("access:write"))])
async def revoke_token(
    token_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Revoke a token. Takes effect on the next request that presents it."""
    perms = getattr(user, "_effective_permissions", [])
    if "users:manage" not in perms:
        owned = {t.id for t in await token_service.list_tokens(session, owner_id=user.id)}
        if token_id not in owned:
            # Same answer as a genuinely missing id, so a non-admin cannot probe
            # for the existence of other people's tokens.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")

    if not await token_service.revoke_token(session, token_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found or already revoked")
    return {"revoked": True, "id": token_id}


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
@router.get("/webhooks", dependencies=[Depends(RequirePermission("access:read"))])
async def list_webhooks(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Every webhook this app participates in, inbound and outbound.

    They were previously only visible in two unrelated places (Settings for the
    inbound scan hook, Alerts for outbound delivery), so no single page answered
    "what is wired up?".
    """
    settings = app_state(request).settings
    secret = await get_or_create_webhook_secret(session, settings)

    return {
        "inbound": [{
            "name": "Nexus push events",
            "direction": "inbound",
            "path": "/api/scan/events/nexus",
            "purpose": "Nexus calls this on a docker push; it triggers a scan of the new image.",
            "auth": "HMAC-SHA1 over the raw body, in X-Nexus-Webhook-Signature",
            "configured": bool(secret),
            "setup_hint": "Nexus → Administration → System → Capabilities → Webhook: Repository",
            "manage_at": "/settings",
        }],
        "outbound": [{
            "name": "Alert delivery",
            "direction": "outbound",
            "purpose": "Alert rules POST to their configured URL when a condition matches.",
            "configured_per_rule": True,
            "manage_at": "/alerts",
            "note": "Destinations are validated by the SSRF guard: loopback, private, link-local "
                    "and cloud-metadata addresses are refused at both save and send time.",
        }],
    }


# ---------------------------------------------------------------------------
# Anonymous access
# ---------------------------------------------------------------------------
class AnonymousGrant(BaseModel):
    repo: str = Field(..., min_length=1, max_length=255)
    repo_format: str = Field(default="docker", max_length=32)


@router.get("/anonymous", dependencies=[Depends(RequirePermission("access:read"))])
async def anonymous_status(request: Request) -> dict[str, Any]:
    """Whether anonymous access is on globally, and which repos grant it.

    The global flag was already read by ``/api/metrics/system`` but never shown,
    and per-repository grants could only be made at creation time — so a repo
    made public by accident could not be found from here, let alone fixed.
    """
    nexus = await _nexus(request)
    return await nexus_security.anonymous_overview(nexus)


@router.post("/anonymous/grant", dependencies=[Depends(RequirePermission("access:write"))])
async def grant_anonymous(request: Request, body: AnonymousGrant) -> dict[str, Any]:
    """Let unauthenticated clients browse and read one repository."""
    nexus = await _nexus(request)
    return await nexus_security.grant_anonymous_access(nexus, body.repo, body.repo_format)


@router.post("/anonymous/revoke", dependencies=[Depends(RequirePermission("access:write"))])
async def revoke_anonymous(request: Request, body: AnonymousGrant) -> dict[str, Any]:
    """Remove the anonymous read grant from one repository."""
    nexus = await _nexus(request)
    return await nexus_security.revoke_anonymous_access(nexus, body.repo)
