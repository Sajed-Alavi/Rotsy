"""Job retry/timeout policy and rate limiting.

Both are reliability features whose *defaults* matter as much as their
behaviour: retrying is off unless a job type opts in, and the rate limiter
allows the request when its own backing store is unavailable. Those two
choices are what these tests mostly pin, because getting either backwards
fails in a way that is worse than not having the feature at all.
"""

from __future__ import annotations


import pytest

from app.core import rate_limit
from app.core.jobs import DEFAULT_POLICY, Job, JobPolicy, JobRunner, _unflatten


# --- policy defaults -------------------------------------------------------


def test_retries_and_timeouts_are_off_by_default():
    """Repeating work is only safe when the work is idempotent, so a type has
    to opt in. A default of "retry everything" would re-run archive jobs."""
    assert DEFAULT_POLICY.max_retries == 0
    assert DEFAULT_POLICY.timeout is None


def test_an_unregistered_type_gets_the_default_policy():
    runner = JobRunner(cache=None)  # type: ignore[arg-type]
    assert runner.policy_for("never-registered") is DEFAULT_POLICY


def test_registering_with_a_policy_stores_it():
    runner = JobRunner(cache=None)  # type: ignore[arg-type]

    async def _handler(job, progress):
        return {}

    runner.register("thing", _handler, JobPolicy(max_retries=3, timeout=12.0))

    policy = runner.policy_for("thing")
    assert policy.max_retries == 3
    assert policy.timeout == 12.0


def test_registering_without_a_policy_leaves_the_default():
    runner = JobRunner(cache=None)  # type: ignore[arg-type]

    async def _handler(job, progress):
        return {}

    runner.register("thing", _handler)
    assert runner.policy_for("thing").max_retries == 0


# --- backoff ---------------------------------------------------------------


def test_retry_delay_backs_off_exponentially():
    policy = JobPolicy(max_retries=5, retry_delay=5.0)
    assert policy.delay_for(0) == 5.0
    assert policy.delay_for(1) == 10.0
    assert policy.delay_for(2) == 20.0


def test_retry_delay_is_capped():
    """Uncapped doubling overflows into absurd sleeps; the exponent is
    clamped as well as the result."""
    policy = JobPolicy(max_retries=100, retry_delay=5.0)
    assert policy.delay_for(50) == 300.0
    assert policy.delay_for(1000) == 300.0


# --- retry state round-trips through Redis ---------------------------------


def test_retry_state_survives_serialisation():
    """The count lives on the job, not in the worker, so an attempt survives
    the process that made it."""
    job = Job(
        id="abc", type="t", status="pending", progress=0, message="", payload={},
        result=None, created_at=1.0, updated_at=1.0, retry_count=2, max_retries=3,
    )
    restored = _unflatten({k: str(v) for k, v in job.to_dict().items() if v is not None})

    assert restored.retry_count == 2
    assert restored.max_retries == 3


def test_a_job_hash_without_retry_fields_still_loads():
    """Jobs enqueued before these fields existed are still sitting in Redis
    with a 7-day TTL; reading one must not raise."""
    restored = _unflatten({
        "id": "abc", "type": "t", "status": "pending", "progress": "0",
        "message": "", "created_at": "1.0", "updated_at": "1.0",
    })
    assert restored.retry_count == 0
    assert restored.max_retries == 0


# --- rate limiting ---------------------------------------------------------


class _FakeRedis:
    """Just the two commands the limiter uses."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expires[key] = seconds

    async def delete(self, key: str) -> None:
        self.counters.pop(key, None)


class _FakeCache:
    def __init__(self, redis=None) -> None:
        self.redis = redis


async def test_requests_under_the_limit_are_allowed():
    cache = _FakeCache(_FakeRedis())
    rule = rate_limit.Limit(max_attempts=3, window_seconds=60)

    for _ in range(3):
        assert await rate_limit.allow(cache, "b", "subject", rule) is True


async def test_requests_over_the_limit_are_denied():
    cache = _FakeCache(_FakeRedis())
    rule = rate_limit.Limit(max_attempts=2, window_seconds=60)

    assert await rate_limit.allow(cache, "b", "s", rule) is True
    assert await rate_limit.allow(cache, "b", "s", rule) is True
    assert await rate_limit.allow(cache, "b", "s", rule) is False


async def test_subjects_are_counted_separately():
    cache = _FakeCache(_FakeRedis())
    rule = rate_limit.Limit(max_attempts=1, window_seconds=60)

    assert await rate_limit.allow(cache, "b", "alice", rule) is True
    assert await rate_limit.allow(cache, "b", "bob", rule) is True


async def test_the_ttl_is_set_once_per_window():
    """Re-setting the expiry on every attempt would let a steady stream keep
    the window alive forever, so the counter would never reset."""
    redis = _FakeRedis()
    cache = _FakeCache(redis)
    rule = rate_limit.Limit(max_attempts=10, window_seconds=60)

    for _ in range(4):
        await rate_limit.allow(cache, "b", "s", rule)

    assert len(redis.expires) == 1


async def test_a_successful_login_clears_the_counter():
    cache = _FakeCache(_FakeRedis())
    rule = rate_limit.Limit(max_attempts=2, window_seconds=60)

    await rate_limit.allow(cache, "b", "s", rule)
    await rate_limit.allow(cache, "b", "s", rule)
    await rate_limit.reset(cache, "b", "s", rule)

    assert await rate_limit.allow(cache, "b", "s", rule) is True


async def test_the_limiter_fails_open_without_redis():
    """A limiter that denies everything when its own store is down turns a
    cache blip into a total outage — and locks out the operator who would
    fix it."""
    assert await rate_limit.allow(None, "b", "s", rate_limit.LOGIN_PER_IP) is True
    assert await rate_limit.allow(_FakeCache(None), "b", "s", rate_limit.LOGIN_PER_IP) is True


async def test_the_limiter_fails_open_when_redis_errors():
    class _BrokenRedis:
        async def incr(self, key):
            raise RuntimeError("redis is down")

    assert await rate_limit.allow(
        _FakeCache(_BrokenRedis()), "b", "s", rate_limit.LOGIN_PER_IP,
    ) is True


async def test_check_raises_429_with_retry_after():
    from fastapi import HTTPException

    cache = _FakeCache(_FakeRedis())
    rule = rate_limit.Limit(max_attempts=1, window_seconds=45)
    await rate_limit.check(cache, "b", "s", rule)

    with pytest.raises(HTTPException) as exc_info:
        await rate_limit.check(cache, "b", "s", rule)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "45"


# --- client address bucketing ----------------------------------------------


def _request_with(headers: dict, host: str | None = "10.0.0.1"):
    class _Client:
        def __init__(self, h): self.host = h

    class _Request:
        def __init__(self):
            self.headers = headers
            self.client = _Client(host) if host else None

    return _Request()


def test_forwarded_for_is_preferred_behind_the_proxy():
    """Rotsy sits behind its own nginx, so without this every caller shares
    one bucket and the first attacker throttles everyone."""
    assert rate_limit.client_ip(
        _request_with({"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    ) == "203.0.113.9"


def test_the_direct_peer_is_used_without_the_header():
    assert rate_limit.client_ip(_request_with({})) == "10.0.0.1"


def test_a_missing_client_does_not_raise():
    assert rate_limit.client_ip(_request_with({}, host=None)) == "unknown"
