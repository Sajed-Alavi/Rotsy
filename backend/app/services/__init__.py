"""Service layer.

:func:`make_emitter` lives here because six long-running services each grew the
same three-line "call the progress callback if there is one" wrapper.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

ProgressCallback = Callable[[int, str], Awaitable[None]]
#: The scanner-database services carry a structured detail payload alongside the
#: message (download byte counts, and so on).
DetailProgressCallback = Callable[[int, str, "dict | None"], Awaitable[None]]


def make_emitter(on_progress: ProgressCallback | None) -> ProgressCallback:
    """Wrap an optional progress callback into one that is always safe to await.

    Long-running services report progress to the job runner when invoked as a
    background job, and to nobody when invoked directly. This collapses that
    difference so call sites can just ``await emit(30, "…")``.
    """
    async def emit(percent: int, message: str) -> None:
        if on_progress is not None:
            await on_progress(percent, message)

    return emit


def make_detail_emitter(
    on_progress: DetailProgressCallback | None,
) -> DetailProgressCallback:
    """:func:`make_emitter` for callbacks that also carry a detail payload."""
    async def emit(percent: int, message: str, detail: dict | None = None) -> None:
        if on_progress is not None:
            await on_progress(percent, message, detail)

    return emit
