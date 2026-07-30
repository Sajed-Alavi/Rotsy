"""Settings: self-service profile + Nexus connection (dashboard-managed).

Profile/password endpoints are open to any authed user (``profile:edit``).
Nexus connection endpoints require ``system:execute`` (admin) and let the
admin set/test the Nexus URL + credentials at runtime — no restart needed.
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import RequirePermission, get_current_user, get_session, get_settings
from ..config import Settings
from ..core.security import hash_password, verify_password
from ..core.config_store import nexus_connection_masked, save_nexus_connection
from ..models import User
from ..schemas.auth import MeResponse, RoleBrief
from ..services import registry
from ..state import app_state


router = APIRouter(prefix="/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Profile (self-service)
# ---------------------------------------------------------------------------
class ProfileUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=64)
    email: EmailStr | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


def _me(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        roles=[RoleBrief(id=r.id, name=r.name, is_system=r.is_system) for r in user.roles],
        permissions=getattr(user, "_effective_permissions", []),
    )


@router.get("/profile", response_model=MeResponse)
async def get_profile(user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    return _me(user)


@router.patch("/profile", response_model=MeResponse)
async def update_profile(
    body: ProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    if body.username is not None and body.username != user.username:
        clash = await session.scalar(select(User).where(User.username == body.username))
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
        user.username = body.username
    if body.email is not None and body.email != user.email:
        clash = await session.scalar(select(User).where(User.email == body.email))
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")
        user.email = body.email
    await session.commit()
    await session.refresh(user)
    return _me(user)


@router.post("/password", status_code=status.HTTP_200_OK)
async def change_password(
    body: PasswordChange,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    user.password_hash = hash_password(body.new_password)
    await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Nexus connection (dashboard-managed, admin only)
# ---------------------------------------------------------------------------
class NexusConnectionUpdate(BaseModel):
    url: str = Field(..., min_length=4, max_length=512)
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256, description="Leave as the current value to keep it unchanged")
    verify_ssl: bool = Field(
        default=True,
        description="Verify TLS certificates on the Nexus REST connection. Does not affect how "
                    "scanners reach Docker connectors — that is derived per repository from the "
                    "connector Nexus reports.",
    )


@router.get("/nexus", dependencies=[Depends(RequirePermission("system:execute"))])
async def get_nexus_settings(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """Return the stored Nexus connection with the password masked."""
    return await nexus_connection_masked(session)


@router.put("/nexus", dependencies=[Depends(RequirePermission("system:execute"))])
async def update_nexus_settings(
    request: Request,
    body: NexusConnectionUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Save the Nexus connection and reconfigure the live client immediately.

    The password is stored encrypted at rest. No restart required — the running
    NexusClient is swapped to the new URL/creds atomically, and the Docker
    registry discovery cache is dropped so the next scan re-discovers connector
    ports against the new target.
    """
    await save_nexus_connection(
        session, settings, body.url, body.username, body.password, body.verify_ssl,
    )
    # Reconfigure the live client so subsequent requests use the new target.
    nexus = app_state(request).nexus
    if nexus is not None:
        await nexus.reconfigure(body.url, body.username, body.password, body.verify_ssl)
    await registry.invalidate(app_state(request).cache)
    return {"ok": True}


@router.post("/nexus/test", dependencies=[Depends(RequirePermission("system:execute"))])
async def test_nexus_connection(body: NexusConnectionUpdate) -> dict[str, Any]:
    """Try to reach ``/service/rest/v1/status`` with the provided creds.

    Does NOT touch the stored config — use this to validate before saving.
    Returns a human-friendly error string so the UI can show *why* it failed
    (DNS, connection refused, auth, etc.).
    """
    # Basic shape validation up front so we get a clean message instead of an
    # httpx stack trace for obvious mistakes.
    url = (body.url or "").strip()
    if not url:
        return {"ok": False, "error": "URL is required."}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL must start with http:// or https://"}

    try:
        async with httpx.AsyncClient(
            base_url=url.rstrip("/"),
            auth=(body.username, body.password),
            verify=body.verify_ssl,
            timeout=httpx.Timeout(10.0),
        ) as client:
            resp = await client.get("/service/rest/v1/status")
    except httpx.ConnectError as exc:
        # The most common case: can't reach the host. Give a targeted hint.
        return {"ok": False, "error": f"Cannot connect to {url}: {exc}. If Nexus is on the Docker host, use http://host.docker.internal:8081 instead of 127.0.0.1 or localhost."}
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Timed out connecting to {url}."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    if resp.status_code == 401:
        return {"ok": False, "status_code": 401, "error": "Authentication failed — wrong username or password."}
    if resp.status_code == 404:
        return {"ok": False, "status_code": 404, "error": "Reached the host but /service/rest/v1/status was not found — is this a Nexus instance?"}
    ok = resp.status_code < 500
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "version": resp.text.strip().split("/")[0] if resp.status_code == 200 else None,
        "error": None if ok else f"Nexus returned HTTP {resp.status_code}",
    }


# ---------------------------------------------------------------------------
# Scanner proxy config (stored in system_config, dashboard-managed)
# ---------------------------------------------------------------------------
class ScannerProxyUpdate(BaseModel):
    proxy: str = Field(default="", max_length=512, description="HTTP/HTTPS proxy URL, empty for direct")


@router.get("/scanner-proxy", dependencies=[Depends(RequirePermission("system:execute"))])
async def get_scanner_proxy(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Return the configured scanner proxy (empty = direct)."""
    from sqlalchemy import select as sa_select
    from ..models import SystemConfig
    row = await session.scalar(sa_select(SystemConfig).where(SystemConfig.key == "scanner_proxy"))
    if row is None:
        return {"proxy": ""}
    try:
        import json
        return {"proxy": json.loads(row.value_json).get("proxy", "")}
    except (json.JSONDecodeError, TypeError):
        return {"proxy": ""}


@router.put("/scanner-proxy", dependencies=[Depends(RequirePermission("system:execute"))])
async def set_scanner_proxy(body: ScannerProxyUpdate,
                            session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Save the scanner proxy. Applied on next DB download job."""
    import json
    from sqlalchemy import select as sa_select
    from ..models import SystemConfig
    blob = json.dumps({"proxy": body.proxy})
    row = await session.scalar(sa_select(SystemConfig).where(SystemConfig.key == "scanner_proxy"))
    if row is None:
        row = SystemConfig(key="scanner_proxy", value_json=blob)
        session.add(row)
    else:
        row.value_json = blob
    await session.commit()
    return {"ok": True, "proxy": body.proxy}
