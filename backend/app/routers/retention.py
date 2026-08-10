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

from ..core.access_control import DELETE, AccessResolver
from ..core.jobs import JobQueue
from ..dependencies import RequirePermission, get_access, get_session
from ..models import RetentionPolicy
from ..services.retention import compute_next_run, run_policy
from ..state import app_state, require_nexus

router = APIRouter(prefix="/retention", tags=["retention"])

_POLICY_NOT_FOUND = "Policy not found"


def _require_repo_wide(access: AccessResolver, repo: str) -> None:
    """Refuse unless the caller may delete every image in ``repo``.

    A retention policy deletes on a schedule, across images that do not exist
    yet. Someone scoped to ``abrisham*`` must not be able to author a rule whose
    blast radius is the whole repository.
    """
    if not access.repo(repo).covers_all(DELETE):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Managing retention for '{repo}' requires delete access to every image in it.",
        )


class PolicyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    repo: str = Field(..., min_length=1, max_length=255)
    keep_last_n: int | None = Field(default=None, ge=0)
    delete_older_than_days: int | None = Field(default=None, ge=1)
    # Own cadence in minutes, overriding the shared daily sweep — e.g. 5 for
    # "near real-time", 60 for hourly, 4320 for every 3 days. None (the
    # default) keeps the policy on the daily RETENTION_RUN_AT schedule.
    interval_minutes: int | None = Field(default=None, ge=1)


class PolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    repo: str | None = Field(default=None, min_length=1, max_length=255)
    keep_last_n: int | None = Field(default=None, ge=0)
    delete_older_than_days: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1)


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
    interval_minutes: int | None
    next_run_at: datetime | None


def _validate(body: PolicyCreate | PolicyUpdate) -> None:
    kn = getattr(body, "keep_last_n", None)
    dod = getattr(body, "delete_older_than_days", None)
    if kn is None and dod is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "At least one of keep_last_n or delete_older_than_days must be set.")


@router.get("/policies", response_model=list[PolicyOut],
            dependencies=[Depends(RequirePermission("retention:read"))])
async def list_policies(
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    rows = (await session.execute(select(RetentionPolicy).order_by(RetentionPolicy.id))).scalars().all()
    return [row for row in rows if access.repo(row.repo).visible]


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequirePermission("retention:execute"))])
async def create_policy(
    body: PolicyCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    _require_repo_wide(access, body.repo)
    _validate(body)
    policy = RetentionPolicy(**body.model_dump())
    if policy.interval_minutes:
        policy.next_run_at = compute_next_run(policy)
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return policy


@router.patch("/policies/{policy_id}", response_model=PolicyOut,
              dependencies=[Depends(RequirePermission("retention:execute"))])
async def update_policy(
    policy_id: int,
    body: PolicyUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _POLICY_NOT_FOUND)
    _require_repo_wide(access, policy.repo)
    data = body.model_dump(exclude_unset=True)
    # Repointing a policy needs authority over the destination too.
    if data.get("repo") and data["repo"] != policy.repo:
        _require_repo_wide(access, data["repo"])
    if "keep_last_n" in data or "delete_older_than_days" in data:
        merged = PolicyCreate(name=policy.name, repo=policy.repo,
                              keep_last_n=data.get("keep_last_n", policy.keep_last_n),
                              delete_older_than_days=data.get("delete_older_than_days", policy.delete_older_than_days))
        _validate(merged)
    for k, v in data.items():
        setattr(policy, k, v)
    if "interval_minutes" in data:
        # Recompute next_run_at whenever the cadence itself changes — either
        # a fresh due time for a new/changed interval, or cleared entirely to
        # drop back onto the shared daily sweep.
        policy.next_run_at = compute_next_run(policy) if policy.interval_minutes else None
    await session.commit()
    await session.refresh(policy)
    return policy


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(RequirePermission("retention:execute"))])
async def delete_policy(
    policy_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
):
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _POLICY_NOT_FOUND)
    _require_repo_wide(access, policy.repo)
    await session.delete(policy)
    await session.commit()


@router.post("/policies/{policy_id}/preview",
             dependencies=[Depends(RequirePermission("retention:read"))])
async def preview_policy(
    policy_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
) -> dict[str, Any]:
    """Dry-run: compute what would be deleted without changing anything.

    A preview names real images, so it is gated the same way the policy itself is.
    """
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _POLICY_NOT_FOUND)
    _require_repo_wide(access, policy.repo)
    return await run_policy(require_nexus(request), session, policy, dry_run=True)


@router.post("/policies/{policy_id}/run", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("retention:execute"))])
async def run_policy_now(
    policy_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    access: Annotated[AccessResolver, Depends(get_access)],
    dry_run: Annotated[bool, Query()] = False,
) -> dict[str, str]:
    """Enqueue a background job to execute the policy now.

    The job runs out-of-band with no principal attached, so authority has to be
    established here, at enqueue time.
    """
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _POLICY_NOT_FOUND)
    _require_repo_wide(access, policy.repo)
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("run_retention", {"policy_id": policy_id, "dry_run": dry_run})
    return {"job_id": job_id}


@router.post("/run-all", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("retention:execute"))])
async def run_all(
    request: Request,
    access: Annotated[AccessResolver, Depends(get_access)],
    dry_run: Annotated[bool, Query()] = False,
) -> dict[str, str]:
    """Enqueue a background job to run every enabled policy.

    Refused for a caller whose rules do not cover everything: running "all"
    partially would report success for work that never happened.
    """
    if not access.unrestricted_everywhere:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Running every policy requires unrestricted access. Run the individual "
            "policies you can reach instead.",
        )
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cache unavailable")
    job_id = await JobQueue(cache).enqueue("run_retention", {"dry_run": dry_run})
    return {"job_id": job_id}
