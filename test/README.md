# Nexus Advanced Wrapper — Documentation

This folder documents **everything**: what's built, how to install 0→100, the
full API surface, configuration, architecture, and operational notes.

Companion files in this folder:
- `INSTALL.md` — step-by-step install (Docker, local dev, troubleshooting).
- `API.md` — complete list of every API endpoint, methods, payloads, permissions.
- `CHANGELOG.md` — what each release implemented.

For interactive API exploration (try-it-now), the backend serves Swagger UI at
**`http://<backend-host>:<port>/docs`** and ReDoc at **`/redoc`**.

---

## What this project is

An advanced web wrapper around Sonatype Nexus Repository Manager. It speaks to
one primary Nexus instance (the one you point it at via env vars) and adds:

| Area | Capability |
|---|---|
| Auth | JWT in httpOnly cookies, RBAC with fine-grained permissions, idle logout |
| Storage | Deep analyzer (Docker manifest traversal + generic per-component sizing) |
| Browse | File browser + authenticated proxy download |
| Retention | Rule-based cleanup (keep last N, delete older than X days), daily scheduler, blob compaction |
| Metrics | Real-time + historical (Postgres), per-repo trends |
| Alerts | Webhook rules (Slack/Discord/generic) |
| Jobs | Redis-backed background queue, live progress over SSE |
| Scan | Trivy + Grype per-repo, auto-scan on push, dashboard |
| Backup | Trigger Nexus backup task + DB snapshot download |
| Sync | Nexus → Nexus component migration (non-docker) |
| UI | Dark/Light theme toggle, dense console-style, theme-aware everywhere |

---

## Architecture

```
        ┌─────────────────────────────────────────┐
        │                  Browser                 │
        └────────────────┬────────────────────────┘
                         │ HTTP (cookies)
        ┌────────────────▼────────────────────────┐
        │         Frontend (nginx :80 or :8080)    │
        │   SPA + /api reverse-proxy to backend    │
        └────────────────┬────────────────────────┘
                         │
        ┌────────────────▼────────────────────────┐
        │         Backend (FastAPI :8000)           │
        │  - Auth (JWT), RBAC                       │
        │  - REST routers (all features)            │
        │  - Background loops:                      │
        │       metrics collector                   │
        │       daily retention scheduler           │
        │       scanner DB refresh                  │
        │       auto-scan poller                    │
        │  - JobRunner (Redis queue + worker)       │
        │  - Trivy + Grype binaries (in image)      │
        └──┬────────────┬──────────────┬───────────┘
           │            │              │
   ┌───────▼───┐  ┌─────▼─────┐  ┌────▼──────────┐
   │ Postgres  │  │   Redis   │  │  Nexus (host)  │
   │ users,    │  │  cache +  │  │  via host.     │
   │ metrics,  │  │  jobs     │  │  docker.       │
   │ scans...  │  │           │  │  internal      │
   └───────────┘  └───────────┘  └───────────────┘
```

### Background jobs (Redis queue)

All heavy work goes through the in-app job queue (no Celery). Each job has:
- a Redis hash (`job:{id}`) with status / progress / result,
- a Redis list (`job:{id}:events`) of progress events consumed by the SSE
  stream,
- a worker handler registered in `app.main` lifespan.

Registered job types:
- `collect_metrics` — snapshot all repos, evaluate alerts.
- `analyze_repo` — deep-analyze one repo.
- `run_retention` — execute one or all retention policies (+ compaction).
- `backup` — trigger the Nexus backup task.
- `sync` — copy components source→target Nexus.
- `scan_image` — run Trivy/Grype on an image.
- `scanner_db_update` — refresh vulnerability databases.

### Periodic loops (in lifespan)

| Loop | Default cadence | Env var |
|---|---|---|
| Metric collection | every 5 min | `METRIC_COLLECTION_INTERVAL_SECONDS` |
| Retention sweep | daily at `RETENTION_RUN_AT` | `RETENTION_RUN_AT` (HH:MM) |
| Scanner DB refresh | every 24 h | `SCANNER_DB_UPDATE_INTERVAL_HOURS` |
| Auto-scan poller | every 60 s | hardcoded (sweeps enabled repos) |

---

## Quick start (Docker)

See `INSTALL.md` for the full walkthrough. The 4-line version:

```bash
cp .env.example .env          # then edit (NEXUS_URL, creds, JWT_SECRET, etc.)
docker compose build          # build backend + frontend images (one time)
docker compose up -d          # start postgres + redis + backend + frontend
open http://localhost:8080    # log in with BOOTSTRAP_ADMIN_USERNAME / _PASSWORD
```

Interactive API docs: **`http://localhost:8000/docs`**.

---

## Configuration

All configuration is environment-driven (no defaults in code — fail-fast). See
`.env.example` for the canonical list with comments. Highlights:

| Variable | Purpose |
|---|---|
| `NEXUS_URL` | Primary Nexus base URL (use `host.docker.internal` when Nexus is on the host). |
| `NEXUS_USERNAME` / `NEXUS_PASSWORD` | Service account for the REST + registry APIs. |
| `NEXUS_VERIFY_SSL` | TLS verify toggle (set false only for self-signed internal). |
| `DATABASE_URL` | Postgres URL (`postgresql+asyncpg://...`). |
| `JWT_SECRET` | Strong random secret. |
| `SESSION_IDLE_TIMEOUT_SECONDS` | Idle logout window (default 1800 = 30 min). |
| `BOOTSTRAP_ADMIN_*` | First admin user created on startup. |
| `RETENTION_RUN_AT` | `HH:MM` daily retention sweep time. |
| `SCANNERS_ENABLED` | `trivy,grype` — order is run order. |
| `SCANNER_DB_UPDATE_INTERVAL_HOURS` | Vuln-DB refresh cadence. |

---

## Permissions (RBAC)

Stored in Postgres. The seeded system roles:
- `admin` — every permission.
- `operator` — read + run scans/retention + most operational perms.
- `profile:edit` is granted to everyone so users can edit their own profile.

Custom roles can be created from the **Roles** page; admins tick individual
permission keys. Permissions catalog: `app/core/permissions.py`.

---

## Operational notes

- **Idle logout**: `last_seen_at` is updated on every authenticated request;
  `/auth/refresh` refuses if the gap exceeds `SESSION_IDLE_TIMEOUT_SECONDS`.
- **Retention + blob compaction**: after deleting components via Nexus DELETE,
  the wrapper triggers the `blobstore.compact` task so physical blobs are
  actually reclaimed (otherwise only metadata is removed).
- **Scanning**: requires Docker-format repos. Trivy/Grype run inside the
  backend container and authenticate to Nexus' registry with the service
  account. The first scan downloads the vuln DB (~hundreds of MB) — handled
  by the `scanner_db_update` job at startup.
- **Sync** copies components for non-docker repos (maven2, nuget, npm, raw).
  Docker push requires the registry v2 API and is out of scope for this pass.
- **CORS / cookies**: cookies are scoped to path `/api` so they reach every
  protected endpoint; `allow_credentials=true` on CORS lets the browser send
  them across the origin boundary.
