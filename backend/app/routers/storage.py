"""Deep Storage Analyzer API (Feature A — fully implemented, auth-protected).

Endpoints:
  * ``GET /storage/repos``           — list Docker repositories (powers the selector).
  * ``GET /storage/{repo}/result``   — return the cached result or 404.
  * ``GET /storage/{repo}/analyze``  — run analysis (non-streaming), cache + return JSON.
  * ``GET /storage/{repo}/analyze/stream`` — run analysis streaming progress over SSE.

All read endpoints require ``storage:read``; the analyze endpoints require
``storage:analyze``. SSE auth still works because the access cookie is sent
automatically by ``EventSource`` (same-origin) — no query-string fallback
needed in the cookie-based auth model.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from ..config import Settings, get_settings
from ..core.access_control import AccessResolver, RepoAccess
from ..core.sse import event
from ..dependencies import RequirePermission, get_access
from ..state import app_state, require_nexus
from ..services.storage_analyzer import StorageAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage", tags=["storage"])

_REPOS_CACHE_KEY = "nexus:all-repos"
# Short TTL for repo lists: they change rarely but we want fresh data when the
# user adds a repo outside the UI. 30s is a good freshness/perf balance.
_REPO_LIST_TTL = 30


def _result_cache_key(repo: str) -> str:
    return f"analysis:{repo}"


def _scope_result(result: dict[str, Any], access: RepoAccess) -> dict[str, Any]:
    """Apply access-rule filtering to an analyzer result.

    The analyzer cache is shared/unfiltered per repo across every user, so
    this is applied as a post-processing step at every response point rather
    than baked into the cache itself. Stats are recomputed from the filtered
    items so a scoped user's aggregate totals can't leak hidden images'
    sizes. ``active_bytes``/``wasted_bytes`` don't decompose cleanly per-image
    (shared-layer dedup can span visible and hidden images), so a filtered
    view reports ``active_bytes == total_bytes`` and ``wasted_bytes == 0``
    rather than a misleading, precise-looking number.
    """
    if access.unrestricted:
        return result
    items = access.filter(result.get("items", []))
    total_bytes = sum(it.get("total_bytes", 0) for it in items)
    scoped = dict(result)
    scoped["items"] = items
    scoped["stats"] = {
        **result.get("stats", {}),
        "total_bytes": total_bytes,
        "active_bytes": total_bytes,
        "wasted_bytes": 0,
        "item_count": len(items),
    }
    return scoped


def _analyzer(request: Request, settings: Settings) -> StorageAnalyzer:
    """Build a StorageAnalyzer bound to this request's Nexus client + settings."""
    return StorageAnalyzer(require_nexus(request), max_concurrency=settings.ANALYZER_MAX_CONCURRENCY)


class _InFlight:
    """One shared analysis run for a repo, with its fanned-out subscribers."""

    def __init__(self) -> None:
        self.subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        # asyncio only holds a weak reference to a task; without keeping this
        # one alive here, it could be garbage-collected mid-run.
        self.task: asyncio.Task[None] | None = None


# Process-local: two requests for the same uncached repo (two tabs, or a
# request landing right after the cache TTL expires) attach to the same run
# instead of each re-running the full analysis against Nexus independently.
_inflight: dict[str, _InFlight] = {}


