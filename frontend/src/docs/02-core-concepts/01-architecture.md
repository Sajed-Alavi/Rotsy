# Architecture

Two deployable projects in one compose stack, plus Postgres and Redis.

```
  browser ──HTTP──► nginx (frontend)
                      │  /api/* ──proxy──► FastAPI (backend)
                      └─ React SPA              │  │
                                                │  ├──REST──────► Nexus :8081
                     Postgres ◄─────────────────┤  │              repo config,
                     (state)                    │  │              components, assets
                                                │  │
                     Redis ◄────────────────────┤  └──registry v2─► manifests + layers
                     (cache + job queue)        │      (Trivy / Grype, read-only)
                                                │
                                                └◄── webhook on push ── Nexus
```

## Backend layers

| Layer | Responsibility |
|---|---|
| `routers/` | HTTP only — parse, authorise, delegate |
| `services/` | The actual logic, framework-agnostic |
| `core/` | Infrastructure: Nexus client, cache, job queue, security |
| `models/` | SQLAlchemy tables |

The separation is real, not decorative: services are importable and testable without FastAPI, and routers contain no business rules.

## Long-running work

Anything slow goes through a Redis-backed job queue rather than blocking a request. Jobs report progress over Server-Sent Events, which is how the Database Management page draws a live download bar and the Storage Analyzer streams results as it walks.

Four background loops run in the application lifespan, each with one job:

| Loop | Responsibility |
|---|---|
| `_metric_loop` | Snapshot repository metrics, evaluate alert rules |
| `_retention_scheduler` | Daily retention sweep |
| `_scanner_db_loop` | Keep the vulnerability databases usable |
| `_push_watch_loop` | Notice newly pushed images (fallback trigger) |

**None of them scan on startup.** This is deliberate and load-bearing — see [The ledger and baseline](/docs/the-ledger-and-baseline).

## Scanning modules

Scanning is split by responsibility, with exactly one owner each:

| Module | Responsibility |
|---|---|
| `services/scanning/registry.py` | Discover each Docker repository's registry endpoint |
| `services/scanning/events.py` | Decide *whether* to scan |
| `services/scanning/base.py` | Shared types, subprocess exec, report parsing, static-ref guard |
| `services/scanning/trivy.py` | Trivy adapter and its parser |
| `services/scanning/grype.py` | Grype adapter and its parser |
| `services/scanning/persistence.py` | Runner registry, orchestration, ORM writes |
| `services/scanning/db/` | Database status, update, offline import |
| `routers/scan/` | HTTP endpoints, one module per route group |

Database handling used to be spread across four places that could disagree with each other, and scan triggering across two loops plus an endpoint. Consolidating them is what made failures explainable.

## State

**Postgres** holds everything durable: users, roles, scan targets, the image ledger, reports, findings, metrics history, retention policies, audit entries and API tokens.

**Redis** holds the job queue, job progress and short-lived caches. It is deliberately not a source of truth — a Redis flush loses in-flight jobs and cache, never records.
