"""Redis-backed background job framework (lightweight, no Celery).

A job is a unit of heavy work (analyzing a large repo, collecting metrics,
running a cleanup sweep, ...). Each job has:

  - an ``id`` (uuid)
  - a ``type`` (e.g. ``analyze_repo``, ``collect_metrics``) registered with the worker
  - ``status`` (``pending`` → ``running`` → ``done`` | ``failed``)
  - ``progress`` (0..100) and ``message``
  - a ``result`` payload (JSON) when done
  - a Redis list ``job:{id}:events`` consumed by the SSE endpoint for live updates

Two Redis structures per job:
  * ``job:{id}`` — a hash with the mutable fields above.
  * ``job:{id}:events`` — a list of progress event dicts, pushed by the worker
    and consumed by SSE streams. Capped to the last 256 events.
  * ``jobs:index`` — a list of recent job ids (newest first) for ``GET /jobs``.

Workers register a handler callable per job type. The runner pops jobs from
``jobs:queue`` and invokes the matching handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis

from .cache import Cache

logger = logging.getLogger(__name__)

_QUEUE_KEY = "jobs:queue"
_INDEX_KEY = "jobs:index"
_INDEX_MAX = 200
_EVENTS_MAX = 256
_JOB_TTL = 7 * 24 * 3600  # keep finished jobs for a week


@dataclass
class Job:
    id: str
    type: str
    status: str
    progress: int
    message: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    created_at: float
    updated_at: float
    # Structured counterpart to ``message`` for the last progress report:
    # bytes, speed, ETA, stage. Absent for handlers that report prose only.
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "payload": self.payload,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "detail": self.detail,
        }


# A handler takes (job, progress_callback) and returns a result dict. The
# callback accepts (percent, message, detail=None) — see the ``progress``
# closure in JobRunner._run_one.
ProgressCallback = Callable[..., Awaitable[None]]
JobHandler = Callable[[Job, ProgressCallback], Awaitable[dict[str, Any]]]


class JobQueue:
    """Enqueue/inspect jobs; the runner consumes them."""

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    @property
    def _r(self) -> aioredis.Redis | None:
        return self._cache.redis

    async def enqueue(self, job_type: str, payload: dict[str, Any] | None = None) -> str:
        """Create a pending job and push its id onto the queue."""
        assert self._r is not None, "Redis unavailable; cannot enqueue jobs"
        job = Job(
            id=uuid.uuid4().hex,
            type=job_type,
            status="pending",
            progress=0,
            message="queued",
            payload=payload or {},
            result=None,
            created_at=time.time(),
            updated_at=time.time(),
        )
        key = f"job:{job.id}"
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=_flatten(job.to_dict()))
            pipe.expire(key, _JOB_TTL)
            pipe.lpush(_INDEX_KEY, job.id)
            pipe.ltrim(_INDEX_KEY, 0, _INDEX_MAX - 1)
            pipe.rpush(_QUEUE_KEY, f"{job.id}:{job.type}")
            await pipe.execute()
        logger.info("Enqueued job %s type=%s", job.id, job_type)
        return job.id

    async def get(self, job_id: str) -> Job | None:
        assert self._r is not None
        raw = await self._r.hgetall(f"job:{job_id}")
        if not raw:
            return None
        return _unflatten(raw)

    async def list_recent(self, limit: int = 50) -> list[Job]:
        assert self._r is not None
        ids = await self._r.lrange(_INDEX_KEY, 0, limit - 1)
        jobs: list[Job] = []
        for jid in ids:
            job = await self.get(jid)
            if job is not None:
                jobs.append(job)
        return jobs

    async def reap_stranded(self) -> int:
        """Fail jobs left mid-flight by a previous process.

        A job's ``running`` status is written by the worker that owns it. If that
        process dies — a restart, a redeploy, a crash — nothing ever writes a
        terminal status, so the job stays ``running`` forever: the UI shows a
        progress bar that will never move and a database update that will never
        finish. Nothing resumes these; the queue entry is gone with the process.

        Runs at startup, before the worker begins consuming, so it cannot race a
        job this process legitimately owns.
        """
        assert self._r is not None
        reaped = 0
        for job_id in await self._r.lrange(_INDEX_KEY, 0, _INDEX_MAX - 1):
            job = await self.get(job_id)
            if job is None or job.status not in ("running", "pending"):
                continue
            await self._r.hset(
                f"job:{job_id}",
                mapping={
                    "status": "failed",
                    "message": "interrupted — the worker process restarted before this job finished",
                    "updated_at": str(time.time()),
                },
            )
            await self.push_event(job_id, {
                "type": "error",
                "message": "interrupted — the worker process restarted before this job finished",
            })
            reaped += 1
        if reaped:
            logger.info("Marked %d stranded job(s) as failed", reaped)
        return reaped

    async def push_event(self, job_id: str, event: dict[str, Any]) -> None:
        """Publish a progress event for the SSE stream to consume."""
        assert self._r is not None
        await self._r.rpush(f"job:{job_id}:events", json.dumps(event, default=str))
        await self._r.ltrim(f"job:{job_id}:events", -_EVENTS_MAX, -1)


def _flatten(d: dict[str, Any]) -> dict[str, str]:
    """Flatten nested dicts for Redis HSET (values must be str)."""
    out: dict[str, str] = {}
    for k, v in d.items():
        out[k] = v if isinstance(v, str) else json.dumps(v, default=str)
    return out


def _unflatten(raw: dict[str, str]) -> Job:
    def load(k: str, default: Any) -> Any:
        v = raw.get(k)
        if v is None:
            return default
        if k in ("payload", "result", "detail"):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return default
        return v

    return Job(
        id=raw["id"],
        type=raw["type"],
        status=raw.get("status", "unknown"),
        progress=int(raw.get("progress", 0)),
        message=raw.get("message", ""),
        payload=load("payload", {}) or {},
        result=load("result", None),
        created_at=float(raw.get("created_at", 0)),
        updated_at=float(raw.get("updated_at", 0)),
        detail=load("detail", None),
    )


class JobRunner:
    """Background loop: pop jobs from the queue and run registered handlers.

    Uses a dedicated Redis connection with a long socket timeout so the
    blocking ``BLPOP`` call (which can wait up to its own timeout) is not
    killed prematurely by the short socket_timeout configured on the shared
    cache client.
    """

    def __init__(self, cache: Cache) -> None:
        self._cache = cache
        self._handlers: dict[str, JobHandler] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._dedicated: aioredis.Redis | None = None
        # job_id -> the asyncio.Task running its handler, for real cancellation.
        # POST /jobs/{id}/cancel used to only flip the Redis status field and
        # hope a handler noticed — nothing ever polled it, so a subprocess-backed
        # handler (e.g. a database download) kept running in the background
        # regardless of what the UI showed. This is a single-process worker (no
        # --reload, no multi-worker uvicorn), so an in-process Task.cancel() is
        # both sufficient and immediate: it interrupts the handler at its next
        # await point rather than waiting on a polling interval.
        self._running: dict[str, asyncio.Task] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler
        logger.info("Registered job handler: %s", job_type)

    def start(self) -> None:
        if self._task is not None:
            return
        # Dedicated connection: socket_timeout long enough for BLPOP's own
        # blocking window (plus headroom) so the worker never hits a spurious
        # timeout reading from an idle queue. from_url() itself is lazy — it
        # doesn't connect yet, so nothing here needs to be awaited.
        self._dedicated = aioredis.from_url(
            self._cache._settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=30,
        )
        self._task = asyncio.create_task(self._loop(), name="job-runner")
        logger.info("JobRunner started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._dedicated is not None:
            await self._dedicated.aclose()
            self._dedicated = None
        logger.info("JobRunner stopped")

    async def _loop(self) -> None:
        r = self._dedicated
        if r is None:
            logger.warning("Redis unavailable; JobRunner not starting.")
            return
        logger.info("JobRunner polling %s", _QUEUE_KEY)
        while not self._stop.is_set():
            try:
                # BLPOP blocks up to 5s, then we re-check the stop flag.
                item = await r.blpop(_QUEUE_KEY, timeout=5)
            except Exception as exc:  # noqa: BLE001
                logger.warning("JobRunner queue error: %s", exc)
                await asyncio.sleep(2)
                continue
            if item is None:
                continue
            _, value = item
            try:
                job_id, job_type = value.split(":", 1)
            except (ValueError, AttributeError):
                # Older clients may push bytes.
                try:
                    job_id, job_type = value.decode().split(":", 1)
                except (ValueError, AttributeError):
                    logger.warning("Malformed job queue item: %r", value)
                    continue
            task = asyncio.create_task(self._run_one(job_id, job_type))
            self._running[job_id] = task
            task.add_done_callback(lambda _t, jid=job_id: self._running.pop(jid, None))

    def request_cancel(self, job_id: str) -> bool:
        """Cancel a job's handler task in-process, if this worker owns it.

        Returns whether a running task was found and cancelled. The Redis
        status flip (so a cancellation requested before the worker even popped
        the job off the queue still takes effect) is the caller's job — see
        ``POST /jobs/{id}/cancel`` in routers/jobs.py.
        """
        task = self._running.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _run_one(self, job_id: str, job_type: str) -> None:
        r = self._cache.redis
        handler = self._handlers.get(job_type)
        job = await JobQueue(self._cache).get(job_id)
        if job is None or r is None:
            return
        if job.status == "cancelled":
            # Cancelled while still queued (before this worker picked it up) —
            # the status flip already happened via the cancel endpoint; there is
            # no handler running yet to interrupt, so just honor it.
            logger.info("Job %s was cancelled before it started running", job_id)
            return

        async def progress(percent: int, message: str, detail: dict | None = None) -> None:
            """Report progress to both the job hash and the SSE event stream.

            ``detail`` is the structured form of what ``message`` says in prose
            (bytes, speed, ETA, stage). Handlers that have nothing structured to
            add omit it and behave exactly as before. It is stored on the hash
            too, so a client that arrives mid-job — after a page reload, or for a
            job it did not start — gets the current numbers without having to
            replay the whole event list.
            """
            mapping = {
                "progress": str(percent), "message": message, "status": "running",
                "updated_at": str(time.time()),
            }
            event: dict[str, Any] = {"type": "progress", "percent": percent, "message": message}
            if detail:
                mapping["detail"] = json.dumps(detail, default=str)
                event["detail"] = detail
            await r.hset(f"job:{job_id}", mapping=mapping)
            await JobQueue(self._cache).push_event(job_id, event)

        if handler is None:
            await r.hset(f"job:{job_id}", mapping={"status": "failed", "message": f"no handler for {job_type}",
                                                   "updated_at": str(time.time())})
            logger.error("No handler registered for job type %s", job_type)
            return

        await r.hset(f"job:{job_id}", mapping={"status": "running", "message": "started",
                                               "updated_at": str(time.time())})
        await JobQueue(self._cache).push_event(job_id, {"type": "phase", "message": "started"})
        try:
            result = await handler(job, progress)
            await r.hset(
                f"job:{job_id}",
                mapping={"status": "done", "progress": "100", "message": "completed",
                         "result": json.dumps(result, default=str), "updated_at": str(time.time())},
            )
            await JobQueue(self._cache).push_event(job_id, {"type": "result", "result": result})
            logger.info("Job %s done", job_id)
        except asyncio.CancelledError:
            # Task.cancel() from request_cancel() lands here. The handler is
            # expected to have killed any subprocess it owned on its way out
            # (see run_streaming/oras_pull in modules/nexus/db/process.py) —
            # this is only responsible for the terminal job state, not process
            # cleanup. Re-raised after that cleanup, not swallowed: this task
            # (created at `asyncio.create_task(self._run_one(...))` above) is
            # itself what was cancelled and nothing awaits it afterward — its
            # `add_done_callback` still fires either way — so re-raising here
            # just lets the task's own final state genuinely be "cancelled"
            # instead of "finished normally after catching a cancellation".
            logger.info("Job %s cancelled", job_id)
            await r.hset(
                f"job:{job_id}",
                mapping={"status": "cancelled", "message": "cancelled by user", "updated_at": str(time.time())},
            )
            await JobQueue(self._cache).push_event(
                job_id, {"type": "phase", "status": "cancelled", "message": "cancelled by user"},
            )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s failed", job_id)
            await r.hset(
                f"job:{job_id}",
                mapping={"status": "failed", "message": str(exc), "updated_at": str(time.time())},
            )
            await JobQueue(self._cache).push_event(job_id, {"type": "error", "message": str(exc)})
