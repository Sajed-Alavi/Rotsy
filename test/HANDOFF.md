# HANDOFF — Full Project State for the Next AI/Developer

> **Read this first.** This file is the complete architectural map + current
> state + known issues + rules. It supersedes any assumptions from training data.

## Project: Nexus Repository Manager — Advanced Web Wrapper

A FastAPI + React web UI that wraps Sonatype Nexus Repository Manager with
deep storage analysis, retention, monitoring, vulnerability scanning, and more.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2 (async), Alembic, httpx |
| Frontend | React 18, Vite 5, Tailwind CSS 3.4, react-router-dom 6 |
| DB | PostgreSQL 16 (users, roles, permissions, metrics, scans, retention, system_config) |
| Cache/Jobs | Redis 7 (cache + background job queue, no Celery) |
| Scanners | Trivy 0.53.0, Grype 0.79.6, oras 1.3.3 (all in backend Docker image) |
| Auth | JWT in httpOnly cookies, RBAC with fine-grained permissions, idle logout (30 min) |

---

## Project Structure

```
nexus-project/
├── .env                    # Runtime config (DO NOT COMMIT)
├── .env.example            # Template with all vars documented
├── docker-compose.yml      # postgres + redis + backend + frontend
├── test/                   # Documentation (README, INSTALL, API, CHANGELOG, THIS FILE)
├── backend/
│   ├── Dockerfile          # python:3.11-slim + trivy + grype + oras
│   ├── requirements.txt
│   ├── entrypoint.sh       # wait DB → alembic upgrade → seed → uvicorn
│   ├── alembic/versions/   # 6 migrations (see below)
│   └── app/
│       ├── main.py         # FastAPI app, lifespan (4 background loops), router wiring
│       ├── config.py       # pydantic-settings (Nexus config is OPTIONAL — dashboard-managed)
│       ├── state.py        # app_state() + lifespan_handles (job handler shared bag)
│       ├── dependencies.py # get_current_user, RequirePermission, get_session
│       ├── core/
│       │   ├── nexus_client.py    # httpx async client + reconfigure() + paginate()
│       │   ├── cache.py           # Redis wrapper (graceful degradation)
│       │   ├── jobs.py            # Redis job queue + JobRunner (dedicated BLPOP connection)
│       │   ├── security.py        # JWT + bcrypt password hashing
│       │   ├── permissions.py     # 23 permission keys + system role mapping
│       │   ├── config_store.py    # encrypted Nexus creds in DB (Fernet)
│       │   └── sse.py            # SSE event helper (json.dumps data)
│       ├── db/             # base, session (async engine), seed (idempotent)
│       ├── models/         # user, metrics, scans, retention, system_config
│       ├── schemas/        # pydantic request/response models
│       ├── routers/        # 15 routers (see API.md for full list)
│       └── services/       # storage_analyzer, metrics_collector, retention,
│                            # scanners, backup, sync, alerting, notifier, job_handlers
└── frontend/
    ├── Dockerfile          # multi-stage: node build → nginx serve + /api proxy
    ├── nginx.conf          # SPA fallback + /api reverse-proxy + SSE support
    ├── package.json        # react, react-dom, react-router-dom (NO chart library)
    └── src/
        ├── main.jsx        # BrowserRouter + ThemeProvider + AuthProvider
        ├── App.jsx         # route map
        ├── index.css       # dark/light CSS variables
        ├── lib/            # api.js (fetch wrapper), format.js, nav.js
        ├── context/        # AuthContext.jsx, ThemeContext.jsx
        ├── components/     # AppShell, Sidebar, TopBar, Stat, DataTable,
        │                    # Badge, Modal, EmptyState, Icon, TimeSeriesChart,
        │                    # ProgressBar, HealthTile, ProtectedRoute
        └── features/       # auth, dashboard, browse, storage, metrics, jobs,
                             # alerts, scan, retention, system, settings,
                             # users, roles, comingsoon
```

---

## Key Architectural Decisions

### 1. Nexus connection is dashboard-managed (not just .env)
- `NEXUS_URL`/`NEXUS_USERNAME`/`NEXUS_PASSWORD` in `.env` are **optional** — they're
  bootstrap defaults for first launch only.
