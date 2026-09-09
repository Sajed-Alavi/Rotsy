"""Redis-backed rate limiting.

Rotsy had none anywhere, which mattered most on ``POST /api/auth/login``.
Passwords are bcrypt-hashed, so guessing is slow — but that cuts both ways:
each attempt costs the *server* a deliberately expensive hash, so an
unthrottled login endpoint is both an online password-guessing oracle and a
cheap way for an unauthenticated caller to saturate the CPU. Slow hashing
turns brute force into denial of service unless something bounds the attempt
rate.

**Fails open.** If Redis is unavailable the limiter allows the request rather
than denying it. A rate limiter that starts rejecting everything the moment
its own backing store hiccups converts a cache outage into a total outage;
that trade is wrong for a self-hosted console where the operator is also the
person locked out. The failure is logged, once per occurrence.

Fixed windows rather than a sliding log: two counters and an expiry, versus a
sorted set per subject. The imprecision at a window boundary (up to twice the
limit across two adjacent windows) does not matter for the thing this
protects against, and the cheaper structure is one round trip.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from .cache import Cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    """``max_attempts`` per ``window_seconds`` for one subject."""

    max_attempts: int
    window_seconds: int

    @property
    def retry_after(self) -> int:
        return self.window_seconds


#: Login is the one endpoint an unauthenticated caller can drive into bcrypt.
#: Per-IP catches a single host hammering; per-username catches a distributed
#: attempt at one account, which per-IP alone would miss entirely.
LOGIN_PER_IP = Limit(max_attempts=10, window_seconds=60)
LOGIN_PER_USERNAME = Limit(max_attempts=5, window_seconds=300)

#: Triggering an analysis clones a repository and runs a scanner. It is
#: authorized, so this is not an abuse control so much as a guard against a
#: stuck client (or a held-down button) queueing the same expensive work
#: repeatedly.
ANALYSIS_PER_USER = Limit(max_attempts=10, window_seconds=60)


def client_ip(request: Request) -> str:
    """The caller's address, honouring ``X-Forwarded-For`` when present.

    Rotsy runs behind its own nginx, so ``request.client.host`` would
    otherwise be the proxy for every caller — one shared bucket, so the first
    attacker would rate-limit every legitimate user. Only the left-most entry
    is used, and only because the deployment's own reverse proxy sets it; do
    not treat this value as trustworthy for anything but bucketing.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


async def check(cache: Cache | None, bucket: str, subject: str, limit: Limit) -> None:
    """Count one attempt against ``bucket:subject``; raise 429 past ``limit``.

    Raises :class:`fastapi.HTTPException` with a ``Retry-After`` header so a
    well-behaved client knows when to come back.
    """
    if not await allow(cache, bucket, subject, limit):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Please wait and try again.",
            headers={"Retry-After": str(limit.retry_after)},
        )


async def allow(cache: Cache | None, bucket: str, subject: str, limit: Limit) -> bool:
    """``True`` if this attempt is within ``limit``. Never raises."""
    if cache is None or cache.redis is None:
        return True  # fail open — see module docstring
    window = int(time.time()) // limit.window_seconds
    key = f"ratelimit:{bucket}:{subject}:{window}"
    try:
        count = await cache.redis.incr(key)
        if count == 1:
            # Only on the first hit of a window: re-setting the TTL on every
            # attempt would let a steady stream of requests keep the window
            # alive forever and never reset the counter.
            await cache.redis.expire(key, limit.window_seconds)
        return count <= limit.max_attempts
    except Exception:  # noqa: BLE001
        logger.warning("Rate-limit check failed for %s:%s — allowing the request", bucket, subject,
                        exc_info=True)
        return True


async def reset(cache: Cache | None, bucket: str, subject: str, limit: Limit) -> None:
    """Clear a subject's counter — used after a *successful* login so a user
    who mistyped their password a few times is not still throttled once they
    get it right."""
    if cache is None or cache.redis is None:
        return
    window = int(time.time()) // limit.window_seconds
    try:
        await cache.redis.delete(f"ratelimit:{bucket}:{subject}:{window}")
    except Exception:  # noqa: BLE001
        logger.debug("Rate-limit reset failed for %s:%s", bucket, subject, exc_info=True)
