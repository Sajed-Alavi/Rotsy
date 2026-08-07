# Rotsy

A management dashboard and API around **Sonatype Nexus Repository Manager**, with
**static container-image vulnerability scanning** (Trivy + Grype) as its
centrepiece.

Two deployable projects, one compose stack:

| Path        | What it is                                                  |
| ----------- | ---------------------------------------------------------   |
| `backend/`  | FastAPI service, Alembic migrations, Trivy + Grype binaries |
| `frontend/` | React + Vite + Tailwind single-page app served by nginx     |

Three design rules run through the whole system. They are not aspirations; each
is enforced in code and pointed at below.

1. **Static analysis only.** No container is ever started, run, or spun up.
   Images are read over the Docker Registry v2 API and analysed as data.
2. **Zero client-side registry configuration.** Docker connector hosts and ports
   are discovered from Nexus. Nothing to type, nothing to keep in sync.
3. **Event-driven scanning.** An image is scanned when it is pushed, or when an
   operator asks. Never on startup, never on a schedule, never twice by accident.

---

> **Full documentation ships inside the app**, at **/docs** in the sidebar — 36
> pages ordered as a learning path, from first login through core concepts,
> guides, database management, administration, an API and configuration
> reference, and end-to-end workflows (CI/CD gating, air-gapped install,
> responding to a new CVE, scaling to many repositories). This README stays the
> short version for people who have not started it yet.

## Contents

