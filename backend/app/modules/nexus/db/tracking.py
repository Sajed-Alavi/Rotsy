"""Remember which database job is the current one.

A database update can be started from three places: the Database Management
page, the startup readiness check, and the daily schedule. Only the first of
those had a caller holding the job id, so an update kicked off by the scheduler
— or by another operator, or before a page reload — was invisible: the UI had a
job streaming live progress and no way to discover its id.

One Redis key fixes that. It is written at enqueue time (not when the worker
picks the job up) so the job is discoverable while it is still queued, and it is
deliberately not deleted on completion: the last finished run is what the page
shows when nothing is in flight, which is how a failure stays on screen instead
of vanishing.
"""

from __future__ import annotations

import logging
from typing import Any

from ....core.cache import Cache
from ....core.jobs import JobQueue

logger = logging.getLogger(__name__)

CURRENT_JOB_KEY = "scanner_db:current_job"
# Long enough to outlive any plausible download, short enough that a very old
# job id does not linger after the job hash itself has expired.
_TTL_SECONDS = 24 * 3600

DB_JOB_TYPES = ("scanner_db_update", "scanner_db_import")


async def enqueue_db_job(cache: Cache, job_type: str, payload: dict[str, Any] | None = None) -> str:
    """Enqueue a database job and record it as the current one."""
    job_id = await JobQueue(cache).enqueue(job_type, payload or {})
    try:
        if cache.redis is not None:
            await cache.redis.set(CURRENT_JOB_KEY, job_id, ex=_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - tracking must never break the enqueue
        logger.debug("could not record the current scanner DB job", exc_info=True)
    return job_id


async def current_db_job(cache: Cache) -> dict[str, Any] | None:
    """The in-flight database job, or the most recent one if none is running.

    Returns the job's full dict (including ``detail``, so a client arriving
    mid-download gets current bytes/speed/ETA without replaying the event list),
    plus ``active`` telling it whether to open an event stream.
    """
    if cache.redis is None:
        return None
    try:
        job_id = await cache.redis.get(CURRENT_JOB_KEY)
    except Exception:  # noqa: BLE001
        logger.debug("could not read the current scanner DB job id", exc_info=True)
        return None
    if not job_id:
        return None

    job = await JobQueue(cache).get(job_id)
    if job is None:
        # The job hash outlived its TTL, or Redis was flushed.
        return None

    data = job.to_dict()
    data["active"] = job.status in ("pending", "running")
    return data
