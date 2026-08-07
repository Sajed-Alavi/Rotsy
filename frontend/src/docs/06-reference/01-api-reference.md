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

## Projects

| Method | Path | Permission |
|---|---|---|
| GET | `/api/projects` | `projects:read` |
| POST | `/api/projects` | `projects:write` |
| GET | `/api/projects/{id}` | `projects:read` |
| DELETE | `/api/projects/{id}` | `projects:write` |
| GET | `/api/projects/{id}/repositories` | `projects:read` |
| GET | `/api/projects/{id}/insights` | `projects:read` |
| GET | `/api/projects/{id}/health` | `projects:read` |
| GET/POST | `/api/projects/{id}/integrations` | `projects:read` / `projects:write` |

## GitHub

| Method | Path | Permission |
|---|---|---|
| GET | `/api/modules/github/status` | `projects:read` |
| GET | `/api/modules/github/manifest-form` | `projects:write` — starts the App Manifest flow |
| GET | `/api/modules/github/manifest-callback` | none (GitHub redirect target) |
| GET | `/api/modules/github/install-url` | `projects:read` |
| GET | `/api/modules/github/callback` | `projects:write` — installation redirect target |
| GET | `/api/modules/github/installations` | `projects:read` |
| POST | `/api/modules/github/installations/{id}/sync` | `projects:write` |
| GET | `/api/modules/github/repositories?unmapped=` | `projects:read` |
| GET | `/api/modules/github/repositories/{id}/branches` | `projects:read` |
| POST | `/api/modules/github/repositories/{id}/map` | `projects:write` |
| POST | `/api/modules/github/repositories/bulk-map` | `projects:write` |
| POST | `/api/modules/github/public-repositories` | `projects:write` — connect one repo by name, no App installation |
| POST | `/api/modules/github/public-repositories/bulk` | `projects:write` |
| POST | `/api/modules/github/webhooks` | HMAC signature |

## GitLab

| Method | Path | Permission |
|---|---|---|
| GET | `/api/modules/gitlab/status` | `projects:read` |
| POST/GET | `/api/modules/gitlab/connections` | `projects:write` / `projects:read` — account-level (one token, many repos) |
| POST | `/api/modules/gitlab/connections/{id}/sync` | `projects:write` |
| POST/GET | `/api/modules/gitlab/repositories` | `projects:write` / `projects:read` — repository-level (one token, one repo) |
| GET | `/api/modules/gitlab/repositories/{id}/branches` | `projects:read` |
| POST | `/api/modules/gitlab/repositories/{id}/reconnect` | `projects:write` — refresh a repository's token after it goes invalid |
| POST | `/api/modules/gitlab/repositories/{id}/register-webhook` | `projects:write` — retry after a failed automatic registration |
| POST | `/api/modules/gitlab/repositories/{id}/map` | `projects:write` |
| POST | `/api/modules/gitlab/repositories/bulk-map` | `projects:write` |
| POST | `/api/modules/gitlab/webhooks/{repo_id}` | signed token per repository |

## SonarQube and Code Quality

| Method | Path | Permission |
|---|---|---|
| GET/PUT | `/api/modules/sonar/config` | `system:execute` |
| POST | `/api/modules/sonar/config/test` | `system:execute` |
| GET | `/api/modules/sonar/status` | `projects:read` |
| POST | `/api/modules/sonar/check-updates` | `system:execute` |
| GET | `/api/modules/sonar/repositories` | `projects:read` — every synced repository, globally |
| POST | `/api/modules/sonar/analyze` | `projects:write` — pick repository + branch, auto-provisions on first use, runs analysis |
| PATCH | `/api/modules/sonar/projects/{id}` | `projects:write` — auto-analyze toggle and branch list |
| GET | `/api/modules/sonar/analysis-runs` | `projects:read` — global, latest-first |
| GET | `/api/modules/sonar/analysis-runs/{id}` | `projects:read` |
| GET | `/api/modules/sonar/analysis-runs/{id}/report.pdf` | `projects:read` |
| GET | `/api/modules/sonar/analysis-runs/{id}/issues` \| `/hotspots` | `projects:read` |
| GET | `/api/modules/sonar/issues` \| `/hotspots` | `projects:read` — global, scoped to each repository's latest successful run |
| GET | `/api/modules/sonar/quality-gates` | `projects:read` |

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
