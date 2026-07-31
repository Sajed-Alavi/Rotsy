# RBAC and image scopes

Sharpy has its own role-based access control, in its own Postgres tables. It is **not** Nexus's RBAC and does not read or write Nexus roles.

## Users, roles, permissions

A user holds roles; a role grants permissions. Effective permissions are the union across a user's roles. Permission keys are `resource:action` — `scan:read`, `repositories:write`, `tasks:control`, and so on. The full list is in the [permissions matrix](/docs/permissions-matrix).

Three roles are seeded on first startup:

| Role | Intent |
|---|---|
| `admin` | Everything |
| `operator` | Day-to-day work: scan, analyse, run jobs, manage retention |
| `viewer` | Read-only across the board |

## Image scopes

Permissions answer *what actions*. Image scopes answer *which images*.

A role can be restricted to images matching shell-glob patterns within a repository — for example `team-a-*` in the `docker-hosted` repository. A user holding only that role sees, scans and downloads only matching images. The restriction is enforced on the image list, the asset list, the download proxy and the scan endpoints.

## The subtlety worth understanding

A role with no scope rows for a repository is unrestricted there, and effective access is the union across roles. That combination has a sharp edge: holding *any* second unscoped role — the baseline `viewer`, say — would silently reopen everything a scoped role was meant to restrict.

Roles therefore carry an **`image_scope_unrestricted`** flag. A role grants blanket access only when it both has no scope rows *and* has that flag set. Turning it off makes a role always defer to scope rows, so it can no longer widen anyone's access by accident.

It defaults to on, so existing behaviour is unchanged until you deliberately opt a role out.

> When you scope a role, also turn off `image_scope_unrestricted` on the other roles those users hold — otherwise the scoping does nothing.

## API tokens inherit, never exceed

A token's effective permissions are the intersection of its declared scopes with its owner's *current* permissions, resolved on every request. Removing someone's role immediately narrows every token they issued. A token cannot outlive the authority it was minted from.
