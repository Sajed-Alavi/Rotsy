# Settings

## Nexus connection

Base URL, username, password and SSL verification for the Nexus account everything runs through. **Test** performs a real authenticated call and reports what came back.

The password is encrypted at rest. See [Connecting to Nexus](/docs/connecting-nexus).

## Registry discovery

Read-only. Shows every Docker repository, the connector endpoint discovered for it, and a reachability check — plus an explicit list of anything that could not be resolved, and why.

There is nothing to configure here by design. See [Registry discovery](/docs/registry-discovery).

## Scan-on-push webhook

The URL to give Nexus, and the shared secret. **Rotate** generates a new secret and invalidates the old one immediately — update the Nexus capability at the same time or push events stop being accepted.

## Scanner proxy

An HTTP proxy for vulnerability-database downloads. Stored in the database rather than the environment, so it takes effect without a redeploy. `SCANNER_PROXY` in the environment acts as a fallback default.

This affects database downloads only, not Nexus communication.

## Profile and password

Your own display name and password. Changing your password does not invalidate existing sessions on other devices.
