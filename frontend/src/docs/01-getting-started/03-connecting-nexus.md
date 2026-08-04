# Connecting to Nexus

Rotsy needs one Nexus account. Everything it does — reading repositories, listing components, pulling manifests for scanning — happens through it.

## Configure the connection

Go to **Settings → Nexus connection**. You need:

| Field | Notes |
|---|---|
| Base URL | The Nexus REST endpoint, e.g. `http://nexus-host:8081`. Not a Docker connector port. |
| Username | A Nexus account with repository-admin read privileges |
| Password | Stored encrypted at rest |
| Verify SSL | Leave on unless Nexus uses a self-signed certificate |

Click **Test** before saving. It performs a real authenticated call and reports exactly what came back, so a typo or a permissions problem surfaces immediately rather than as empty pages later.

## Why the account needs repository-admin read

Registry discovery reads each Docker repository's connector port from its configuration. Without repository-admin read privileges, Nexus returns the repository but omits the `docker` connector block, and every Docker repository shows up as *unresolved* with that reason. The built-in `nx-admin` role covers it.

This is the single most common setup problem. If the Settings page shows repositories under "unresolved", check this first.

## How the password is stored

The Nexus password is encrypted at rest with a Fernet key derived from `NEXUS_CONFIG_ENCRYPTION_KEY`. That variable is mandatory and must differ from `JWT_SECRET` — they protect different things and need to be rotatable independently.

> **If you rotate `NEXUS_CONFIG_ENCRYPTION_KEY`**, the stored password can no longer be decrypted. The app keeps running and reports the connection as unconfigured; re-enter the password here once and it is fixed.

## Confirm discovery worked

Still in **Settings**, the *Registry discovery* panel lists every Docker repository with the connector endpoint found for it and a reachability check. Anything under "unresolved" tells you what went wrong per repository.

## Next

[A quick tour](/docs/quick-tour).