- After first launch, the admin sets the Nexus connection via **Settings → Nexus
  Connection** in the UI. Stored **encrypted** (Fernet) in the `system_config`
  table.
- `NexusClient.reconfigure()` swaps the live httpx client atomically — no restart.
- On startup, `lifespan` loads the DB config and reconfigures if present.
- `collect_once()` in `metrics_collector.py` checks for empty URL and fails with
  a clear message instead of a cryptic connection error.

### 2. Background jobs via Redis queue (no Celery)
- `JobRunner` in `core/jobs.py` uses a **dedicated Redis connection** with
  `socket_timeout=30` for `BLPOP` (the shared cache client has `socket_timeout=3`
  which killed BLPOP — this was a real bug).
- Job types: `collect_metrics`, `analyze_repo`, `run_retention`, `backup`,
  `sync`, `scan_image`, `scanner_db_update`.
- Progress events stored in Redis list `job:{id}:events`; SSE endpoint
  `/jobs/{id}/stream` tails them.
- **IMPORTANT**: `sse.py` must `json.dumps` the `data` field — sse-starlette
  writes `str(dict)` (Python repr with single quotes) which `JSON.parse` rejects.

### 3. Scanner DB downloads use oras + proxy (+ OFFLINE import for air-gapped)
- `update_scanner_dbs()` in `scanners.py` uses `oras pull` from OCI registry
  (NOT `trivy image --download-db-only` which pulls from GitHub releases and
  is frequently rate-limited/blocked).
- Trivy DB: `oras pull registry-1.docker.io/aquasec/trivy-db:2`
- Trivy Java DB: `oras pull ghcr.io/aquasecurity/trivy-java-db:1`
- Grype DB: `grype db update -v`
- All subprocesses receive `HTTP_PROXY`/`HTTPS_PROXY` env vars when
  `SCANNER_PROXY` is set in `.env`.
- **Live progress**: `_oras_pull_with_progress()` polls the output directory
  size every 2 seconds and emits `"trivy-db: 12.3 / ~50 MB"` messages.
- Old DBs are pruned after download (`_prune_trivy_old()`).
- **OFFLINE / air-gapped path** (for restricted networks where Docker Hub /
  ghcr.io / github.com are blocked): `import_offline_dbs()` reads
  pre-downloaded archives from `SCANNER_OFFLINE_DIR` (host `./offline-db`,
  mounted read-only at `/app/offline-db`) — extracts `db.tar.gz`(+`javadb.tar.gz`)
  for Trivy and runs `grype db import <archive>` for Grype. NO network.
  Endpoints: `POST /scan/db-import`, `GET /scan/db-offline`. UI: "Import
  offline DBs" button on the Scanning page. Fetch helper on an
  internet-connected box: `scripts/fetch-offline-db.sh`.
- **Scheduling**: the background refresh runs every
  `SCANNER_DB_UPDATE_INTERVAL_HOURS`, OR once daily at a fixed
  `SCANNER_DB_UPDATE_AT` (HH:MM) when set. Set `SCANNER_DB_OFFLINE_MODE=true`
  so the scheduled run imports offline archives instead of downloading.

### 4. Four background loops in lifespan
| Loop | Cadence | Env var |
|---|---|---|
| Metric collection | every 5 min | `METRIC_COLLECTION_INTERVAL_SECONDS` |
| Retention sweep | daily at HH:MM | `RETENTION_RUN_AT` |
| Scanner DB refresh | every 24h | `SCANNER_DB_UPDATE_INTERVAL_HOURS` |
| Auto-scan poller | every 60s | hardcoded |

### 5. RBAC
- 23 permission keys in `core/permissions.py`.
- System roles: `admin` (all), `operator` (read + execute), `viewer` (read + profile:edit).
- Custom roles creatable from UI. System roles can't be deleted.
- `RequirePermission("key")` is a callable dependency on write endpoints.

### 6. Theme: dark/light toggle
- `ThemeContext` toggles `dark` class on `<html>`.
- Tailwind `darkMode: 'class'` in tailwind.config.js.
- **Every** component uses `dark:` variants — no hardcoded dark colors.

---

## Migrations (Alembic)