def _join_or_start(
    repo: str, analyzer: StorageAnalyzer, fmt_hint: str | None, cache: Any,
) -> tuple[_InFlight, "asyncio.Queue[dict[str, Any]]"]:
    entry = _inflight.get(repo)
    my_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    if entry is None:
        entry = _InFlight()
        _inflight[repo] = entry

        async def broadcast(ev: dict[str, Any]) -> None:
            for q in list(entry.subscribers):  # NOSONAR — a real copy, not redundant: awaiting q.put() below yields control, during which a subscriber can detach and mutate entry.subscribers concurrently
                await q.put(dict(ev))

        async def run() -> None:
            try:
                result = await analyzer.analyze_repo(repo, on_progress=broadcast, fmt_hint=fmt_hint)
                # Cache once here, regardless of how many subscribers are still
                # attached — a disconnect must not discard a completed run.
                await cache.set_json(_result_cache_key(repo), result)
                for q in list(entry.subscribers):  # NOSONAR — a real copy, not redundant: awaiting q.put() below yields control, during which a subscriber can detach and mutate entry.subscribers concurrently
                    await q.put({"__result__": result})
            except Exception as exc:  # noqa: BLE001
                for q in list(entry.subscribers):  # NOSONAR — a real copy, not redundant: awaiting q.put() below yields control, during which a subscriber can detach and mutate entry.subscribers concurrently
                    await q.put({"__error__": exc})
            finally:
                for q in list(entry.subscribers):  # NOSONAR — a real copy, not redundant: awaiting q.put() below yields control, during which a subscriber can detach and mutate entry.subscribers concurrently
                    await q.put({"__done__": True})
                _inflight.pop(repo, None)

        entry.task = asyncio.create_task(run())

    entry.subscribers.append(my_queue)
    return entry, my_queue


async def _await_result(queue: "asyncio.Queue[dict[str, Any]]") -> dict[str, Any]:
    """Drain a subscriber queue (no progress events expected before this call
    is used) until the shared run finishes; return its result or raise."""
    while True:
        ev = await queue.get()
        if "__result__" in ev:
            return ev["__result__"]
        if "__error__" in ev:
            raise ev["__error__"]
        if "__done__" in ev:
            raise RuntimeError("Analysis ended without a result")


@router.get("/repos", dependencies=[Depends(RequirePermission("storage:read"))])
async def list_all_repos(
    request: Request,
    access: Annotated[AccessResolver, Depends(get_access)],
    refresh: Annotated[bool, Query(description="Bypass/refresh the cache")] = False,
    format_filter: Annotated[str | None, Query(alias="format", description="Filter by format: docker, maven2, nuget, etc.")] = None,
) -> list[dict[str, Any]]:
    """Return ALL repositories (or filtered by format), cached briefly.

    ``?format=docker`` returns only Docker repos (for the scanner dropdown).
    ``?refresh=true`` bypasses the cache.

    Repositories the caller's access rules do not reach are omitted. The cache
    is shared across users, so filtering happens on the way out — never on the
    way in, or one user's narrow view would be served to the next caller.
    """
    cache = app_state(request).cache

    if refresh:
        await cache.delete(_REPOS_CACHE_KEY)
        await cache.delete("nexus:repositories")
    else:
        cached = await cache.get_json(_REPOS_CACHE_KEY)
        if cached is not None:
            repos = cached
            if format_filter:
                repos = [r for r in repos if r.get("format") == format_filter]
            return _visible(access, repos)

    nexus = require_nexus(request)
    try:
        resp = await nexus.client.get("/service/rest/v1/repositories")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list repositories: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to contact Nexus") from exc

    repos = [
        {"name": r.get("name"), "format": r.get("format"), "type": r.get("type")}
        for r in resp.json()
        if r.get("name")
    ]
    type_order = {"hosted": 0, "proxy": 1, "group": 2}
    repos.sort(key=lambda r: (type_order.get(r.get("type"), 9), r["name"]))
    await cache.set_json(_REPOS_CACHE_KEY, repos, ttl=_REPO_LIST_TTL)

    if format_filter:
        repos = [r for r in repos if r.get("format") == format_filter]
    return _visible(access, repos)


def _visible(access: AccessResolver, repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in repos if access.repo(r.get("name") or "").visible]


@router.get("/{repo}/result", dependencies=[Depends(RequirePermission("storage:read"))])
async def get_cached_result(
    request: Request,
    repo: str,
    access: Annotated[AccessResolver, Depends(get_access)],
) -> dict[str, Any]:
    """Return the cached analysis result for ``repo``, or 404 if none yet."""
    cache = app_state(request).cache
    cached = await cache.get_json(_result_cache_key(repo))
    if cached is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No cached analysis for '{repo}'.")
    return _scope_result(cached, access.repo(repo))


