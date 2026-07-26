"""CI/CD Tokens & Webhooks (Feature G) — scaffold."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ..dependencies import RequirePermission

router = APIRouter(prefix="/access", tags=["access"])

_NOT_IMPL = {"status": "not_implemented", "feature": "Feature G — CI/CD Tokens & Webhooks"}


@router.post("/tokens", status_code=status.HTTP_501_NOT_IMPLEMENTED,
             dependencies=[Depends(RequirePermission("access:write"))])
async def create_token() -> dict[str, str]:
    """TODO Feature G: generate an expiring, scoped CI/CD token."""
    return _NOT_IMPL


@router.get("/tokens", status_code=status.HTTP_501_NOT_IMPLEMENTED,
            dependencies=[Depends(RequirePermission("access:read"))])
async def list_tokens() -> dict[str, str]:
    """TODO Feature G: list active tokens."""
    return _NOT_IMPL


@router.get("/webhooks", status_code=status.HTTP_501_NOT_IMPLEMENTED,
            dependencies=[Depends(RequirePermission("access:read"))])
async def list_webhooks() -> dict[str, str]:
    """TODO Feature G: list configured webhooks."""
    return _NOT_IMPL
