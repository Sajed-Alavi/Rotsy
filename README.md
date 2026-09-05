# Rotsy

A management dashboard and API around **Sonatype Nexus Repository Manager**, with
**static container-image vulnerability scanning** (Trivy + Grype) and **automatic
SonarQube code analysis** on every push to GitHub or GitLab as its two centrepieces.

| Path        | What it is                                                  |
| ----------- | ----------------------------------------------------------- |
| `backend/`  | FastAPI service, Alembic migrations, Trivy + Grype binaries |
| `frontend/` | React + Vite + Tailwind single-page app served by nginx     |

Three design rules run through the whole system. They are not aspirations; each
is enforced in code.

1. **Static analysis only.** No container is ever started, run, or spun up.
   Images are read over the Docker Registry v2 API and analysed as data.
2. **Zero client-side registry configuration.** Docker connector hosts and ports
   are discovered from Nexus. Nothing to type, nothing to keep in sync.
3. **Event-driven scanning.** An image is scanned when it is pushed, or when an
   operator asks. Never on startup, never on a schedule, never twice by accident.

> **Full documentation ships inside the app**, at **/docs** in the sidebar —
> ordered as a learning path, from first login through core concepts, guides,
> database management, administration, an API and configuration reference, and
> end-to-end workflows (CI/CD gating, air-gapped install, responding to a new
> CVE, scaling to many repositories). **This README is only the short version
> for people who have not started it yet** — anything below that raises a
> question is answered at length in there.

---

## What it does

- **Vulnerability scanning** — Trivy and Grype against every image, browsable as
  repository → image → tag → report, with per-CVE detail and PDF export. Scans
  run with bounded concurrency against a private cache replica each.
- **Code Quality** — connect a GitHub or GitLab repository; every push to a
  watched branch clones the commit, runs `sonar-scanner`, and reports back. No
  CI YAML. Run it on demand too. Per-branch Sonar projects are auto-provisioned
  so Community Edition's single-branch limit stops mattering, and each
  repository picks its own quality-gate preset.
- **Projects** — group a source repository with its SonarQube analysis and its
  Nexus artifacts. Carries a documented 0–100 Health Score and a feed of
  Insights (new issues, coverage regressions, gate changes) computed as each
  analysis completes.
- **Telegram bot** *(optional)* — link a Telegram account to a Rotsy user and
  they can check their Project access, manage membership, and trigger analysis
  from chat. Analysis-report PDFs are delivered automatically when a run
  finishes, and failures (analysis, backup, scanner-database update) raise a
  notification. The bot re-derives the user's live permissions on every tap, so
  it can never grant more than the web UI would.
- **Access control** — RBAC permissions ("what") plus two independent scoping
  axes ("where"): per-repository/image access rules, and per-Project membership
  (viewer / member / admin). Closed by default on both.
- **Retention, backups, monitoring** — scheduled tag cleanup with automatic blob
  compaction, scheduled compressed backups, repository metrics with alert rules,
  and SSE-streamed job progress with real cancellation.

---

## Architecture

```
                    ┌──────────────────────────── Docker host ────────────────────────────┐
                    │                                                                     │
  browser ──HTTP──► │  nginx (frontend)                    Nexus Repository Manager       │
                    │    │  /api/* ──proxy──► FastAPI (backend)   :8081  REST API         │
                    │    └─ React SPA              │  │           :15987 docker "team-a"  │
                    │                              │  │           :15988 docker "team-b"  │
                    │                              │  │              ⋮   (discovered)     │
                    │                              │  │                                   │
                    │        Postgres ◄────────────┤  ├──REST───────► repository config,  │
                    │        (state)               │  │               components, assets  │
                    │                              │  │                                   │
                    │        Redis ◄───────────────┤  └──registry v2─► manifests + layers │
                    │        (cache + job queue)   │        (Trivy / Grype, read-only)    │
                    │                              │                                      │
                    │                              └◄── webhook on push ──────────────────┤
                    └─────────────────────────────────────────────────────────────────────┘
```

**Backend layers:** `routers/` handle HTTP only → `services/` (business logic,
framework-agnostic) and `modules/` (one adapter per external system: `nexus`,
`github`, `gitlab`, `sonar`, `telegram`) → `core/` (Nexus client, cache, job
queue, security, access control) and `models/` (SQLAlchemy tables).

Long-running work goes through a Redis-backed job queue (`core/jobs.py`) with
live progress over SSE, so a scan or a database download never blocks a request.
Background loops in the app lifespan handle metrics, the daily retention sweep,
vulnerability-database freshness, a fallback new-image watcher, and Telegram
long-polling. **None of them scan on startup.**

**Scan state lives in a durable Postgres ledger**, not a cache — every image
Rotsy has seen and its state (`baseline`, `queued`, `scanned`, `failed`), so
nothing is silently re-scanned after a restart. The first time a repository is
observed its existing contents are recorded as baseline and deliberately left
unscanned: onboarding 500 tags does not mean 500 scans.

---

## Setup

**Prerequisites:** Docker + Docker Compose, a reachable Nexus with at least one
Docker repository, and a Nexus account with repository-admin read privileges.
Nothing else — Python, Node, Postgres and Redis all run in the stack with pinned
versions.

```bash
cp .env.example .env     # set JWT_SECRET, BOOTSTRAP_ADMIN_*, Postgres creds
docker compose up --build
```

