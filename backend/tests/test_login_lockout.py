"""Five consecutive wrong passwords lock an account for 60 seconds.

The unit tests pin the lockout's rules; the HTTP tests drive the real login
route, because the property that matters most — the correct password is
refused while locked — only exists if the route checks the lock *before*
verifying the password, and a unit test of the helpers cannot see that order.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.core import rate_limit
from app.core.security import hash_password
from app.models import Role, User

PASSWORD = "a-strong-test-only-password-123"


class _FakeRedis:
    """The handful of commands the lockout uses, with expiry the test controls."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds

    async def set(self, key: str, value, ex: int | None = None, nx: bool = False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)

    async def ttl(self, key: str) -> int:
        if key not in self.values:
            return -2
        return self.ttls.get(key, -1)

    def expire_now(self, key: str) -> None:
        """Stand-in for the 60 seconds passing."""
        self.values.pop(key, None)
        self.ttls.pop(key, None)


class _FakeCache:
    def __init__(self, redis=None) -> None:
        self.redis = redis


def _lock_key(username: str) -> str:
    return f"login-lock:{username}"


# --- the rules ---------------------------------------------------------------


async def test_four_failures_do_not_lock():
    cache = _FakeCache(_FakeRedis())
    for _ in range(4):
        assert await rate_limit.record_login_failure(cache, "alice") == 0
    assert await rate_limit.lockout_remaining(cache, "alice") == 0


async def test_the_fifth_consecutive_failure_locks_for_60_seconds():
    cache = _FakeCache(_FakeRedis())
    for _ in range(4):
        await rate_limit.record_login_failure(cache, "alice")

    assert await rate_limit.record_login_failure(cache, "alice") == 60
    assert await rate_limit.lockout_remaining(cache, "alice") == 60


async def test_a_success_in_between_resets_the_run():
    """Four wrong, one right, four wrong is not five in a row."""
    cache = _FakeCache(_FakeRedis())
    for _ in range(4):
        await rate_limit.record_login_failure(cache, "alice")
    await rate_limit.clear_login_failures(cache, "alice")
    for _ in range(4):
        assert await rate_limit.record_login_failure(cache, "alice") == 0

    assert await rate_limit.lockout_remaining(cache, "alice") == 0


async def test_accounts_are_locked_independently():
    cache = _FakeCache(_FakeRedis())
    for _ in range(5):
        await rate_limit.record_login_failure(cache, "alice")

    assert await rate_limit.lockout_remaining(cache, "alice") == 60
    assert await rate_limit.lockout_remaining(cache, "bob") == 0


async def test_after_the_lock_expires_the_user_has_five_fresh_attempts():
    """The count is cleared when the lock is set; otherwise the first mistake
    after the lock lifted would re-lock immediately."""
    redis = _FakeRedis()
    cache = _FakeCache(redis)
    for _ in range(5):
        await rate_limit.record_login_failure(cache, "alice")
    redis.expire_now(_lock_key("alice"))

    for _ in range(4):
        assert await rate_limit.record_login_failure(cache, "alice") == 0


async def test_failure_memory_expires_so_old_typos_do_not_accumulate():
    redis = _FakeRedis()
    cache = _FakeCache(redis)
    await rate_limit.record_login_failure(cache, "alice")

    assert redis.ttls["login-fail:alice"] == rate_limit.FAILURE_MEMORY_SECONDS


async def test_assert_not_locked_raises_429_with_retry_after():
    cache = _FakeCache(_FakeRedis())
    for _ in range(5):
        await rate_limit.record_login_failure(cache, "alice")

    with pytest.raises(HTTPException) as exc_info:
        await rate_limit.assert_not_locked(cache, "alice")

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "60"
    assert "60 seconds" in exc_info.value.detail


async def test_a_lock_without_an_expiry_is_never_treated_as_permanent():
    """Redis reports -1 for a key with no TTL. That must read as unlocked, not
    as a lock that never ends."""
    redis = _FakeRedis()
    redis.values[_lock_key("alice")] = "1"  # present, no expiry
    assert await rate_limit.lockout_remaining(_FakeCache(redis), "alice") == 0


async def test_lockout_fails_open_without_redis():
    """With Redis down nobody is locked out, rather than everybody."""
    assert await rate_limit.lockout_remaining(None, "alice") == 0
    assert await rate_limit.record_login_failure(None, "alice") == 0
    assert await rate_limit.lockout_remaining(_FakeCache(None), "alice") == 0


async def test_lockout_fails_open_when_redis_errors():
    class _BrokenRedis:
        async def ttl(self, key):
            raise RuntimeError("redis is down")

        async def incr(self, key):
            raise RuntimeError("redis is down")

    cache = _FakeCache(_BrokenRedis())
    assert await rate_limit.lockout_remaining(cache, "alice") == 0
    assert await rate_limit.record_login_failure(cache, "alice") == 0


# --- through the real login route ---------------------------------------------


async def _account(session, username: str = "alice") -> User:
    role = Role(name=f"role-{id(object())}", access_mode="unrestricted")
    session.add(role)
    user = User(
        username=username, email=f"{username}@example.com",
        password_hash=hash_password(PASSWORD), is_active=True, roles=[role],
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    user.last_seen_at = datetime.now(timezone.utc)
    return user


@pytest.fixture
def redis(api):
    """Attach a cache to the app so the route sees one — the `api` fixture
    runs without the lifespan, and the lockout fails open without Redis."""
    fake = _FakeRedis()
    api.app.state.cache = _FakeCache(fake)
    return fake


async def test_route_locks_after_five_wrong_passwords(api, db_session, redis):
    await _account(db_session)

    statuses = [(await api.login("alice", "wrong")).status_code for _ in range(5)]

    # Four ordinary rejections, then the fifth reports the lock it caused.
    assert statuses == [401, 401, 401, 401, 429]


async def test_route_refuses_the_correct_password_while_locked(api, db_session, redis):
    """The property that makes it a lockout. Checked before the password is
    verified, so a guess that happens to be right still gets nowhere."""
    await _account(db_session)
    for _ in range(5):
        await api.login("alice", "wrong")

    resp = await api.login("alice", PASSWORD)

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert "access_token" not in resp.cookies


async def test_route_lets_the_user_back_in_once_the_lock_expires(api, db_session, redis):
    await _account(db_session)
    for _ in range(5):
        await api.login("alice", "wrong")
    redis.expire_now(_lock_key("alice"))

    resp = await api.login("alice", PASSWORD)

    assert resp.status_code == 200
    assert "access_token" in resp.cookies


async def test_route_does_not_reveal_whether_a_username_exists(api, redis):
    """An unknown username locks exactly like a real one — otherwise five
    wrong passwords would answer 'does this account exist?'."""
    statuses = [(await api.login("nobody-here", "wrong")).status_code for _ in range(5)]
    assert statuses == [401, 401, 401, 401, 429]


async def test_route_success_resets_the_failure_run(api, db_session, redis):
    await _account(db_session)
    for _ in range(4):
        await api.login("alice", "wrong")
    assert (await api.login("alice", PASSWORD)).status_code == 200

    # A fresh run: four more mistakes are still under the threshold.
    statuses = [(await api.login("alice", "wrong")).status_code for _ in range(4)]
    assert statuses == [401, 401, 401, 401]
