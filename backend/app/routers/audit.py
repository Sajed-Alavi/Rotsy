"""Audit log read endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import RequirePermission, get_session
from ..models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    timestamp: datetime
    username: str
    action: str
    resource_type: str
    resource_id: str
    detail: str


@router.get("", response_model=list[AuditEntry],
            dependencies=[Depends(RequirePermission("roles:manage"))])
async def list_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    resource_type: Annotated[str | None, Query()] = None,
) -> list[Any]:
    stmt = select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)