@router.get("/{repo}/analyze", dependencies=[Depends(RequirePermission("storage:analyze"))])
async def analyze(
    request: Request,
    repo: str,
    settings: Annotated[Settings, Depends(get_settings)],
    access: Annotated[AccessResolver, Depends(get_access)],
    use_cache: Annotated[bool, Query(description="Return cached result if available")] = True,
    format: Annotated[str | None, Query(description="Known repo format — skips an extra lookup")] = None,
) -> dict[str, Any]:
    """Run the analysis (non-streaming), cache it, and return the JSON result."""
    cache = app_state(request).cache
    allowed = access.repo(repo)

    if use_cache:
        cached = await cache.get_json(_result_cache_key(repo))
        if cached is not None:
            return _scope_result(cached, allowed)

    analyzer = _analyzer(request, settings)
    _, queue = _join_or_start(repo, analyzer, format, cache)
    try:
        result = await _await_result(queue)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Analysis failed for %s", repo)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Analysis failed: {exc}") from exc
    return _scope_result(result, allowed)


@router.get("/{repo}/analyze/stream", dependencies=[Depends(RequirePermission("storage:analyze"))])
async def analyze_stream(
    request: Request,
    repo: str,
    settings: Annotated[Settings, Depends(get_settings)],
    access: Annotated[AccessResolver, Depends(get_access)],
    use_cache: Annotated[bool, Query(description="Emit a 'cache' event and return cached result if available")] = True,
    format: Annotated[str | None, Query(description="Known repo format — skips an extra lookup")] = None,
) -> EventSourceResponse:
    """Stream analysis progress as Server-Sent Events.

    Event types (see ``core/sse.py``): ``phase``, ``progress``, ``cache``,
    ``result``, ``error``. If another request is already analyzing this repo,
    this stream attaches to that run and receives the same events rather than
    starting a second, independent analysis.
    """
    cache = app_state(request).cache
    # Resolved outside the generator: the request scope (and its DB session) is
    # gone by the time the SSE body streams.
    allowed = access.repo(repo)

    async def _wait_for_event(queue: asyncio.Queue, timeout: float = 15.0) -> dict[str, Any] | None:
        """The next analyzer event, or ``None`` on a timeout (caller should
        emit a keepalive and retry)."""
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def event_generator() -> AsyncIterator[dict[str, Any]]:
        if use_cache:
            cached = await cache.get_json(_result_cache_key(repo))
            if cached is not None:
                yield event("cache", {"message": "Returning cached result", "result": _scope_result(cached, allowed)})
                return

        analyzer = _analyzer(request, settings)
        entry, queue = _join_or_start(repo, analyzer, format, cache)
        analyzer_error: Exception | None = None

        try:
            while True:
                if await request.is_disconnected():
                    logger.info("Client disconnected from analysis stream for '%s'.", repo)
                    break
                ev = await _wait_for_event(queue)
                if ev is None:
                    yield event("progress", {"message": "working"})
                    continue

                if "__done__" in ev:
                    break
                if "__result__" in ev:
                    # Caching already happened once inside the shared run.
                    continue
                if "__error__" in ev:
                    analyzer_error = ev["__error__"]
                    continue
                # Translate the analyzer's internal payload (``{"type": ...,
                # ...rest}``) into an SSE frame. JSON-encode the data so the
                # browser gets valid JSON (sse-starlette writes str(dict)
                # otherwise, which JSON.parse rejects).
                ev_type = ev.pop("type", "progress")
                if ev_type == "result" and "result" in ev:
                    ev["result"] = _scope_result(ev["result"], allowed)
                yield event(ev_type, ev)

            if analyzer_error is not None:
                logger.exception("Analysis failed for %s", repo, exc_info=analyzer_error)
                yield event("error", {"message": f"Analysis failed: {analyzer_error}"})
        finally:
            # Detach without cancelling — other subscribers (or a request that
            # started after us) may still be waiting on this same run.
            entry.subscribers[:] = [q for q in entry.subscribers if q is not queue]

    return EventSourceResponse(event_generator(), media_type="text/event-stream")
