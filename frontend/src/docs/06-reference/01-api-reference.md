# API reference

Every endpoint is under `/api`. Interactive OpenAPI documentation is served by the backend at `/api/docs`.

Authenticate with either the session cookie (browser) or a bearer [API token](/docs/tokens-and-webhooks) (automation).

## Scanning

| Method | Path | Permission |
|---|---|---|
| GET | `/api/scan/summary` | `scan:read` |
| GET | `/api/scan/targets` | `scan:read` |
| POST | `/api/scan/targets` | `scan:execute` |
| PATCH | `/api/scan/targets/{id}` | `scan:execute` |
| DELETE | `/api/scan/targets/{id}` | `scan:execute` |
| GET | `/api/scan/images` | `scan:read` |
| POST | `/api/scan/image` | `scan:execute` |
| GET | `/api/scan/reports` | `scan:read` |
| GET | `/api/scan/reports/{id}` | `scan:read` |
| DELETE | `/api/scan/reports/{id}` | `scan:execute` |
| GET | `/api/scan/vulnerabilities` | `scan:read` |
| GET | `/api/scan/registry` | `scan:read` |
| POST | `/api/scan/events/nexus` | HMAC signature |

## Vulnerability databases

| Method | Path | Permission |
|---|---|---|
| GET | `/api/scan/db-status` | `scan:read` |
| GET | `/api/scan/db-offline` | `scan:read` |
| GET | `/api/scan/db-job` | `scan:read` |
| POST | `/api/scan/db-update?force=` | `scan:execute` |
| POST | `/api/scan/db-import` | `scan:execute` |

## Repositories and images

| Method | Path | Permission |
|---|---|---|
| GET | `/api/repositories` | `repositories:read` |
| POST | `/api/repositories` | `repositories:write` |
| DELETE | `/api/repositories/{name}` | `repositories:write` |
| GET | `/api/repositories/{name}/images` | `repositories:read` |
| POST | `/api/repositories/{name}/images/delete` | `repositories:write` |
| GET | `/api/repositories/{name}/assets` | `repositories:read` |
| GET | `/api/repositories/{name}/assets/download` | `repositories:read` |

## Access

| Method | Path | Permission |
|---|---|---|
| POST | `/api/access/tokens` | `access:write` |
| GET | `/api/access/tokens` | `access:read` |
| DELETE | `/api/access/tokens/{id}` | `access:write` |
| GET | `/api/access/webhooks` | `access:read` |
| GET | `/api/access/anonymous` | `access:read` |
| POST | `/api/access/anonymous/grant` | `access:write` |
| POST | `/api/access/anonymous/revoke` | `access:write` |

## Tasks

| Method | Path | Permission |
|---|---|---|
| GET | `/api/tasks` | `tasks:control` |
| POST | `/api/tasks/{id}/run` | `tasks:control` |
| POST | `/api/tasks/{id}/stop` | `tasks:control` |

## Jobs

| Method | Path | Permission |
|---|---|---|
| GET | `/api/jobs` | `jobs:read` |
| GET | `/api/jobs/{id}` | `jobs:read` |
| GET | `/api/jobs/{id}/stream` | `jobs:read` (SSE) |
| POST | `/api/jobs/{id}/cancel` | `jobs:manage` |

## Roles and access rules

All require `roles:manage`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/roles` | Each role carries `access_mode` (`unrestricted` \| `scoped`) |
| POST | `/api/roles` | |
| PATCH | `/api/roles/{id}` | |
| DELETE | `/api/roles/{id}` | System roles are refused |
| GET | `/api/roles/permissions` | The permission catalog |
| GET | `/api/roles/actions` | The access-rule actions: `read`, `scan`, `delete` |
| GET | `/api/roles/{id}/access-rules` | |
| POST | `/api/roles/{id}/access-rules` | `{effect, repo_pattern, image_pattern, actions[], description}` |
| PATCH | `/api/roles/{id}/access-rules/{rule_id}` | |
| DELETE | `/api/roles/{id}/access-rules/{rule_id}` | |
| POST | `/api/roles/{id}/access-rules/test` | `{repo, image}` → allowed actions + the rules that matched |

`GET /api/users/{id}/effective-access?repo=&image=` (requires `users:manage`) resolves every role a user holds and reports which one produced the grant.

Rules on the `admin` role are refused with 400 — see [the permission model](/docs/permission-model).

## Other

Storage (`/api/storage/*`), metrics (`/api/metrics/*`), alerts (`/api/alerts`), retention (`/api/retention/*`), system (`/api/system/*`), users and audit follow the same conventions. Prometheus metrics are exported at `/metrics/export`.

Endpoints returning per-repository or per-image data apply the caller's access rules to the response, including the repository lists themselves. Bulk operations that cannot be applied partially — `DELETE /api/scan/reports`, `POST /api/retention/run-all` — return 403 for a caller whose rules do not cover everything.
