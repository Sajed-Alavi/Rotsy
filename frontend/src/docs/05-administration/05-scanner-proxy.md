# Scanner proxy and restricted networks

Notes for deployments that cannot reach the public internet directly.

## What needs outbound access

Only the vulnerability databases:

| Destination | For |
|---|---|
| `registry-1.docker.io` | Trivy database |
| `ghcr.io` | Trivy Java database |
| `grype.anchore.io` | Grype database listing and archives |

Nothing else in normal operation. Scanning itself reads images from *your* Nexus, not from the internet.

## Option 1: a proxy

Set it in **Settings → Scanner proxy**, or `SCANNER_PROXY` in the environment as a fallback. It is passed to the download subprocesses as `HTTP_PROXY`/`HTTPS_PROXY`.

## Option 2: fully air-gapped

Skip outbound access entirely and import the databases from archives. See [Offline and air-gapped installs](/docs/offline-airgapped).

## The failure mode to avoid

If the scanners are left to manage their own databases, a blocked download does not just fail to update — it fails the *scan*, because both tools try to refresh before scanning by default.

Rotsy disables that and manages the database separately, precisely so a network problem produces "the database is stale" rather than "every image failed to scan". If you are seeing the latter, something has bypassed that arrangement.

## Egress allow-list

If you allow-list by hostname rather than proxying, the three destinations above are the complete set. The scanners do not phone home and do not fetch anything else at scan time.
