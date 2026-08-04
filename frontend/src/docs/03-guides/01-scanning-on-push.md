# Scanning on push

Getting from "Nexus has images" to "every new image is scanned automatically".

## 1. Enable a repository

**Vulnerability Scanning → Targets → Enable repo**. Pick a Docker repository, choose which scanners to use (both by default), and leave *auto scan* on.

The moment you save, the repository is baselined: everything already in it is recorded as history and **not** scanned. See [The ledger and baseline](/docs/the-ledger-and-baseline) for why.

## 2. Wire up push events

An image is scanned for exactly two reasons — it was pushed, or you asked. The push path has a primary and a fallback.

### Primary: the Nexus webhook

Nexus posts to `POST /api/scan/events/nexus` the moment a component is created or updated. Reaction time is seconds. The request is authenticated by an HMAC signature in `X-Nexus-Webhook-Signature`, not by a user session — it is a machine calling a machine.

Get the secret and the exact URL from **Settings → Scan-on-push webhook**, then in Nexus:

1. **Administration → System → Capabilities → Create capability**
2. Choose **Webhook: Repository**
3. Set the URL to `https://your-rotsy-host/api/scan/events/nexus`
4. Paste the secret
5. Select the repositories to emit events for

You can rotate the secret from the same Settings panel. Rotating invalidates the old one immediately, so update Nexus at the same time.

### Fallback: the new-image watcher

For deployments where you cannot add the capability, `_push_watch_loop` lists each enabled repository's components every `SCAN_PUSH_POLL_SECONDS` and queues a scan **only** for images the ledger has never seen. It compares metadata; it does not re-scan anything already known.

Set `SCAN_PUSH_POLL_SECONDS=0` to turn it off and rely purely on webhooks.

## 3. Make sure a database is installed

Scans cannot run without a vulnerability database. Check **Vulnerability Scanning → Database Management**; both scanners should read *ready*. If not, see [Updating the databases](/docs/updating).

## 4. Push something

Push an image to the repository and watch **Vulnerability Scanning → Images**. The image appears with state `queued`, then `scanned`, with its severity counts.

## Scanning on demand

The Scan button on any row of the Images view is the only path that re-scans an image already scanned or baselined. Use it for spot checks and for pulling baselined history into scope.
