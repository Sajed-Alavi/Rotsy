# Permissions matrix

Permission keys are `resource:action`. Effective permissions are the union across a user's roles.

| Key | Grants |
|---|---|
| `storage:read` | View repository storage data |
| `storage:analyze` | Run a storage analysis |
| `retention:read` | View retention policies |
| `retention:execute` | Create, edit, delete and run policies |
| `scan:read` | View scan targets, images, reports and findings |
| `scan:execute` | Enable repositories, trigger scans, manage databases |
| `repositories:read` | View repositories, images and assets |
| `repositories:write` | Create and delete repositories, delete images |
| `blobstores:read` | View blobstores |
| `blobstores:write` | Create and delete blobstores |
| `access:read` | View API tokens, webhooks and anonymous access |
| `access:write` | Issue and revoke tokens, manage anonymous access |
| `tasks:control` | View, run and stop Nexus scheduled tasks |
| `metrics:read` | View metrics |
| `metrics:collect` | Trigger metric collection |
| `jobs:read` | View background jobs |
| `jobs:manage` | Enqueue and cancel jobs, run backups and sync |
| `alerts:read` | View alert rules |
| `alerts:write` | Create, edit and delete alert rules |
| `system:read` | View system status and backup runs |
| `system:execute` | Change the Nexus connection and scanner proxy |
| `users:manage` | Manage users; see all API tokens |
| `roles:manage` | Manage roles and permissions; read the audit log |
| `profile:edit` | Edit your own profile and password |

## Seeded roles

| Role | Permissions |
|---|---|
| `admin` | All of the above |
| `operator` | storage, retention, repositories:read, scan:*, metrics, jobs, tasks:control, profile:edit |
| `viewer` | Every `:read` permission, plus `profile:edit` |

## Notes

`system:execute` is effectively administrative — it can repoint the app at a different Nexus, which changes what every other permission operates on.

`roles:manage` includes reading the audit log, since role administration and reviewing the trail are the same job.

Access rules are orthogonal to all of this. A permission key says *what* a user may do; an access rule says *which repositories and images* it reaches. Both are required.

## Access-rule actions

Rules grant these independently of the keys above.

| Action | Grants |
|---|---|
| `read` | See an image in listings, browse and download its assets, read its scan reports and findings, see it in storage analysis |
| `scan` | Trigger a scan of it, and configure scanning for its repository |
| `delete` | Delete its tags, delete its scan reports, and have retention act on it |

A rule combines an effect (`allow` or `deny`), an action set, a repository pattern and an image pattern. Patterns are Ant-style: `*` matches any characters except `/`, `**` crosses `/`, `?` is one character. See [the permission model](/docs/permission-model) and the [cookbook](/docs/access-rules-cookbook).
