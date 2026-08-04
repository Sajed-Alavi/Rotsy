# The permission model

Sharpy has its own role-based access control, in its own Postgres tables. It is **not** Nexus's RBAC and does not read or write Nexus roles.

The model has two independent axes, and keeping them apart is the whole idea:

| Axis | Question it answers | Where it lives |
|---|---|---|
| **Permissions** | *What* may this user do? | Permission keys on a role |
| **Access rules** | *Where* do those actions reach? | Access rules on a role |

A user needs both. `scan:execute` says they may trigger scans at all; an access rule granting the `scan` action on `abrisham*` says which images they may trigger them on. Neither is sufficient alone.

## Users, roles, permissions

A user holds roles; a role grants permissions. Effective permissions are the union across a user's roles. Permission keys are `resource:action` — `scan:read`, `repositories:write`, `tasks:control`, and so on. The full list is in the [permissions matrix](/docs/permissions-matrix).

Three roles are seeded on first startup:

| Role | Intent |
|---|---|
| `admin` | Everything |
| `operator` | Day-to-day work: scan, analyse, run jobs, manage retention |
| `viewer` | Read-only across the board |

## Access rules

An access rule is one statement with five parts:

```
<effect>  <actions>  on  <repo pattern> / <image pattern>
```

For example, "allow read and scan on `abrisham-hosted` / `abrisham*`" — the case this model was built for. Some more:

| Effect | Actions | Repository | Image | Meaning |
|---|---|---|---|---|
| allow | read | `*` | `abrisham*` | See every `abrisham*` image, in every repository |
| allow | read, scan | `prod-*` | `**` | Full read and scan across every production repository |
| allow | read, scan, delete | `docker-hosted` | `team/*` | Own everything directly under `team/` in one repository |
| deny | read | `*` | `*-secrets` | Never see anything ending in `-secrets` |

### Actions

There are three, and they are independent. This is what lets you hand a team its own images without also handing it the ability to destroy them.

| Action | Grants |
|---|---|
| `read` | See the image in listings, browse and download its assets, read its scan reports and findings, see it in storage analysis |
| `scan` | Trigger a scan of it, and configure scanning for its repository |
| `delete` | Delete its tags, delete its scan reports, and have retention act on it |

### Effects

`allow` grants. `deny` takes away, and **beats an allow within the same role**. That is how you express "everything except":

```
allow  read,scan  on  *  /  abrisham*
deny   read,scan  on  *  /  abrisham-secrets*
```

A deny only applies inside the role that declares it. It cannot reach across and strip access that a *different* role grants outright — see [when rules combine](#when-rules-combine).

## Wildcard patterns

Patterns are Ant-style, the same grammar JFrog Artifactory uses. They are anchored: the pattern must match the whole name, not merely part of it.

| Token | Matches |
|---|---|
| `*` | Any run of characters **except** `/` |
| `**` | Any run of characters, **including** `/` |
| `?` | Exactly one character, not `/` |

The `/` rule is the one to internalise, because Docker image names are often nested (`team/api/edge`):

| Pattern | `team/api` | `team/api/edge` |
|---|---|---|
| `team/*` | matches | does not match |
| `team/**` | matches | matches |

Worked examples:

| Pattern | Matches | Does not match |
|---|---|---|
| `abrisham*` | `abrisham`, `abrisham-frontend`, `abrisham-frontend:1.4` | `not-abrisham`, `abrisham/sub` |
| `prod-*` | `prod-api`, `prod-web` | `prod`, `staging-api` |
| `team/*` | `team/api` | `team`, `team/api/edge` |
| `**` | everything | — |
| `*` | `app` | `team/app` |

Matching is case-sensitive. Repository names contain no `/`, so on the repository side `*` and `**` behave identically — `prod-*` is the normal way to write it.

What the image pattern is matched against differs slightly by surface: the repository browser matches the image **display name** (`group/name`), while the scan ledger matches `name:tag`. A pattern like `abrisham*` covers both; one that pins an exact name may not.

## Access modes

Every role has an access mode, which decides what it does with a repository **none of its rules mention**.

| Mode | A repository no rule matches |
|---|---|
| `unrestricted` | Fully accessible. The default, and how roles behaved before access rules existed |
| `scoped` | Not accessible at all. The role grants only what its own rules allow |

The moment *any* rule of the role matches a repository, that repository is decided by the rules alone — the mode stops applying there. So an `unrestricted` role with one rule for `docker-hosted` is restricted in `docker-hosted` and open everywhere else.

The seeded roles are all `unrestricted`, so an install that never writes a rule behaves exactly as it always did. `admin` is pinned that way and cannot be changed: an administrator locked out of a repository would have no way back through the app.

## When rules combine

Rules are evaluated **per role**, and the results are unioned across the roles a user holds. One role granting an action is enough.

That has a sharp edge worth stating plainly: **holding any `unrestricted` role with no matching rules opens everything**, however carefully another role is scoped. If you scope a role, set the baseline roles those same users hold — typically `viewer` — to `scoped`, or the scoping does nothing.

> Denies are role-local by design. A deny in role A cannot veto an allow in role B. If it could, no role's rules could be read in isolation and one role could silently break another. Artifactory works the same way: exclude patterns only narrow the permission target that declares them.

To see what actually happened for a given person, use the **effective access** view on a user, which shows the decision role by role and names the rule responsible.

## Repository-level access control

Access rules gate the repository list itself, not just its contents. A `scoped` role sees only repositories at least one of its allow rules reaches — in the repository browser, the storage analyzer, the scan target list, retention policies and per-repository metrics. Someone who cannot open a repository does not learn it exists.

Repository-wide operations are held to a higher bar. Creating a retention policy, enabling scanning for a repository, or deleting every report affects images that do not exist yet, so each requires access to the **whole** repository — an allow rule whose image pattern is `**` — not merely to part of it. A role scoped to `abrisham*` can scan and delete its own images but cannot author a policy whose blast radius is everything.

## API tokens inherit, never exceed

A token's effective permissions are the intersection of its declared scopes with its owner's *current* permissions, resolved on every request. Access rules apply to a token exactly as they apply to its owner, because a token resolves to the owner's account. Removing someone's role immediately narrows every token they issued. A token cannot outlive the authority it was minted from.

## Sharpy's RBAC is not Nexus's

They are separate systems with separate databases. Granting someone `repositories:write` here does not give them any Nexus privilege, and it does not need to — actions are performed by the backend's Nexus service account.

Because everything runs through that one privileged account, **Sharpy's access control is the only thing standing between a user and its full authority.** That is why rules are enforced on every path that touches image data — listings, the asset list, the download proxy, scan reports and findings, storage analysis and retention — rather than just the obvious one.

Ready to write some? See the [access rules cookbook](/docs/access-rules-cookbook).
