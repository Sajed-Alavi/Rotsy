"""Retention & cleanup: rule-based deletion.

CRUD for policies + dry-run preview + run-now endpoint. Actual deletion is a
background job (so a large repo doesn't block the request). The job calls the
Nexus component DELETE endpoint and then triggers blob compaction so the
physical space is reclaimed (not just the metadata).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.jobs import JobQueue
from ..dependencies import RequirePermission, get_session
from ..models import RetentionPolicy
from ..services.retention import run_policy
from ..state import app_state

router = APIRouter(prefix="/retention", tags=["retention"])


class PolicyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    repo: str = Field(..., min_length=1, max_length=255)
    keep_last_n: int | None = Field(default=None, ge=0)
    delete_older_than_days: int | None = Field(default=None, ge=1)


class PolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    repo: str | None = Field(default=None, min_length=1, max_length=255)
    keep_last_n: int | None = Field(default=None, ge=0)
    delete_older_than_days: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    repo: str
    keep_last_n: int | None
    delete_older_than_days: int | None
    enabled: bool
    created_at: datetime
    last_run_at: datetime | None


def _validate(body: PolicyCreate | PolicyUpdate) -> None:
    kn = getattr(body, "keep_last_n", None)
    dod = getattr(body, "delete_older_than_days", None)
    if kn is None and dod is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "At least one of keep_last_n or delete_older_than_days must be set.")


@router.get("/policies", response_model=list[PolicyOut],
            dependencies=[Depends(RequirePermission("retention:read"))])
async def list_policies(session: Annotated[AsyncSession, Depends(get_session)]):
    rows = (await session.execute(select(RetentionPolicy).order_by(RetentionPolicy.id))).scalars().all()
    return list(rows)


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("retention:execute"))])
async def create_policy(body: PolicyCreate, session: Annotated[AsyncSession, Depends(get_session)]):
    _validate(body)
    policy = RetentionPolicy(**body.model_dump())
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return policy


@router.patch("/policies/{policy_id}", response_model=PolicyOut,
              dependencies=[Depends(RequirePermission("retention:execute"))])
async def update_policy(policy_id: int, body: PolicyUpdate,
                        session: Annotated[AsyncSession, Depends(get_session)]):
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    data = body.model_dump(exclude_unset=True)
    if "keep_last_n" in data or "delete_older_than_days" in data:
        merged = PolicyCreate(name=policy.name, repo=policy.repo,
                              keep_last_n=data.get("keep_last_n", policy.keep_last_n),
                              delete_older_than_days=data.get("delete_older_than_days", policy.delete_older_than_days))
        _validate(merged)
    for k, v in data.items():
        setattr(policy, k, v)
    await session.commit()
    await session.refresh(policy)
    return policy


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("retention:execute"))])
async def delete_policy(policy_id: int, session: Annotated[AsyncSession, Depends(get_session)]):
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    await session.delete(policy)
    await session.commit()


@router.post("/policies/{policy_id}/preview",
             dependencies=[Depends(RequirePermission("retention:read"))])
async def preview_policy(policy_id: int, request: Request,
                         session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """Dry-run: compute what would be deleted without changing anything."""
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found")
    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not available")
    return await run_policy(nexus, session, policy, dry_run=True)


@router.post("/policies/{policy_id}/run", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("retention:execute"))])
async def run_policy_now(policy_id: int, request: Request,
                         dry_run: Annotated[bool, Query()] = False) -> dict[str, str]:
    """Enqueue a background job to execute the policy now."""
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("run_retention", {"policy_id": policy_id, "dry_run": dry_run})
    return {"job_id": job_id}


@router.post("/run-all", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("retention:execute"))])
async def run_all(request: Request, dry_run: Annotated[bool, Query()] = False) -> dict[str, str]:
    """Enqueue a background job to run every enabled policy."""
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("run_retention", {"dry_run": dry_run})
    return {"job_id": job_id}