- [Architecture](#architecture)
- [Browsing images](#browsing-images)
- [Deleting images](#deleting-images)
- [Access control](#access-control)
- [How scanning is triggered](#how-scanning-is-triggered)
- [Zero-configuration registry discovery](#zero-configuration-registry-discovery)
- [Static-only guarantee](#static-only-guarantee)
- [Vulnerability databases](#vulnerability-databases)
- [Setup](#setup)
- [Manual steps you must perform](#manual-steps-you-must-perform)
- [Operations](#operations)
- [Troubleshooting scan failures](#troubleshooting-scan-failures)
- [Configuration reference](#configuration-reference)
- [Development](#development)
- [Security notes](#security-notes)
- [License](#license)

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

**Backend layers.** `routers/` handle HTTP only; `services/` hold the logic and
are framework-agnostic; `core/` holds infrastructure (Nexus client, cache, job
queue, security); `models/` are the SQLAlchemy tables.

Long-running work goes through a Redis-backed job queue (`core/jobs.py`) with
live progress over SSE, so a scan or a database download never blocks a request.
Four background loops run in the app lifespan, each with a single job:

| Loop                   | Responsibility                                           |
| ---------------------- | -------------------------------------------------------- |
| `_metric_loop`         | Snapshot repository metrics, evaluate alert rules        |
| `_retention_scheduler` | Daily retention sweep at `RETENTION_RUN_AT`              |
| `_scanner_db_loop`     | Keep the vulnerability databases usable                  |
| `_push_watch_loop`     | Notice newly pushed images (fallback trigger, see below) |

**None of them scan on startup.**

### Scanning modules

| Module                        | Responsibility                                                  |
| ----------------------------- | --------------------------------------------------------------- |
| `services/scanning/registry.py` | Discover each Docker repository's registry endpoint           |
| `services/scanning/events.py` | Decide *whether* to scan (webhook, watcher, manual)             |
| `services/scanning/base.py`   | Shared types, subprocess exec, report parsing, static-ref guard |
| `services/scanning/trivy.py`  | Trivy adapter + its report parser                               |
| `services/scanning/grype.py`  | Grype adapter + its report parser                               |
| `services/scanning/persistence.py` | Runner registry, orchestration, ORM writes                 |
| `services/scanning/db/`       | Vulnerability database status, update, offline import           |
| `routers/scan/`               | HTTP endpoints, one module per route group                      |
| `schemas/scan.py`             | Request/response models for those endpoints                     |
| `models/scans.py`             | Targets, image ledger, reports, findings                        |
| `services/images.py`          | Image/tag inventory, deletion, blob compaction                  |

Each of those has exactly one owner. That is a change: database handling used to
be spread across four places that could disagree, and scan triggering across two
loops plus an endpoint.

---

## Browsing images

**Browse** has two views, selectable per repository.

**Images** (the default) shows what a repository actually holds: each image as a
folder, expanding to its tags, with the push time, size and a delete action per
tag. This is the view you want for a Docker repository — its raw asset listing is
mostly layer blobs (`v2/myapp/blobs/sha256:6f2a…`), which says nothing about
which images exist. For non-Docker formats the same view lists components and
versions.

**Files** shows the raw assets as an expandable **directory tree** built from
their paths, rather than one flat list, with a download button per file.
Downloads are proxied through the backend, so the browser never handles Nexus
credentials.

Timestamps come from the asset metadata Nexus already returns. For a Docker tag
the "created" time is when its **manifest** was written — that is the push time.
Layer blobs are shared between tags and are often much older, so dating a tag by
its layers would be misleading.

---

## Deleting images

Delete from **Browse → Images**: `delete` on a tag row, `delete all` on an image
row, or tick several tags and use **Delete N selected**.

Every delete reports its outcome **per tag** — `deleted` and `failed`, with a
reason on each failure. This matters more than it sounds: deletion used to be
reachable only through a retention policy, which reduced every failure to a
boolean, so "4 images, none deleted" gave no clue whether Nexus had rejected the
request, the ids were stale, or the policy had simply matched nothing. Common
reasons now surfaced verbatim:

| Reason | Meaning                                                                                                                    |
| ------ | -------------------------------------------------------------------------------------------------------------------------- |
| `component not found` | Stale id — the list was loaded before something else changed it. Refresh.                                   |
| `Nexus refused the delete (HTTP 403)` | The service account lacks delete privileges on that repository.                             |
| `this repository does not allow deletion` | The repository's write policy is *Disable redeploy* or *Read-only*. Change it in Nexus. |

**Space is not freed by the delete itself.** Nexus removes the tag immediately
but leaves the blobs on disk until its **Compact blob store** task runs. Rotsy
triggers that task after a successful delete — and if no such task exists, says
so instead of leaving you wondering why disk usage did not move. Nexus does not
create one by default; add it under **Administration → System → Tasks**.

### Retention policies

For bulk cleanup, **Retention** applies rules on a schedule. `keep last N`
counts versions **within each image**, not across the repository — counted
repository-wide, `keep_last_n=3` deletes whole images merely because other images
were pushed more recently, which is how a "keep 3" rule ends up removing an
image's only tag. Components whose age Nexus does not report are never deleted by
an age rule; the run reports them as skipped rather than guessing.

---

## Access control

Two independent axes, modelled on JFrog Artifactory's permission targets.

**Permissions** are `resource:action` keys on a role (`scan:execute`,
`repositories:write`, …) and answer *what* a user may do. **Access rules** are
also attached to roles and answer *where* those actions reach:

```
<allow|deny>  <read,scan,delete>  on  <repo pattern> / <image pattern>
```

Patterns are Ant-style — `*` matches any characters except `/`, `**` crosses
it, `?` is one character — and cover both dimensions, so one rule can span
every repository matching `prod-*` without being rewritten each time a
repository is added. Within a role a deny beats an allow, which is how
"everything except `*-secrets`" is expressed. The three actions are
independent: a team can be given `read` and `scan` on its own images without
`delete`.

A role with no rule matching a repository falls back to its access mode:
`unrestricted` (that repository stays open — the default, and what the seeded
admin/operator/viewer roles carry) or `scoped` (deny by default). Effective
access is the union across a user's roles, so scoping one role only bites once
the baseline roles those users also hold are set to `scoped`.

Rules gate the repository lists themselves, not only their contents, and
repository-wide operations (retention policies, enabling scanning, bulk report
deletion) require access to the whole repository rather than part of it.

Full reference, wildcard tables and worked examples are in the in-app docs at
**/docs/permission-model** and **/docs/access-rules-cookbook**.

## How scanning is triggered

An image is scanned for **exactly two reasons**.

### a) It was pushed

**Primary path — Nexus webhook.** Nexus posts to
`POST /api/scan/events/nexus` the moment a component is created or updated. The
request is authenticated by the HMAC signature in `X-Nexus-Webhook-Signature`,
not by a user session. Reaction time: seconds. Requires the one-time Nexus
capability described in [Manual steps](#manual-steps-you-must-perform).

**Fallback path — new-image watcher.** For deployments without webhooks,
`_push_watch_loop` lists each enabled repository's components every
`SCAN_PUSH_POLL_SECONDS` and queues a scan **only** for images the ledger has
never seen. This compares metadata; it does not scan anything already known. Set
`SCAN_PUSH_POLL_SECONDS=0` to turn it off and rely purely on webhooks.

### b) An operator asked

`POST /api/scan/image`, behind the **Scan** button on each row of the Images
table. This is the only path that may re-scan an image that has already been
scanned or baselined.

### The baseline: why history is never scanned

The first time a repository is observed, everything already in it is written to
the ledger as `baseline` — history, deliberately unscanned — and
`scan_targets.baseline_at` is stamped. Enabling scanning on a repository holding
a thousand images therefore triggers **zero** scans, and scaling from 7 projects
to 12 adds five baselines, not five repositories' worth of work. Baselined images
can still be scanned individually with the Scan button.

### The ledger

`scan_image_ledger` is the durable record of every image the system knows about:

| State      | Meaning                                                                |
| ---------- | ---------------------------------------------------------------------- |
| `baseline` | Present before scanning was enabled. Never auto-scanned.               |
| `queued`   | A scan job is in flight.                                               |
| `scanned`  | Scanned successfully. Will not be re-scanned implicitly.               |
| `failed`   | The last attempt failed; the report carries the reason.                |

Because it lives in Postgres, a restart, a cache flush or a redeploy cannot
resurrect work. Deduplication previously lived in Redis with a 24-hour TTL, which
silently re-scanned every image in every enabled repository once a day and
re-scanned everything whenever Redis restarted. A tag re-pushed with new content
*is* a new push: the manifest digest changes, and the ledger compares digests.

There is intentionally **no "scan all" endpoint**. The one that existed
(`POST /scan/scan-all`) fanned a job out per image per repository.

---

## Zero-configuration registry discovery

Nexus does not serve the Docker Registry v2 API on its main port. Every Docker
repository gets its own connector port, part of that repository's configuration:

```
nexus REST API           http://nexus-host:8081
docker repo "team-a"     http://nexus-host:15987/v2/...
docker repo "team-b"     http://nexus-host:15988/v2/...
```

Nexus is the authority on those ports, so `services/scanning/registry.py` asks it:

1. `GET /service/rest/v1/repositorySettings` — one call, full configuration for
   every repository including the `docker` connector block.
2. `GET /service/rest/v1/repositories/docker/{type}/{name}` — per-repository
   fallback when (1) is unavailable.

The **host** is derived from the live Nexus base URL (connectors listen on the
same interface). The **scheme** comes from the connector the repository declares:
an `httpsPort` means TLS, an `httpPort` means plaintext. Results are cached for
120 seconds, and a scan targeting an unknown repository forces one immediate
re-probe, so a repository created seconds ago is scannable at once.

There is **no** `DOCKER_REGISTRY_URL` env var and no registry field in the UI. The
Settings page shows a read-only table of what was discovered, with a reachability
check per endpoint, plus an explicit list of any Docker repository that could not
be resolved and why.

> **Requirement:** the Nexus service account needs repository-admin *read*
> privileges for discovery to see connector ports. Without it, discovery reports
> each repository under `unresolved` with that exact reason. `nx-admin` covers it.

---

## Static-only guarantee

Nothing in this system starts a container. Four independent enforcement points:

| Enforcement                                          | Where                            |
| ---------------------------------------------------- | -------------------------------- |
| Trivy runs with `--image-src remote`                 | `services/scanning/`           |
| Grype gets an explicit `registry:` reference and `GRYPE_DEFAULT_IMAGE_PULL_SOURCE=registry` | `services/scanning/` |
| `_assert_static_ref()` rejects `docker:`, `podman:`, `containerd:`, `dir:`, … | `services/scanning/` |
| No Docker socket is mounted, and the image has no Docker client | `docker-compose.yml`, `backend/Dockerfile` |

The Grype default matters: left alone, Grype tries the local docker, podman and
containerd daemons *before* the registry. The explicit `registry:` scheme is what
keeps it off them.

---

## Vulnerability databases

Scans need a local database. `services/scanning/db/` is its only owner, and
scans never update it themselves — both tools would otherwise try to refresh
mid-scan and fail the scan when the download fails.

| Path                                 | When                                                                                                     |
| ---------------------------------    | -------------------------------------------------------------------------------------------------------- |
| Online (`POST /api/scan/db-update`)  | Normal networks. Trivy via `oras` (falling back to Trivy's own downloader), Grype via `grype db update`. |
| Offline (`POST /api/scan/db-import`) | Restricted or air-gapped networks. Extracts pre-downloaded archives; nothing touches the network.        |

**Startup behaviour:** the app fetches a database only if one is *missing*. A
present database is left to `SCANNER_DB_UPDATE_AT` /
`SCANNER_DB_UPDATE_INTERVAL_HOURS`. An update that is already current is skipped
(both projects publish at most one build per day). Databases live on the
`scanner-cache` named volume and survive `docker compose up --build`.

Air-gapped flow:

```bash
# on a machine with internet
./scripts/scanner/fetch-offline-db.sh
# copy the archives to the restricted host's ./offline-db/, then import:
#   dashboard → Vulnerability Scanning → Database Management → "Import offline DBs"
```

Recognised filenames in `./offline-db/`:

| Scanner    | Filename                                                      |
| ---------- | ------------------------------------------------------------- |
| Trivy      | `db.tar.gz` or `trivy-db.tar.gz`                              |
| Trivy Java | `javadb.tar.gz` or `trivy-java-db.tar.gz` (optional)          |
| Grype      | `grype-db.tar.{gz,zst}` or `vulnerability-*.tar.{gz,zst}`     |

Set `SCANNER_DB_OFFLINE_MODE=true` to make the *scheduled* refresh import rather
than download. The on-demand import always works regardless.

---

## Setup

### Prerequisites

- Docker + Docker Compose
- A reachable Nexus Repository Manager with at least one **Docker** repository
- A Nexus account with repository-admin read privileges (for discovery)

Nothing else. Python, Node, PostgreSQL and Redis all run inside the compose
stack with their versions pinned in the Dockerfiles
(`python:3.13.14-slim-bookworm`, `node:24.16.0-alpine`) — there is no local
install step for any of them.

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env`. The minimum is `JWT_SECRET`, the `BOOTSTRAP_ADMIN_*` values and the
Postgres credentials. `NEXUS_*` are optional bootstrap defaults — leave them
blank and configure the connection from the UI on first login.

Use `host.docker.internal` (mapped via `extra_hosts`), **not** `localhost`, when
Nexus runs on the Docker host: inside a container `localhost` is the container.

### 2. Start

```bash
docker compose up --build
```

Then open <http://localhost:8080> and sign in with `BOOTSTRAP_ADMIN_USERNAME` /
`BOOTSTRAP_ADMIN_PASSWORD`. Change that password immediately.

Migrations and the idempotent seed (permissions, system roles, bootstrap admin)
run automatically from `backend/entrypoint.sh`.

### 3. Point it at Nexus

**Settings → Nexus connection.** Enter the URL, username and password, click
**Test**, then **Save**. Applied live; no restart. The password is encrypted at
rest with a key derived from `NEXUS_CONFIG_ENCRYPTION_KEY` (or `JWT_SECRET`).

`Verify SSL` applies to the **REST** connection only. How scanners reach a Docker
connector is derived from that connector, not from this checkbox.

### 4. Confirm discovery

**Settings → Docker registries.** Every Docker repository should appear with an
endpoint and `reachable: yes`. Anything under *Not scannable* states its own
reason. Resolve those before enabling scanning.

### 5. Install the vulnerability databases

**Vulnerability Scanning → Refresh vuln DBs** (or *Import offline DBs* on a
restricted network). Wait until both cards show **ready**. Scans fail until they
do, and say so.

### 6. Enable scanning per repository

**Vulnerability Scanning → Enable repo.** Pick the repository, optionally
override the scanners, leave *scan images pushed from now on* ticked. Existing
images are baselined, not scanned.

### 7. Wire up push events

See below — this is the one step that must be done inside Nexus.

---

## Manual steps you must perform

Creating a Nexus capability is an administrative action in Nexus's own
configuration. This system will not attempt it. Do this once per repository you
want scanned on push.

**Get the secret first:** Settings → *Scan-on-push webhook* → **show**
(or `GET /api/scan/webhook`). It is generated on first use; you do not invent it.

1. In Nexus, open **Administration → System → Capabilities**.
2. Click **Create capability**.
3. Select type **Webhook: Repository**.
4. **Repository** — choose the Docker repository to watch. Repeat this whole
   procedure for each repository.
5. **Event Types** — tick **component**.
6. **URL** — the backend's webhook endpoint, as reachable *from the Nexus host*:
   ```
   http://localhost:8000/api/scan/events/nexus
   ```
   Use `BACKEND_PORT` from `.env` if you changed it. This works because the
   backend publishes that port and Nexus runs on the host network; if your Nexus
   is elsewhere, use an address that resolves from Nexus to this backend.
7. **Secret Key** — paste the secret from Settings.
8. **Save**, and confirm the capability shows as *active*.

**Verify it, without a test scan:** push an image to that repository and watch
the backend log:

```bash
docker compose logs -f backend | grep -i "scan queued"
```

You should see `Scan queued for <repo>/<image> (webhook trigger)` within seconds.
The image then appears in the Images table. If nothing arrives, check
`docker compose logs backend | grep -i webhook` — a signature mismatch is logged
explicitly.

If you rotate the secret (Settings → **Rotate secret**), update every Nexus
capability to match; deliveries fail closed until you do.

---

## Operations

### Scanning an image on demand

**Vulnerability Scanning → Images → Scan.** Queues a job; live progress under
Background Jobs. The button reads *rescan* once an image has been scanned before.

### Reading a report

Click any row in **Recent reports**. Successful reports list findings ordered by
severity then CVSS. Failed reports show the reason plus the scanner's command
line (password redacted), exit code and output tail.

**Download PDF** on a report's detail view exports it as a standalone document:
repository, image, tag, scan date, severity breakdown, the full CVE list
(installed/fixed version, CVSS), and short recommendations derived from which
Critical/High findings have a fix available — everything needed to hand a
report to someone who does not have access to the dashboard.

### Clearing reports

`DELETE /api/scan/reports` (dashboard: *clear all*) removes reports and findings.
Pass `?reset_ledger=true` to also forget which images were scanned. That does
**not** trigger any scan: affected images return to `baseline` and are only
scanned on their next push or on request.

### Keeping databases current

Scheduled by `_scanner_db_loop`. Force one now with
`POST /api/scan/db-update?force=true`. `GET /api/scan/db-status` reports build
date, size, readiness and staleness. A Grype database older than five days is
reported stale; scans still run (Grype's own age check is disabled — a slightly
stale database beats no scan) but it wants attention.

### Adding repositories at scale

Nothing to do. Create the Docker repository in Nexus with a connector port,
enable it as a scan target, add its webhook capability. Discovery picks the port
up within its 120-second cache window, or immediately on the first scan.

---

## Troubleshooting scan failures

A failed report always carries a reason. Start with the report detail
(`GET /api/scan/reports/{id}` or clicking the row), then match it below.

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `no <scanner> vulnerability database on disk` | Preflight found no database | Run a DB update or offline import; wait for both cards to read **ready** |
| Repository listed under `unresolved` in `GET /api/scan/registry` | No connector port on the repository, or the service account cannot read repository config | Set an HTTP/HTTPS connector port in Nexus; grant repository-admin read |
| `probe.reachable: false` on a discovered endpoint | Connector port not reachable from the backend container | Confirm Nexus is listening on that port; check `extra_hosts` maps `host.docker.internal` |
| `unauthorized` / `denied` from a scanner | Docker connector rejected the Nexus credentials | Confirm the account can pull from that repository; if the repository has *Force basic authentication* off, it still requires a valid account for private content |
| `no such host` | Registry host does not resolve inside the container | Use `host.docker.internal` (not `localhost`/`127.0.0.1`) in the Nexus URL — the registry host is derived from it |
| `x509` / certificate errors | HTTPS connector with a certificate the container does not trust | Prefer a plaintext connector on a trusted network, or install the CA in the image |
| Both scanners fail on one image, others fine | Manifest list without a `linux/amd64` entry | Expected: the scanners default to `linux/amd64` |

### What was actually wrong before

The `FAILED` reports this refactor set out to fix had five root causes, all now
addressed in code:

1. **The scanners were pointed at an endpoint that does not exist.** The image
   reference was built from a hand-configured registry URL and, when that was
   blank, fell back to `{nexus-host}:8081/{repo}/{image}` — Nexus does not serve
   the v2 API there, so every scan 404'd. *Fixed by discovery.*
2. **TLS handling was taken from the wrong setting.** Plaintext-vs-TLS was driven
   by `NEXUS_VERIFY_SSL`, which describes the REST connection, so a plaintext
   connector behind an HTTPS Nexus was probed over TLS. *Fixed by deriving the
   scheme per connector.*
3. **The scanners tried to update their databases mid-scan.** Both do by default,
   and Grype outright refuses a database older than five days. On a restricted
   network that download fails and takes the scan with it. *Fixed with
   `--skip-db-update` / `GRYPE_DB_AUTO_UPDATE=false`, plus a preflight that
   reports a missing database as such.*
4. **Grype was resolving images through container runtimes.** With no scheme it
   tries docker, podman and containerd first — none of which exist in the
   container, and none of which it should touch. *Fixed with `registry:`.*
5. **Failures were undiagnosable.** The reason was truncated to 500 characters
   and buried in a JSON blob. *Fixed: the reason, command, exit code and output
   tail are persisted and shown.*

One more, unrelated to failures but to coverage: component listing was
unpaginated, so only the first page of any repository was ever considered. It now
pages properly.

---

## Configuration reference

Every value is read from the environment; the app fails fast on a missing
required one. Full annotated list in [`.env.example`](.env.example).

### Required

| Variable | Purpose |
| -------- | ------- |
| `DATABASE_URL` | Postgres DSN (`postgresql+asyncpg://…`) |
| `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS` | Session tokens |
| `SESSION_IDLE_TIMEOUT_SECONDS` | Idle logout |
| `COOKIE_SECURE` | Must be `true` behind TLS |
| `FRONTEND_ORIGIN` | CORS + cookie scoping |
| `BOOTSTRAP_ADMIN_USERNAME`/`_PASSWORD`/`_EMAIL` | First admin |
| `REDIS_URL`, `CACHE_TTL_SECONDS` | Cache + job queue |
| `ANALYZER_MAX_CONCURRENCY`, `ANALYZER_REQUEST_TIMEOUT` | Storage analyzer |
| `METRIC_COLLECTION_INTERVAL_SECONDS`, `METRIC_RETENTION_DAYS` | Monitoring |
| `RETENTION_RUN_AT` | Daily retention sweep (`HH:MM`) |
| `SCANNERS_ENABLED` | `trivy`, `grype`, in run order |
| `SCANNER_DB_UPDATE_INTERVAL_HOURS` | Database refresh interval |
| `BACKEND_HOST`, `BACKEND_PORT`, `LOG_LEVEL` | Server runtime |

### Optional

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `NEXUS_URL`, `NEXUS_USERNAME`, `NEXUS_PASSWORD`, `NEXUS_VERIFY_SSL` | empty / `true` | Bootstrap connection; the dashboard value wins once saved |
| `NEXUS_CONFIG_ENCRYPTION_KEY` | derived from `JWT_SECRET` | Key for the stored Nexus password |
| `NEXUS_WEBHOOK_SECRET` | generated | Seeds the webhook secret on first use |
| `SCAN_PUSH_POLL_SECONDS` | `60` | New-image watcher interval; `0` = webhooks only |
| `SCANNER_DB_UPDATE_AT` | empty | Fixed daily time (`HH:MM`) instead of the interval |
| `SCANNER_DB_OFFLINE_MODE` | `false` | Scheduled refresh imports instead of downloading |
| `SCANNER_OFFLINE_DIR` | `/app/offline-db` | Offline archive directory in the container |
| `SCANNER_PROXY` | empty | Proxy for database downloads (also settable in the UI) |

There is **no** registry URL or port setting, by design — see
[discovery](#zero-configuration-registry-discovery).

---

## Development

Everything runs through Docker — there is no local (non-Docker) install path
for the backend or frontend.

```bash
# rebuild and restart after a code change
docker compose up --build

# one-off commands inside the running containers
docker compose exec backend alembic upgrade head
docker compose exec backend pytest
docker compose exec frontend npm run build
```

## Security notes

- No credential is ever baked into an image; everything comes from the
  environment or the encrypted config store.
- Scanner credentials are passed via environment variables, never on the command
  line, so they stay out of the process table.
- The backend runs as a non-root user and mounts no Docker socket.
- Webhook deliveries are HMAC-verified and fail closed on a mismatch.
- Offline archives are extracted with tar's `data` filter, which rejects absolute
  paths, `..` traversal, symlinks and device files.

## License

See [`LICENSE`](./LICENSE). Use, modification, and redistribution are
permitted; the original copyright notice must be retained and may not be
removed, altered, or obscured.
