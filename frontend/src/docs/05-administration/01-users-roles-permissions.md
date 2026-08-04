# Users, roles and permissions

## Users

**Users** creates accounts, assigns roles, and deactivates people who have left. Deactivation is preferred over deletion — it preserves the audit trail, and it immediately invalidates every API token the user issued, since token permissions resolve against the owner's live account on each request.

## Roles

**Roles & Permissions** is where both halves of a role are edited, on separate tabs.

**Permissions** answer *what* the role may do: the `resource:action` keys, grouped by the area they govern. Effective permissions are the union across a user's roles.

**Access rules** answer *where* those actions reach: repository and image wildcard patterns, each granting some combination of `read`, `scan` and `delete`, as either an allow or a deny. A role with no matching rule for a repository falls back to its **access mode** — `unrestricted` (the default: that repository stays open) or `scoped` (deny by default).

Three roles are seeded on first startup: `admin`, `operator` and `viewer`. You can edit them and delete roles you created. `admin` cannot be scoped or given rules — an administrator locked out of a repository would have no way back through the app.

See [the permission model](/docs/permission-model) for the full semantics, and the [access rules cookbook](/docs/access-rules-cookbook) for configurations you can copy.

## The habit that makes scoping work

Because effective access is the union across a user's roles, a single `unrestricted` role with no matching rules reopens everything a scoped role was meant to restrict. Whenever you scope a role, set the baseline role those users also hold — usually `viewer` — to `scoped` as well.

The **Test these rules** panel in the role editor shows what a role would do to a specific repository and image before you save. The effective-access view on a user resolves every role they hold at once, which is where union surprises actually surface.

## Rotsy's RBAC is not Nexus's

They are separate systems with separate databases. Granting someone `repositories:write` here does not give them any Nexus privilege, and it does not need to — actions are performed by the backend's Nexus service account.

The one place Rotsy touches Nexus's own security model is [anonymous access](/docs/tokens-and-webhooks), which manipulates the built-in `nx-anonymous` role.

## The consequence worth understanding

Because everything runs through one privileged service account, **Rotsy's access control is the only thing standing between a user and that account's full authority.** That is why rules are enforced on every path that touches image data — the image list, the asset list, the download proxy, scan reports and findings, storage analysis and retention — rather than just the obvious one.
