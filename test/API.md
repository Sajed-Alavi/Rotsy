# API Reference

Base URL: `http://<backend-host>:8000`. Interactive docs at `/docs` (Swagger)
and `/redoc`.

> **v5 additions** are marked with `[v5]`.

## Authentication

- **Mechanism**: JWT access + refresh tokens delivered in **httpOnly cookies**
  (`access_token`, `refresh_token`), path-scoped to `/api`.
- **Login**: `POST /api/auth/login` with `{username, password}` — sets both
  cookies and returns the user profile.
- **Refresh**: `POST /api/auth/refresh` — exchanges the refresh cookie for a
  new access cookie. Refuses if the user has been idle longer than
  `SESSION_IDLE_TIMEOUT_SECONDS`.
- **Logout**: `POST /api/auth/logout` — clears both cookies.
- Every other endpoint requires a valid access cookie.

## Permissions

Each non-auth endpoint lists the required permission (or permissions). System
roles (`admin`, `operator`, `viewer`) bundle permissions; admins can craft
custom roles from the UI. Permission catalog: `app/core/permissions.py`.

---

## Auth — `/api/auth`

| Method | Path | Body | Permission | Notes |
|---|---|---|---|---|
| POST | `/auth/login` | `{username, password}` | — | Sets cookies; returns `MeResponse`. |
| POST | `/auth/logout` | — | — | Clears cookies. |
| POST | `/auth/refresh` | — | — | Rolling refresh; enforces idle timeout. |
| GET | `/auth/me` | — | (any authed) | Returns user + roles + permissions. |

---

## Users — `/api/users` *(permission: `users:manage`)*

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/users` | — | List all users. |
| POST | `/users` | `UserCreate` | Create user (username, email, password, role_ids). |
| PATCH | `/users/{id}` | `UserUpdate` | Partial update (email, password, is_active, role_ids). |
| DELETE | `/users/{id}` | — | Delete user. |

---

## Roles — `/api/roles` *(permission: `roles:manage` for write; `roles:manage` to read)*

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/roles` | — | List all roles + nested permissions. |
| GET | `/roles/permissions` | — | The full permission catalog. |
| POST | `/roles` | `RoleCreate` | Create role with permission_keys. |
| PATCH | `/roles/{id}` | `RoleUpdate` | Update role (name/desc/permissions). System roles can't be renamed. |
| DELETE | `/roles/{id}` | — | Delete role. System roles are protected. |

---

## Settings — `/api/settings` *(permission: `profile:edit`)*

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/settings/profile` | — | Current user profile. |
| PATCH | `/settings/profile` | `{username?, email?}` | Update own profile. |
| POST | `/settings/password` | `{current_password, new_password}` | Change own password. |
| GET | `/settings/scanner-proxy` | — | Get scanner proxy config [v5]. |
| PUT | `/settings/scanner-proxy` | `{proxy}` | Set scanner proxy [v5]. |

---

## Health — `/api/health` *(permission: any authed)*

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Returns `{status, version, nexus_reachable, redis_reachable}`. |

---

## Repositories — `/api/repositories`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/repositories?format=&refresh=` | `repositories:read` | List repos; optional format filter; `refresh=true` bypasses cache. |
| GET | `/repositories/{name}/assets?continuationToken=` | `repositories:read` | Paginated asset list (path, size, type, uploader, modified...). |
| GET | `/repositories/{name}/assets/download?path=` | `repositories:read` | **Authenticated proxy download** — streams bytes from Nexus to the browser; sets `Content-Disposition`. |
| POST | `/repositories` | `repositories:write` | Create hosted/proxy/group repo. Body `{name, format, type, blob_store?, write_policy?, remote_url?, members?, docker_http_port?, ...}`. |
| DELETE | `/repositories/{name}` | `repositories:write` | Delete a repository. |
| POST | `/repositories/{name}/invalidate-cache` | `repositories:write` | *(stub 501)* invalidate proxy cache. |
| POST | `/repositories/{name}/rebuild-index` | `repositories:write` | *(stub 501)* rebuild index. |

