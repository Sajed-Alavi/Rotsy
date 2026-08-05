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

from fastapi import HTTPException, Request, status

from .core.cache import Cache
from .modules.nexus.connector import NexusClient


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


def require_nexus(request: Request) -> NexusClient:
    """The shared Nexus client, or 503 if the lifespan has not configured one.

    Usable directly (``require_nexus(request)``) or as a FastAPI dependency.
    """
    client = app_state(request).nexus
    if client is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nexus client not initialised")
    return client


# Module-level handles for code that runs outside a request (job handlers).
# Populated by ``app.main`` lifespan; cleared on shutdown.
lifespan_handles: dict[str, Any] = {}
