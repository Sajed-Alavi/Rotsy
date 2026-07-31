"""Alert rule CRUD.

Alerts are evaluated automatically after each metric collection cycle (see
the lifespan metric loop). This router only manages rule definitions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..core.outbound import OutboundURLError, validate_outbound_url
from ..dependencies import RequirePermission, get_session
from ..models import AlertRule

router = APIRouter(prefix="/alerts", tags=["alerts"])

_VALID_METRICS = {"storage.total", "storage.asset_count", "blobstore.used_pct"}
_VALID_CONDITIONS = {">", "<", "=="}


def _checked_webhook_url(value: str | None) -> str | None:
    """Reject webhook destinations the backend must not be pointed at.

    ``webhook_url`` is optional, so ``None`` passes through untouched. Anything
    else has to survive the SSRF guard before it is allowed to persist — see
    :mod:`app.core.outbound`.
    """
    if value is None:
        return None
    try:
        return validate_outbound_url(value, get_settings())
    except OutboundURLError as exc:
        raise ValueError(f"webhook_url rejected: {exc}") from exc


class AlertCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    metric: str
    condition: Literal[">", "<", "=="]
    threshold: float
    repo_filter: str | None = Field(default=None, max_length=255)  # SQL LIKE pattern; null = all
    webhook_url: str | None = Field(default=None, min_length=8, max_length=512, description="Optional — the rule still evaluates without one, just skips delivery")
    enabled: bool = True

    @field_validator("webhook_url")
    @classmethod
    def _check_webhook_url(cls, value: str | None) -> str | None:
        return _checked_webhook_url(value)


class AlertUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    metric: str | None = None
    condition: Literal[">", "<", "=="] | None = None
    threshold: float | None = None
    repo_filter: str | None = Field(default=None, max_length=255)
    webhook_url: str | None = Field(default=None, min_length=8, max_length=512)
    enabled: bool | None = None

    @field_validator("webhook_url")
    @classmethod
    def _check_webhook_url(cls, value: str | None) -> str | None:
        return _checked_webhook_url(value)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    metric: str
    condition: str
    threshold: float
    repo_filter: str | None
    webhook_url: str | None
    enabled: bool
    is_default: bool
    created_at: datetime
    last_triggered_at: datetime | None


def _validate_fields(metric: str | None = None, condition: str | None = None) -> None:
    if metric is not None and metric not in _VALID_METRICS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown metric '{metric}'.")
    if condition is not None and condition not in _VALID_CONDITIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown condition '{condition}'.")


@router.get("", response_model=list[AlertOut], dependencies=[Depends(RequirePermission("alerts:read"))])
async def list_alerts(session: Annotated[AsyncSession, Depends(get_session)]):
    rows = (await session.execute(select(AlertRule).order_by(AlertRule.id))).scalars().all()
    return list(rows)


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("alerts:write"))])
async def create_alert(body: AlertCreate, session: Annotated[AsyncSession, Depends(get_session)]):
    _validate_fields(body.metric, body.condition)
    rule = AlertRule(**body.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.patch("/{rule_id}", response_model=AlertOut,
              dependencies=[Depends(RequirePermission("alerts:write"))])
async def update_alert(rule_id: int, body: AlertUpdate, session: Annotated[AsyncSession, Depends(get_session)]):
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert rule not found")
    data = body.model_dump(exclude_unset=True)
    _validate_fields(data.get("metric"), data.get("condition"))
    for k, v in data.items():
        setattr(rule, k, v)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("alerts:write"))])
async def delete_alert(rule_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert rule not found")
    await session.delete(rule)
    await session.commit()
