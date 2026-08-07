"""Background job endpoints (Redis-backed queue).

  * ``GET  /jobs``           — list recent jobs.
  * ``GET  /jobs/{id}``      — job status + result.
  * ``GET  /jobs/{id}/stream`` — live SSE progress.
  * ``POST /jobs/collect-metrics`` — enqueue a metric snapshot now.
  * ``POST /jobs/analyze-repo``    — enqueue a deep analysis for one repo.
  * ``POST /jobs/analyze-all``     — enqueue analysis for every repo (fan-out).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..core.jobs import JobQueue
from ..core.sse import event
from ..dependencies import RequirePermission
from ..state import app_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])

_CACHE_UNAVAILABLE = "Cache unavailable"


def _queue(request: Request) -> JobQueue:
    cache = app_state(request).cache
    if cache is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CACHE_UNAVAILABLE)
    return JobQueue(cache)


@router.get("", dependencies=[Depends(RequirePermission("jobs:read"))])
async def list_jobs(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    return [j.to_dict() for j in await _queue(request).list_recent(limit)]


@router.get("/{job_id}", dependencies=[Depends(RequirePermission("jobs:read"))])
async def get_job(request: Request, job_id: str) -> dict[str, Any]:
    job = await _queue(request).get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job.to_dict()


@router.post("/{job_id}/cancel", dependencies=[Depends(RequirePermission("jobs:manage"))])
async def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
    """Cancel a running/pending job.

    Two steps, in order:
      1. Flip the Redis status to 'cancelled' immediately — this is what a job
         still sitting in the queue (not yet picked up by the worker) needs;
         ``JobRunner._run_one`` checks for it before invoking the handler.
      2. Ask the in-process ``JobRunner`` to cancel the asyncio Task actually
         running the handler, if this worker owns one. This is real
         cancellation, not cooperative polling: it interrupts the handler at
         its next ``await`` (e.g. the subprocess-wait loop a database download
         is sitting in) rather than waiting for the handler to check a flag it
         was never actually reading — a handler that kills its own subprocess
         on ``asyncio.CancelledError`` (see modules/nexus/db/process.py)
         stops within moments, not "eventually or never".
    """
    cache = app_state(request).cache
    if cache is None or cache.redis is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CACHE_UNAVAILABLE)
    job = await _queue(request).get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status in ("done", "failed", "cancelled"):
        return {"ok": True, "status": job.status, "message": "already terminal"}
    import time
    await cache.redis.hset(f"job:{job_id}", mapping={
        "status": "cancelled", "message": "cancelled by user",
        "updated_at": str(time.time()),
    })
    await JobQueue(cache).push_event(
        job_id, {"type": "phase", "status": "cancelled", "message": "cancelled by user"},
    )
    runner = getattr(request.app.state, "runner", None)
    cancelled_in_process = bool(runner and runner.request_cancel(job_id))
    return {"ok": True, "status": "cancelled", "stopped_running_task": cancelled_in_process}


@router.get("/{job_id}/stream", dependencies=[Depends(RequirePermission("jobs:read"))])
async def stream_job(request: Request, job_id: str) -> EventSourceResponse:
    """Stream progress events for a job until it terminates.

    Tails the job's event list in Redis. Once the job reaches a terminal
    status (done/failed) we emit a final event and close.
    """
    cache = app_state(request).cache
    if cache is None or cache.redis is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _CACHE_UNAVAILABLE)
    r = cache.redis
    queue = _queue(request)

    async def gen() -> AsyncIterator[dict[str, Any]]:
        # First, replay any buffered events.
        last_idx = 0
        while True:
            if await request.is_disconnected():
                return
            job = await queue.get(job_id)
            if job is None:
                yield {"event": "error", "data": {"message": "job not found"}}
                return
            raw_events = await r.lrange(f"job:{job_id}:events", last_idx, -1)
            for raw in raw_events:
                try:
                    ev = json.loads(raw)
                    ev_type = ev.pop("type", "progress")
                    yield event(ev_type, ev)
                except (json.JSONDecodeError, TypeError):
                    continue
            last_idx += max(0, len(raw_events))
            # "cancelled" belongs here too: POST /jobs/{id}/cancel sets it, but
            # this loop used to wait only for done/failed, so cancelling a job
            # left every subscribed stream polling until the client gave up.
            if job.status in ("done", "failed", "cancelled"):
                yield event("phase", {"status": job.status, "message": job.message})
                return
            await asyncio.sleep(1)

    return EventSourceResponse(gen(), media_type="text/event-stream")


class RepoPayload(BaseModel):
    repo: str


@router.post("/collect-metrics", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("jobs:manage"))])
async def enqueue_collect(request: Request) -> dict[str, str]:
    job_id = await _queue(request).enqueue("collect_metrics", {})
    return {"job_id": job_id}


@router.post("/analyze-repo", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("jobs:manage"))])
async def enqueue_analyze(request: Request, body: RepoPayload) -> dict[str, str]:
    job_id = await _queue(request).enqueue("analyze_repo", {"repo": body.repo})
    return {"job_id": job_id}


@router.post("/analyze-all", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(RequirePermission("jobs:manage"))])
async def enqueue_analyze_all(request: Request) -> dict[str, Any]:
    """Fan out: enqueue one analyze_repo job per repo.

    Returns the list of created job ids so the frontend can watch them all.
    """
    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client unavailable")
    try:
        resp = await nexus.client.get("/service/rest/v1/repositories")
        resp.raise_for_status()
        names = [r["name"] for r in resp.json() if r.get("name")]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Failed to list repos: {exc}") from exc

    queue = _queue(request)
    job_ids = [await queue.enqueue("analyze_repo", {"repo": name}) for name in names]
    return {"job_ids": job_ids, "count": len(job_ids)}