## Blobstores — `/api/blobstores`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/blobstores` | `blobstores:read` | List blobstores with type/state/blob count/size (+ quota where available). |
| POST | `/blobstores/file` | `blobstores:write` | Create File blobstore. Body `{name, path, soft_quota?}`. |
| POST | `/blobstores/s3` | `blobstores:write` | Create S3 blobstore. Body `{name, bucket, region?, prefix?, endpoint?, access_key_id?, secret_access_key?, expiration?}`. |
| DELETE | `/blobstores/{name}` | `blobstores:write` | Delete a blobstore (409 if still used by a repo). |

---

## Storage Analyzer — `/api/storage`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/storage/repos?refresh=` | `storage:read` | All repositories (any format); `refresh=true` bypasses cache. |
| GET | `/storage/{repo}/result` | `storage:read` | Last cached analysis result (404 if none). |
| GET | `/storage/{repo}/analyze?use_cache=` | `storage:analyze` | Run analysis (non-streaming), cache + return. |
| GET | `/storage/{repo}/analyze/stream?use_cache=` | `storage:analyze` | **SSE** stream of progress events (`phase`/`progress`/`cache`/`result`/`error`). |

Result schema (uniform across docker + generic modes):
```json
{
  "repo": "test", "format": "raw", "mode": "generic",
  "scanned_at": "ISO-8601",
  "stats": {"total_bytes": ..., "active_bytes": ..., "wasted_bytes": ..., "item_count": ..., "asset_count": ...},
  "items": [{"name": ..., "total_bytes": ..., "version_count": ..., "versions": [{"version": ..., "size_bytes": ...}]}]
}
```

---

## Retention — `/api/retention`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/retention/policies` | `retention:read` | List policies. |
| POST | `/retention/policies` | `retention:execute` | Create (`name`, `repo`, `keep_last_n?`, `delete_older_than_days?`). |
| PATCH | `/retention/policies/{id}` | `retention:execute` | Update. |
| DELETE | `/retention/policies/{id}` | `retention:execute` | Delete. |
| POST | `/retention/policies/{id}/preview` | `retention:read` | **Dry-run**: returns candidate deletion list. |
| POST | `/retention/policies/{id}/run?dry_run=` | `retention:execute` | Enqueue background job (deletes + compaction). |
| POST | `/retention/run-all?dry_run=` | `retention:execute` | Enqueue a sweep of every enabled policy. |

The **daily scheduler** (configured by `RETENTION_RUN_AT`) enqueues
`run-all` automatically.

---

## Metrics — `/api/metrics` *(permission: `metrics:read`)*

| Method | Path | Notes |
|---|---|---|
| GET | `/metrics/overview` | Latest per-repo snapshot. |
| GET | `/metrics/{repo}/timeseries?hours=24` | Historical samples for charts (1h–720h). |
| GET | `/metrics/realtime` | Live reachability + Nexus version. |
| GET | `/metrics/health` | Nexus probes categorized `critical`/`security`/`info` [v5]. |
| GET | `/metrics/blobstores` | Disk usage per blobstore [v5]. |
| GET | `/metrics/system` | Version, edition, security warnings [v5]. |

---

## Alerts — `/api/alerts`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/alerts` | `alerts:read` | List rules. |
| POST | `/alerts` | `alerts:write` | Create rule: `{name, metric, condition, threshold, repo_filter?, webhook_url, enabled?}`. |
| PATCH | `/alerts/{id}` | `alerts:write` | Update. |
| DELETE | `/alerts/{id}` | `alerts:write` | Delete. |

Supported metrics: `storage.total`, `storage.asset_count`. Conditions: `>`, `<`,
`==`. Rules are evaluated after each metric collection; firing posts to the
webhook URL with the envelope `{source, event, timestamp, data}`.

