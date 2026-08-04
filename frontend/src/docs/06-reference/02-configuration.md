# Configuration reference

All configuration is environment variables, supplied through `.env` and read by docker-compose. Start from `.env.example`.

## Required

| Variable | Notes |
|---|---|
| `JWT_SECRET` | Session signing key. At least 32 chars; the app refuses placeholders. |
| `NEXUS_CONFIG_ENCRYPTION_KEY` | Encrypts the stored Nexus password. **Must differ from `JWT_SECRET`.** |
| `BOOTSTRAP_ADMIN_USERNAME` | First admin account |
| `BOOTSTRAP_ADMIN_PASSWORD` | At least 12 chars; placeholders refused |
| `DATABASE_URL` | Postgres connection string |
| `POSTGRES_PASSWORD` | Must match `DATABASE_URL` |
| `NEXUS_URL` | Nexus REST base URL |
| `FRONTEND_ORIGIN` | Used for CORS and cookie scoping |
| `COOKIE_SECURE` | Must be `true` behind TLS |

Generate secrets with `openssl rand -hex 32`.

> The app fails to start rather than run with weak or placeholder secrets. That is deliberate: a deployment that boots with the example values is worse than one that does not boot.

## Scanning

| Variable | Default | Notes |
|---|---|---|
| `SCANNERS_ENABLED` | `trivy,grype` | Which scanners to run |
| `SCAN_PUSH_POLL_SECONDS` | `60` | New-image watcher interval; `0` disables it |
| `SCANNER_DB_UPDATE_AT` | — | Daily refresh time, `HH:MM` |
| `SCANNER_DB_UPDATE_INTERVAL_HOURS` | `24` | Used when no time of day is set |
| `SCANNER_DB_OFFLINE_MODE` | `false` | Scheduled run imports instead of downloading |
| `SCANNER_OFFLINE_DIR` | `/app/offline-db` | Where import looks for archives |
| `SCANNER_PROXY` | — | Proxy for database downloads; the Settings value wins |

## Networking

| Variable | Notes |
|---|---|
| `OUTBOUND_ALLOWED_HOSTS` | Comma-separated hosts exempt from the SSRF guard, for legitimate internal webhook and sync targets |

## Backups

| Variable | Default | Notes |
|---|---|---|
| `BACKUP_OUTPUT_DIR` | `/app/backups` | Where archive runs are written — the dedicated backup volume mounts here |
| `BACKUP_MIN_FREE_BYTES` | `536870912` (512MB) | Abort a run rather than fill the volume to zero |
| `BACKUP_SCHEDULER_POLL_SECONDS` | `60` | How often the scheduled-backup loop checks for due schedules; independent schedules can each have their own cadence, so this polls rather than sleeping until one shared time |

## Other

Metric collection interval and retention, retention sweep time, and job concurrency are all in `.env.example` with inline comments.

## Image pinning

Base images are pinned to explicit versions in the Dockerfiles and compose file. Scanner versions in particular should move deliberately — a scanner major release can change its database schema, at which point the database on the persisted cache volume stops validating and every scan fails until it is re-downloaded.
