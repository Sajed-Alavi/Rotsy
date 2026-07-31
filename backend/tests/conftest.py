"""Shared test fixtures.

No real Postgres/Redis is required: ``make_settings`` builds a valid
``Settings`` instance from explicit kwargs (bypassing any real .env), and
``db_session`` runs against an in-memory SQLite database via aiosqlite so
model-backed tests (e.g. image-scope logic) don't need a live Postgres.
"""

from __future__ import annotations

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
