"""Shared application state accessors.

Two concerns live here (both leaf-level to avoid import cycles):

1. :func:`app_state` — per-request typed view of ``app.state.nexus`` /
   ``app.state.cache`` (set by the lifespan).
2. :data:`lifespan_handles` — a module-level dict populated by the lifespan
   so background job handlers (which run outside any request) can reach the
   shared Nexus client, cache, and analyzer tuning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from .core.cache import Cache
from .core.nexus_client import NexusClient


@dataclass
class AppState:
    """Typed view over the shared resources stored on ``app.state``."""

    nexus: NexusClient | None
    cache: Cache | None


def app_state(request: Request) -> AppState:
    """Return the shared :class:`AppState` for this application."""
    return AppState(
        nexus=getattr(request.app.state, "nexus", None),
        cache=getattr(request.app.state, "cache", None),
    )


# Module-level handles for code that runs outside a request (job handlers).
# Populated by ``app.main`` lifespan; cleared on shutdown.
lifespan_handles: dict[str, Any] = {}
