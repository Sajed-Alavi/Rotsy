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
#: This per-source throttle counts every attempt, so one host cycling through
#: usernames is bounded even though each username only sees a few tries. The
#: per-account rule is the lockout below, which counts *failures*.
LOGIN_PER_IP = Limit(max_attempts=10, window_seconds=60)

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


# --- login lockout -----------------------------------------------------------
#
# A lockout, not a rate limit, and the difference is the point. The fixed-window
# per-username limit this replaces counted *attempts* — so a successful login
# used one of the five — and its "lock" lasted only until the window rolled
# over: five failures landing in the last second of a window were followed by
# a fresh window one second later. The lockout duration was an accident of the
# clock.
#
# This counts consecutive *failures* for an account, and on the fifth sets a
# separate lock key with its own 60-second expiry, measured from that failure.
# A success clears the count. While the lock exists every attempt is refused —
# including one with the correct password, which is what makes it a lockout:
# otherwise a guess that happened to be right would still get through.
#
# Keyed on the username as typed, whether or not that account exists. Locking
# only real accounts would answer "does this username exist?" to anyone
# willing to type five wrong passwords.
#
# Trade-off, stated plainly: per-account lockout lets someone lock a user out
# by typing wrong passwords for them. Keeping the lock short (60s) bounds that
# to an annoyance — sustaining it takes five attempts a minute, which the
# per-source throttle above also sees.

LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 60
#: How long a run of failures is remembered with no new failure. Long enough
#: that "five in a row" means five in a row, short enough that one typo last
#: week does not count towards a lock today.
FAILURE_MEMORY_SECONDS = 15 * 60


def _failure_key(username: str) -> str:
    return f"login-fail:{username[:64]}"


def _lock_key(username: str) -> str:
    return f"login-lock:{username[:64]}"


def lockout_error(seconds: int) -> HTTPException:
    """The 429 returned when an attempt triggers or meets a lockout."""
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Too many failed login attempts. Try again in {seconds} seconds.",
        headers={"Retry-After": str(seconds)},
    )


async def lockout_remaining(cache: Cache | None, username: str) -> int:
    """Seconds left on ``username``'s lock, or 0 if it is not locked.

    Fails open like the rest of this module: with Redis unavailable nobody is
    locked out, rather than everybody.
    """
    if cache is None or cache.redis is None:
        return 0
    try:
        ttl = await cache.redis.ttl(_lock_key(username))
    except Exception:  # noqa: BLE001
        logger.warning("Lockout check failed for %r — allowing the attempt", username[:64], exc_info=True)
        return 0
    # Redis answers -2 for a missing key and -1 for one with no expiry; the
    # latter should never exist here, and must not become a permanent lock.
    return int(ttl) if ttl and ttl > 0 else 0


async def assert_not_locked(cache: Cache | None, username: str) -> None:
    """Raise 429 if ``username`` is currently locked out."""
    remaining = await lockout_remaining(cache, username)
    if remaining:
        raise lockout_error(remaining)


async def record_login_failure(cache: Cache | None, username: str) -> int:
    """Count one failed attempt. On reaching the threshold, lock the account
    for :data:`LOCKOUT_SECONDS` and return that duration; otherwise return 0.

    The failure count is cleared when the lock is set, so once the lock expires
    the user has a full five attempts again rather than being re-locked by the
    next single mistake.
    """
    if cache is None or cache.redis is None:
        return 0
    redis = cache.redis
    try:
        failures = await redis.incr(_failure_key(username))
        # Refreshed on every failure: the memory is "since the last failure",
        # so a slow, steady run of wrong guesses still accumulates.
        await redis.expire(_failure_key(username), FAILURE_MEMORY_SECONDS)
        if failures >= LOCKOUT_THRESHOLD:
            await redis.set(_lock_key(username), "1", ex=LOCKOUT_SECONDS)
            await redis.delete(_failure_key(username))
            logger.warning(
                "Locked login for %r for %ds after %d consecutive failures",
                username[:64], LOCKOUT_SECONDS, failures,
            )
            return LOCKOUT_SECONDS
    except Exception:  # noqa: BLE001
        logger.warning("Could not record a login failure for %r", username[:64], exc_info=True)
    return 0


async def clear_login_failures(cache: Cache | None, username: str) -> None:
    """A successful login resets the run — the failures were not consecutive."""
    if cache is None or cache.redis is None:
        return
    try:
        await cache.redis.delete(_failure_key(username))
    except Exception:  # noqa: BLE001
        logger.debug("Could not clear login failures for %r", username[:64], exc_info=True)
