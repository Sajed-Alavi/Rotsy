"""Redis-backed cache with graceful degradation.

The cache is used to make the dashboard faster than the native Nexus UI by
storing analyzer results and list responses with a TTL.

Design choice: cache failures must **never break a request**. If Redis is
unreachable, every method logs a warning and returns a miss / no-op so the
underlying endpoint still serves fresh data from Nexus. This keeps the
application operational even during a Redis outage.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis

from ..config import Settings

logger = logging.getLogger(__name__)


class Cache:
    """Async JSON cache over ``redis.asyncio``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis: redis.Redis | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Connect to Redis. Connection is lazy; a failed ping is logged."""
        if self._redis is not None:
            return
        self._redis = redis.from_url(
            self._settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        try:
            await self._redis.ping()
            logger.info("Cache connected -> %s", self._settings.REDIS_URL)
        except redis.RedisError as exc:  # pragma: no cover - network path
            logger.warning("Cache unavailable (running degraded): %s", exc)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            logger.info("Cache connection closed")

    @property
    def redis(self) -> redis.Redis | None:
        return self._redis

    @property
    def ttl(self) -> int:
        return self._settings.CACHE_TTL_SECONDS

    # ------------------------------------------------------------------
    # Operations (all degrade gracefully on Redis errors)
    # ------------------------------------------------------------------
    async def get_json(self, key: str) -> Any | None:
        """Return the decoded JSON value for ``key`` or ``None`` on miss/error."""
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
        except redis.RedisError as exc:
            logger.warning("Cache get failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Cache hit but undecodable JSON for %s", key)
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store ``value`` as JSON with the configured TTL (or ``ttl`` override)."""
        if self._redis is None:
            return
        try:
            await self._redis.set(key, json.dumps(value, default=str), ex=ttl or self.ttl)
        except redis.RedisError as exc:
            logger.warning("Cache set failed for %s: %s", key, exc)

    async def delete(self, *keys: str) -> None:
        """Delete one or more keys. No-op if Redis is unavailable."""
        if self._redis is None or not keys:
            return
        try:
            await self._redis.delete(*keys)
        except redis.RedisError as exc:
            logger.warning("Cache delete failed for %s: %s", keys, exc)