Open <http://localhost:8080>, sign in with `BOOTSTRAP_ADMIN_USERNAME` /
`BOOTSTRAP_ADMIN_PASSWORD`, and **change that password immediately**. Migrations
and the idempotent seed run automatically from `backend/entrypoint.sh`.

Use `host.docker.internal` (mapped via `extra_hosts`), **not** `localhost`, for
services on the Docker host — inside a container `localhost` is the container.

Then, in the UI:

1. **Settings → Integrations → Nexus** — URL, username, password, *Test*, *Save*.
   Applied live, no restart; the password is encrypted at rest.
2. **Settings → Docker registries** — confirm every repository shows an endpoint
   and `reachable: yes`. Anything under *Not scannable* states its own reason.
3. **Vulnerability Scanning → Refresh vuln DBs** (or *Import offline DBs* on a
   restricted network) — wait until both cards read **ready**. Scans fail until
   they do, and say so.
4. **Vulnerability Scanning → Enable repo** — pick a repository. Existing images
   are baselined, not scanned.
5. **Wire up push events** — see below.

GitHub, GitLab, SonarQube and Telegram are all optional and all connected the
same way, from **Settings → Integrations**. Nothing needs to go in `.env`.

### The one manual step: Nexus push webhooks

Creating a Nexus capability is an administrative action inside Nexus's own
configuration, and Rotsy will not attempt it. Do this once per repository you
want scanned on push.

Get the secret first from **Settings → Scan-on-push webhook → show**. It is
generated on first use; you do not invent it. Then in Nexus:

1. **Administration → System → Capabilities → Create capability**
2. Type **Webhook: Repository**, and pick the Docker repository to watch
3. **Event Types** — tick **component**
4. **URL** — `http://localhost:8000/api/scan/events/nexus`, as reachable *from
   the Nexus host* (use your `BACKEND_PORT` if changed)
5. **Secret Key** — paste the secret, **Save**, confirm it shows *active*

Verify by pushing an image and watching for `Scan queued for <repo>/<image>
(webhook trigger)`:

```bash
docker compose logs -f backend | grep -i "scan queued"
```

A signature mismatch is logged explicitly. If you rotate the secret, update
every Nexus capability — deliveries fail closed until you do.

---

## Configuration

Every value is read from the environment and the app fails fast on a missing
required one. **Full annotated list in [`.env.example`](.env.example)**; the
`/docs` configuration reference explains each in context.

**Required:** `DATABASE_URL` · `JWT_SECRET` and the other `JWT_*` values ·
`SESSION_IDLE_TIMEOUT_SECONDS` · `COOKIE_SECURE` · `FRONTEND_ORIGIN` ·
`BOOTSTRAP_ADMIN_USERNAME`/`_PASSWORD`/`_EMAIL` · `REDIS_URL` ·
`CACHE_TTL_SECONDS` · `ANALYZER_*` · `METRIC_*` · `RETENTION_RUN_AT` ·
`SCANNERS_ENABLED` · `SCANNER_DB_UPDATE_INTERVAL_HOURS` · `BACKEND_HOST` ·
`BACKEND_PORT` · `LOG_LEVEL`

**Commonly useful optional values:**

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `NEXUS_URL`, `NEXUS_USERNAME`, `NEXUS_PASSWORD`, `NEXUS_VERIFY_SSL` | empty / `true` | Bootstrap connection; the dashboard value wins once saved |
| `NEXUS_CONFIG_ENCRYPTION_KEY` | derived from `JWT_SECRET` | Key for stored credentials |
| `SCAN_PUSH_POLL_SECONDS` | `60` | New-image watcher interval; `0` = webhooks only |
| `SCANNER_MAX_CONCURRENCY` | `4` | Concurrent scans; also sizes the cache-replica pool |
| `SCANNER_DB_OFFLINE_MODE`, `SCANNER_OFFLINE_DIR`, `SCANNER_PROXY` | `false`, `/app/offline-db`, empty | Air-gapped / proxied database handling |
| `TELEGRAM_BOT_TOKEN` | empty | Bootstrap default; the dashboard value wins once saved |
| `TELEGRAM_PROXY_URL` | empty | Only if `api.telegram.org` is blocked. Scoped to Telegram traffic alone — it does not affect GitHub, Nexus, SonarQube or registry calls |

There is **no** registry URL or port setting, by design — endpoints are
discovered from Nexus.

---

## Development

Everything runs through Docker; there is no local non-Docker install path.

```bash
docker compose up --build                              # rebuild after a change
docker compose exec backend alembic upgrade head
docker compose --profile test run --rm backend-test    # backend test suite
```

Repo conventions and load-bearing invariants for anyone (or any agent) changing
the code: [`AGENTS.md`](./AGENTS.md).

## Security notes

- No credential is ever baked into an image; everything comes from the
  environment or the encrypted config store.
- Scanner credentials are passed via environment variables, never on the command
  line, so they stay out of the process table.
- The backend runs as a non-root user and mounts no Docker socket.
- Webhook deliveries are HMAC-verified and fail closed on a mismatch.
- Offline archives are extracted with tar's `data` filter, which rejects
  absolute paths, `..` traversal, symlinks and device files.

## License

See [`LICENSE`](./LICENSE). Use, modification, and redistribution are
permitted; the original copyright notice must be retained and may not be
removed, altered, or obscured.
