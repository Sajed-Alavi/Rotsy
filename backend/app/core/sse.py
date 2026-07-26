"""Server-Sent Events helpers.

The analyzer streams progress to the browser. ``sse-starlette`` expects each
yielded dict to carry ``event`` (the frame name) and ``data`` as a **string**.
If ``data`` is a dict, we JSON-encode it here — otherwise sse-starlette falls
back to ``str(dict)`` (Python repr with single quotes), which the browser's
``JSON.parse`` cannot read.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Literal

# Canonical event names. The frontend's EventSource switches on these.
ProgressEvent = Literal[
    "phase",     # high-level phase change: scanning_assets, collecting_tags, ...
    "progress",  # incremental percent / message update within a phase
    "cache",     # a cached result was found and will be returned without re-scanning
    "result",    # the final completed result payload
    "error",     # a fatal error during the stream
]


def event(name: ProgressEvent, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a normalised SSE frame dict for ``sse-starlette``.

    ``data`` is JSON-encoded so the wire format is ``data: {"key": ...}``
    (valid JSON) rather than Python repr. Yields on the wire as::

        event: <name>
        data: <json>

    """
    return {"event": name, "data": json.dumps(data or {}, default=str)}


async def drain(gen: AsyncIterator[dict[str, Any]]):
    """Consume an async generator to completion, ignoring values."""
    async for _ in gen:
        pass


__all__ = ["ProgressEvent", "event", "drain"]

