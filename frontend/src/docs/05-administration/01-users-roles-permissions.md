# Users, roles and permissions

## Users

**Users** creates accounts, assigns roles, and deactivates people who have left. Deactivation is preferred over deletion — it preserves the audit trail, and it immediately invalidates every API token the user issued, since token permissions resolve against the owner's live account on each request.

## Roles

**Roles & Permissions** creates roles and assigns permission keys. Effective permissions are the union across a user's roles.

Three roles are seeded on first startup: `admin`, `operator` and `viewer`. You can edit them, and you can delete roles you created.

## Image scopes

A role can be restricted to images matching shell-glob patterns within a repository. See [RBAC and image scopes](/docs/rbac-and-image-scopes) for the full model — in particular the `image_scope_unrestricted` flag, which is what stops a second unscoped role from silently undoing your scoping.

## Sharpy's RBAC is not Nexus's

They are separate systems with separate databases. Granting someone `repositories:write` here does not give them any Nexus privilege, and it does not need to — actions are performed by the backend's Nexus service account.

The one place Sharpy touches Nexus's own security model is [anonymous access](/docs/tokens-and-webhooks), which manipulates the built-in `nx-anonymous` role.

## The consequence worth understanding

Because everything runs through one privileged service account, **Sharpy's RBAC is the only thing standing between a user and that account's full authority.** That is why image scoping is enforced on every path that touches image data — the list, the asset list, the download proxy and the scan endpoints — rather than just the obvious one.
