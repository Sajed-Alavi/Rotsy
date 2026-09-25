"""Shared test fixtures.

No real Postgres/Redis is required: ``make_settings`` builds a valid
``Settings`` instance from explicit kwargs (bypassing any real .env), and
``db_session`` runs against an in-memory SQLite database via aiosqlite so
model-backed tests (e.g. image-scope logic) don't need a live Postgres.

``api`` goes one level up: it builds the *real* FastAPI application and
drives it over ASGI, so a test exercises routing, dependency resolution and
— critically — the permission dependencies declared on each route. Tests
that call a service function directly cannot see an endpoint whose
``dependencies=[...]`` list is missing a check; those are exactly the bugs
this fixture exists to catch.
"""

from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models  # noqa: F401 - import registers every table on Base.metadata
from app.config import Settings
from app.db.base import Base

# Every value here is deliberately NOT a placeholder Settings rejects, and
# not a real secret either — these only ever back an in-memory test process.
BASE_SETTINGS_KWARGS: dict = dict(
    DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
    JWT_SECRET="test-only-jwt-secret-0123456789abcdef",
    # Deliberately different from JWT_SECRET — Settings rejects them being equal.
    NEXUS_CONFIG_ENCRYPTION_KEY="test-only-at-rest-key-fedcba9876543210",
    JWT_ALGORITHM="HS256",
    JWT_ACCESS_TTL_SECONDS=900,
    JWT_REFRESH_TTL_SECONDS=604800,
    SESSION_IDLE_TIMEOUT_SECONDS=1800,
    COOKIE_SECURE=False,
    FRONTEND_ORIGIN="http://localhost:8080",
    BOOTSTRAP_ADMIN_USERNAME="admin",
    BOOTSTRAP_ADMIN_PASSWORD="a-strong-test-only-password-123",
    BOOTSTRAP_ADMIN_EMAIL="admin@example.com",
    REDIS_URL="redis://localhost:6379/0",
    CACHE_TTL_SECONDS=300,
    ANALYZER_MAX_CONCURRENCY=15,
    ANALYZER_REQUEST_TIMEOUT=15,
    METRIC_COLLECTION_INTERVAL_SECONDS=300,
    METRIC_RETENTION_DAYS=90,
    RETENTION_RUN_AT="02:30",
    SCANNER_DB_UPDATE_INTERVAL_HOURS=24,
    SCANNERS_ENABLED="trivy,grype",
    BACKEND_HOST="0.0.0.0",
    BACKEND_PORT=8000,
    LOG_LEVEL="INFO",
)


def make_settings(**overrides) -> Settings:
    """Build a valid ``Settings`` instance, overriding only what a test needs.

    Explicit init kwargs take precedence over env vars/.env in pydantic-settings,
    so this is unaffected by whatever real .env happens to exist on disk.
    """
    return Settings(**{**BASE_SETTINGS_KWARGS, **overrides})


@pytest_asyncio.fixture
async def db_session():
    """An in-memory SQLite session with the full ORM schema created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ``app.main`` builds the application at import time, and ``create_app()``
# reads configuration through the process-wide ``get_settings()``. The test
# image deliberately ships no .env, so seed the environment here — at conftest
# import, before any test module can pull in ``app.main``. Tests that build
# their own Settings via ``make_settings`` are unaffected: explicit kwargs
# still win over the environment in pydantic-settings.
for _key, _value in BASE_SETTINGS_KWARGS.items():
    os.environ.setdefault(_key, str(_value))


class ApiHarness:
    """The real app, an ASGI client for it, and the knobs a test needs.

    ``authenticate_as`` overrides ``get_current_user`` rather than minting a
    cookie. That is deliberate: the point of these tests is the *authorization*
    layer (permission keys, project membership) rather than token decoding,
    which ``test_security.py`` already covers, and it keeps each test from
    paying for a bcrypt hash. ``login`` exists for the one test that should
    prove the real cookie path still works end to end.
    """

    def __init__(self, app, client) -> None:
        self.app = app
        self.client = client

    def authenticate_as(self, user) -> None:
        from app.dependencies import get_current_user

        self.app.dependency_overrides[get_current_user] = lambda: user

    def logout(self) -> None:
        from app.dependencies import get_current_user

        self.app.dependency_overrides.pop(get_current_user, None)

    async def login(self, username: str, password: str):
        return await self.client.post(
            "/api/auth/login", json={"username": username, "password": password},
        )


@pytest_asyncio.fixture
async def api(db_session):
    """The real FastAPI app, driven over ASGI against the in-memory database.

    The application's lifespan is never run — httpx's ASGITransport does not
    fire startup/shutdown — so none of the background loops (metrics,
    retention, scanner DB, Telegram polling) start during tests. That also
    leaves ``app.state`` empty, so ``AppState`` resolves to
    ``nexus=None, cache=None`` and any endpoint needing the job queue answers
    503. That is useful rather than limiting: a 503 from inside a handler
    proves the request got *past* every authorization dependency, which is
    what these tests assert about.
    """
    import httpx

    from app.config import get_settings
    from app.db.session import get_session
    from app.main import create_app

    settings = make_settings()
    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override
    application.dependency_overrides[get_settings] = lambda: settings

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield ApiHarness(application, client)
    application.dependency_overrides.clear()