---

## Jobs — `/api/jobs`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/jobs?limit=` | `jobs:read` | List recent jobs. |
| GET | `/jobs/{id}` | `jobs:read` | Job status + result. |
| POST | `/jobs/{id}/cancel` | `jobs:manage` | Cancel a running job [v5]. |
| GET | `/jobs/{id}/stream` | `jobs:read` | **SSE** live progress. |
| POST | `/jobs/collect-metrics` | `jobs:manage` | Enqueue metric snapshot. |
| POST | `/jobs/analyze-repo` | `jobs:manage` | Body `{repo}` — enqueue deep analysis. |
| POST | `/jobs/analyze-all` | `jobs:manage` | Fan-out: one job per repo. |

---

## Scan — `/api/scan`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/scan/targets` | `scan:read` | List per-repo scan targets. |
| POST | `/scan/targets` | `scan:execute` | `{repo, enabled?, auto_scan?, scanners?}`. |
| PATCH | `/scan/targets/{id}` | `scan:execute` | Update target. |
| DELETE | `/scan/targets/{id}` | `scan:execute` | Delete target. |
| POST | `/scan/image` | `scan:execute` | Body `{repo, image, scanners?}` — enqueue scan job. |
| POST | `/scan/db-update` | `scan:execute` | Enqueue vuln-DB refresh job (downloads). |
| POST | `/scan/db-import` | `scan:execute` | Enqueue OFFLINE vuln-DB import from `./offline-db` (air-gapped, no network). |
| GET | `/scan/db-status` | `scan:read` | Scanner DB info: version, date, size. |
| GET | `/scan/db-offline` | `scan:read` | List archives detected in the offline import dir. |
| GET | `/scan/reports?repo=&limit=` | `scan:read` | Recent reports (severity counts). |
| DELETE | `/scan/reports/{id}` | `scan:execute` | Delete one scan report [v5]. |
| DELETE | `/scan/reports` | `scan:execute` | Delete ALL scan reports [v5]. |
| GET | `/scan/reports/{id}/vulnerabilities?severity=` | `scan:read` | Detailed findings per report [v5]. |
| GET | `/scan/vulnerabilities?repo=&severity=&limit=` | `scan:read` | Findings list (CRITICAL first). |
| GET | `/scan/summary` | `scan:read` | Aggregate counts for the dashboard. |

Auto-scan: when a target has `enabled=true` and `auto_scan=true`, the
`_auto_scan_loop` polls the repo every 60 s for new Docker components and
enqueues a `scan_image` job for each unseen image.

---

## System — `/api/system`

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/system/status` | `system:read` | Basic OK. |
| GET | `/system/backup/tasks` | `system:read` | List Nexus backup tasks. |
| POST | `/system/backup` | `system:execute` | Enqueue backup-trigger job. |
| GET | `/system/backup/db` | `system:execute` | Download Nexus DB snapshot (streamed). |
| POST | `/system/sync` | `system:execute` | Body `{source_repo, target_base_url, target_username, target_password, target_repo, verify_ssl?}` — enqueue Nexus→Nexus sync. |
| POST | `/system/scripts/{name}` | `system:execute` | *(stub 501)* host script trigger. |

---

## Scaffolded (return 501)

| Path | Feature |
|---|---|
| `/api/blobstores` | Blobstore management (read + write). |
| `/api/analytics/bandwidth`, `/top-downloads`, `/cache-hit-rate`, `/tasks` | Analytics. |
| `/api/access/tokens`, `/access/webhooks` | CI/CD tokens + webhooks. |

---

## SSE event protocol

Both `/storage/{repo}/analyze/stream` and `/jobs/{id}/stream` emit frames:

```
event: <name>
data: <json>

```

`<name>` is one of: `phase`, `progress`, `cache`, `result`, `error`. The
`<json>` payload varies by event type — see the OpenAPI schema at `/docs` for
the per-endpoint details.
