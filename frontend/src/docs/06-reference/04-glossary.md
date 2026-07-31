# Glossary

**Asset** — a single file in Nexus: a layer blob, a manifest, a jar. One image is many assets.

**Baseline** — an image recorded in the ledger as pre-existing history. Never auto-scanned. See [The ledger and baseline](/docs/the-ledger-and-baseline).

**Blobstore** — the physical storage backing one or more repositories, file or S3. Blobs are shared, so repository sizes do not sum to blobstore usage.

**Component** — Nexus's unit of a published thing: one image tag, one jar version. Made of assets.

**Compaction** — the Nexus task that reclaims disk from deleted components. Until it runs, deletions free nothing.

**Connector port** — the per-repository port on which Nexus serves the Docker Registry v2 API. Discovered, never configured.

**CVE** — Common Vulnerabilities and Exposures identifier, e.g. `CVE-2024-3094`.

**Finding** — one vulnerability in one package in one report.

**Grype** — Anchore's vulnerability scanner. One of the two backends.

**Job** — a unit of background work in the Redis-backed queue, with live progress over SSE.

**Ledger** — `scan_image_ledger`, the durable record of every known image and its state.

**Manifest** — the registry document describing an image: its layers and configuration. Fetching it is how an image is identified without pulling it.

**Manifest digest** — the content hash of a manifest. What the ledger compares, so a re-pushed tag is correctly seen as new.

**Report** — the result of one scanner run against one image. Two scanners produce two reports.

**Scan target** — a repository enabled for scanning.

**SSE** — Server-Sent Events, the one-way streaming protocol used for live job progress.

**Stale database** — a vulnerability database older than five days.

**Static analysis** — reading an image as data over the registry API, never executing it. See [The static-only guarantee](/docs/static-only-guarantee).

**Trivy** — Aqua Security's vulnerability scanner. The other backend.