| Revision | Description |
|---|---|
| `20260717_2000` | Initial schema: users, roles, permissions, joins |
| `20260718_2000` | Metrics + alert_rules |
| `20260719_2100` | User last_seen_at + retention_policies |
| `20260720_0100` | Scan targets, scan reports, scan vulnerabilities |
| `20260720_0200` | system_config (dashboard-managed Nexus connection) |

Chain: `20260717_2000` → `20260718_2000` → `20260719_2100` → `20260720_0100` → `20260720_0200`.

`entrypoint.sh` runs `alembic upgrade head` before starting uvicorn.

---

## Known Issues & Gotchas

1. **`from __future__ import annotations` breaks FastAPI dependencies** —
   `RequirePermission.__call__` with `Annotated[User, ...]` fails because
   pydantic can't resolve the string annotation. `dependencies.py` must NOT
   use `from __future__ import annotations`.

2. **Import cycles** — routers must import `app_state` from `..state`, NOT
   `..main`. `main.py` imports routers at the top, so `from ..main import X`
   creates a cycle. `state.py` is a leaf module.

3. **`app.state._state` doesn't work** — Starlette's `State` object reserves
   underscore-prefixed names and returns its `__dict__` for them. Use
   `app.state.nexus` and `app.state.cache` directly (top-level attributes).

4. **`pipeline(transaction=True)` needs `await pipe.execute()`** — without it,
   the pipeline is queued but never committed to Redis. Jobs appear enqueued
   but vanish immediately.

5. **`_SharedHandles` is a dataclass, not a dict** — `job_handlers.py` uses
   `state.nexus`, `state.retention_days` (attributes), not `.get()`.

6. **`api.put` must exist** — the API client (`lib/api.js`) must define `put`
   alongside `get`/`post`/`patch`/`delete`. Missing verbs cause
   `M.put is not a function`.

7. **Cookie path must be `/api`** (not `/api/auth`) — otherwise protected
   endpoints outside `/api/auth` can't read the access cookie → 401.

8. **Docker Hub may be blocked** — the environment uses a mirror
   (`docker.arvancloud.ir` in `/etc/docker/daemon.json`). PyPI is slow —
   `pip install` needs `--retries 6 --timeout 120`.

9. **`host.docker.internal`** — the user's Nexus runs on the host. The backend
   container reaches it via `host.docker.internal:8081` (mapped in
   `docker-compose.yml` via `extra_hosts: host.docker.internal:host-gateway`).

---

## API Surface (see test/API.md for full details)

- **Auth**: `/api/auth/login`, `/logout`, `/refresh`, `/me`
- **Users/Roles**: CRUD with `users:manage` / `roles:manage`
- **Settings**: `/api/settings/profile`, `/password`, `/nexus` (GET/PUT), `/nexus/test`
- **Repositories**: list, browse assets, proxy download
- **Storage**: analyze (SSE stream), repos list
- **Retention**: policies CRUD, preview (dry-run), run (job)
- **Metrics**: overview, health, blobstores, system, realtime, timeseries
- **Alerts**: rules CRUD + auto-evaluation after metric collection
- **Jobs**: list, status, SSE stream, enqueue (metrics/analyze/retention/backup/sync/scan/db-update)
- **Scan**: targets CRUD, run scan, reports, vulnerabilities, summary, db-status
- **System**: backup, sync, scripts (stub)
- **Swagger**: `/docs` and `/redoc`

---

## Running

```bash
cd nexus-project
docker compose build backend frontend
docker compose up -d
# Frontend: http://localhost:8080
# Backend:  http://localhost:8000
# Swagger:  http://localhost:8000/docs
```

Default login: whatever `BOOTSTRAP_ADMIN_USERNAME`/`_PASSWORD` is in `.env`.
Nexus connection: set via Settings → Nexus Connection in the UI (or env for bootstrap).

---

## What's NOT done (future work)

- Docker image sync (requires registry v2 push API — separate effort)
- Blobstore management (Feature C — still 501 stubs)
- Analytics bandwidth/top-downloads (Feature H — still 501 stubs)
- CI/CD tokens + webhooks (Feature G — still 501 stubs)
- Per-user API keys (currently cookie-only)
- Audit log UI (schema not built)
- Tests (pytest/vitest — none written)
- OIDC/SAML SSO
- ARM support for oras (currently pinned linux_amd64)
