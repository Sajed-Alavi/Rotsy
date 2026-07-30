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
from ..core.sse import event
from ..dependencies import RequirePermission
from ..state import app_state
from ..services.storage_analyzer import StorageAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage", tags=["storage"])

_REPOS_CACHE_KEY = "nexus:all-repos"
# Short TTL for repo lists: they change rarely but we want fresh data when the
# user adds a repo outside the UI. 30s is a good freshness/perf balance.
_REPO_LIST_TTL = 30


def _result_cache_key(repo: str) -> str:
    return f"analysis:{repo}"


def _analyzer(request: Request, settings: Settings) -> StorageAnalyzer:
    """Build a StorageAnalyzer bound to this request's Nexus client + settings."""
    nexus = app_state(request).nexus
    if nexus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not initialised")
    return StorageAnalyzer(nexus, max_concurrency=settings.ANALYZER_MAX_CONCURRENCY)


@router.get("/repos", dependencies=[Depends(RequirePermission("storage:read"))])
async def list_all_repos(
    request: Request,
    refresh: Annotated[bool, Query(description="Bypass/refresh the cache")] = False,
    format_filter: Annotated[str | None, Query(alias="format", description="Filter by format: docker, maven2, nuget, etc.")] = None,
) -> list[dict[str, Any]]:
    """Return ALL repositories (or filtered by format), cached briefly.

    ``?format=docker`` returns only Docker repos (for the scanner dropdown).
    ``?refresh=true`` bypasses the cache.
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
            return repos

    nexus = app_state(request).nexus
    try:
        resp = await nexus.client.get("/service/rest/v1/repositories")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list repositories: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to contact Nexus")

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
    return repos


@router.get("/{repo}/result", dependencies=[Depends(RequirePermission("storage:read"))])
async def get_cached_result(
    request: Request,
    repo: str,
) -> dict[str, Any]:
    """Return the cached analysis result for ``repo``, or 404 if none yet."""
    cache = app_state(request).cache
    cached = await cache.get_json(_result_cache_key(repo))
    if cached is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No cached analysis for '{repo}'.")
    return cached


@router.get("/{repo}/analyze", dependencies=[Depends(RequirePermission("storage:analyze"))])
async def analyze(
    request: Request,
    repo: str,
    settings: Annotated[Settings, Depends(get_settings)],
    use_cache: Annotated[bool, Query(description="Return cached result if available")] = True,
) -> dict[str, Any]:
    """Run the analysis (non-streaming), cache it, and return the JSON result."""
    cache = app_state(request).cache

    if use_cache:
        cached = await cache.get_json(_result_cache_key(repo))
        if cached is not None:
            return cached

    analyzer = _analyzer(request, settings)
    try:
        result = await analyzer.analyze_repo(repo)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Analysis failed for %s", repo)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Analysis failed: {exc}") from exc

    await cache.set_json(_result_cache_key(repo), result)
    return result


@router.get("/{repo}/analyze/stream", dependencies=[Depends(RequirePermission("storage:analyze"))])
async def analyze_stream(
    request: Request,
    repo: str,
    settings: Annotated[Settings, Depends(get_settings)],
    use_cache: Annotated[bool, Query(description="Emit a 'cache' event and return cached result if available")] = True,
) -> EventSourceResponse:
    """Stream analysis progress as Server-Sent Events.

    Event types (see ``core/sse.py``): ``phase``, ``progress``, ``cache``,
    ``result``, ``error``.
    """
    cache = app_state(request).cache

    async def event_generator() -> AsyncIterator[dict[str, Any]]:
        if use_cache:
            cached = await cache.get_json(_result_cache_key(repo))
            if cached is not None:
                yield event("cache", {"message": "Returning cached result", "result": cached})
                return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        final_result: dict[str, Any] | None = None
        analyzer_error: Exception | None = None

        async def on_progress(ev: dict[str, Any]) -> None:
            await queue.put(ev)

        async def run() -> None:
            nonlocal final_result, analyzer_error
            analyzer = _analyzer(request, settings)
            try:
                final_result = await analyzer.analyze_repo(repo, on_progress=on_progress)
            except Exception as exc:  # noqa: BLE001
                analyzer_error = exc
            finally:
                await queue.put({"__done__": True})

        task = asyncio.create_task(run())
        try:
            while True:
                if await request.is_disconnected():
                    logger.info("Client disconnected from analysis stream for '%s'.", repo)
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield event("progress", {"message": "working"})
                    continue

                if ev.get("__done__"):
                    break
                # Translate the analyzer's internal payload (``{"type": ...,
                # ...rest}``) into an SSE frame. JSON-encode the data so the
                # browser gets valid JSON (sse-starlette writes str(dict)
                # otherwise, which JSON.parse rejects).
                ev_type = ev.pop("type", "progress")
                yield event(ev_type, ev)

            if analyzer_error is not None:
                logger.exception("Analysis failed for %s", repo, exc_info=analyzer_error)
                yield event("error", {"message": f"Analysis failed: {analyzer_error}"})
            elif final_result is not None:
                await cache.set_json(_result_cache_key(repo), final_result)
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return EventSourceResponse(event_generator(), media_type="text/event-stream")
