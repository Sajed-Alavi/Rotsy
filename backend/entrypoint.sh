#!/bin/sh
# Backend container entrypoint.
#
# 1. Wait for Postgres to accept connections.
# 2. Run Alembic migrations to the latest revision.
# 3. Run the idempotent seed (permissions, system roles, bootstrap admin).
# 4. Start uvicorn.
#
# Environment (DATABASE_URL etc.) is supplied by docker-compose via env_file.
set -e

echo "[entrypoint] waiting for database..."
# Best-effort wait loop using python so we don't need extra shell utilities.
python3 - <<'PY'
import os, sys, time
import psycopg
url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
for attempt in range(1, 61):
    try:
        with psycopg.connect(url, connect_timeout=3):
            print(f"[entrypoint] database reachable after {attempt} attempt(s).")
            sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        if attempt in (1, 5, 15, 30, 60):
            print(f"[entrypoint] attempt {attempt}/60: {exc}")
        time.sleep(1)
print("[entrypoint] database never became reachable; exiting.")
sys.exit(1)
PY

echo "[entrypoint] running alembic migrations..."
alembic upgrade head

echo "[entrypoint] running seed..."
python3 - <<'PY'
import asyncio
from app.config import get_settings
from app.db.seed import run_seed
from app.db.session import get_session_factory

async def main():
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        await run_seed(session, settings)

asyncio.run(main())
PY

echo "[entrypoint] starting uvicorn..."
# Vulnerability databases are managed by the application itself
# (app/services/scanner_db.py, scheduled by _scanner_db_loop): it fetches only
# what is missing at startup and otherwise follows SCANNER_DB_UPDATE_AT /
# SCANNER_DB_UPDATE_INTERVAL_HOURS. A shell script used to do the same job here
# in parallel, ignoring the configured proxy and offline mode, so every boot
# downloaded databases the app was also downloading.
exec uvicorn app.main:app --host "${BACKEND_HOST:-0.0.0.0}" --port "${BACKEND_PORT:-8000}"
