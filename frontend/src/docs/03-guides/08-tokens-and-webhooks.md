# Tokens and webhooks

Everything under **Access & Webhooks**.

## API tokens

The dashboard authenticates with httpOnly cookies, which a pipeline cannot hold. Without tokens, CI would have to embed a person's password or run as the service account. Tokens make the narrow, revocable option the easy one.

### Issuing

**Access & Webhooks → API Tokens → New token**. Give it a name that says what it is for, an expiry, and optionally a set of scopes.

The plaintext token is shown **once**, at creation. Only a SHA-256 hash is stored, so it cannot be recovered or re-displayed — only replaced. Copy it then.

### Using

```bash
curl -H "Authorization: Bearer shp_..." \
     https://your-host/api/scan/summary
```

### Scopes narrow, never widen

A token's effective permissions are the intersection of its scopes with its owner's *current* permissions, resolved on every request. Leaving scopes empty means "inherit the owner's permissions" — it does not mean unrestricted.

The consequence worth internalising: revoking someone's role immediately shrinks every token they issued. A token cannot outlive the authority it was minted from.

### Revoking

Revocation takes effect on the next request that presents the token. Revoked rows are kept rather than deleted, so the trail survives.

Admins with `users:manage` see every token, so a leaver's credentials are discoverable. Everyone else sees only their own.

## Webhooks

The **Webhooks** tab is an index of every webhook the app takes part in, inbound and outbound. It links to the page that owns each setting rather than duplicating the controls.

**Inbound — Nexus push events.** Nexus calls `/api/scan/events/nexus` on a push. HMAC-authenticated. Configured in Settings; see [Scanning on push](/docs/scanning-on-push).

**Outbound — alert delivery.** Alert rules POST to their configured URL when a condition matches. Configured per rule under Alerts.

> Outbound destinations are validated at both save time and send time. The double check is not redundant: rules created before the guard existed still get checked, and re-validating at send closes the DNS-rebinding window between saving and firing.

## Anonymous access

The third tab shows Nexus's global anonymous-access toggle and every repository currently readable without logging in, with grant and revoke.

Previously this could only be granted, only at repository creation, via a checkbox — so a repository made public by accident was invisible here and fixable only in the Nexus UI.

Repositories are derived from the privileges actually attached to the `nx-anonymous` role, so grants made by hand in Nexus show up too.
