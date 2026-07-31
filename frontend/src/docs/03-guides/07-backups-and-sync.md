# Backups and sync

Both live under **System & Scripts**.

## Metadata export

Nexus OSS does not expose a backup API. Instead, Sharpy produces a **metadata export**: a downloadable JSON containing every repository's configuration plus asset manifests.

This is often more useful than a raw database dump — it is version-independent and can be fed into the sync service — but be clear about what it is not. It contains metadata, not artifact content. It will not restore your blobs.

## Archive backup

An archive backup downloads actual asset content into a run directory on the backend, producing a manifest and the files themselves.

Two modes: **full** (everything) and **selective** (named repositories).

Guards worth knowing about:

- Repository names are validated before they are used as directory names. A name that is absolute or contains traversal segments is rejected before the job is queued — otherwise it would be a write primitive for anyone who can start a backup.
- Free disk space is checked before the run and continuously during it, both every N assets and every N bytes written. A burst of large layer blobs cannot outrun the check and fill the volume.
- Each run gets a collision-proof id, so two runs starting in the same second cannot interleave writes into the same directory and silently corrupt each other.

Completed runs are listed with a download link.

## Nexus-to-Nexus sync

Copies components from repositories on this Nexus to repositories on another one — for promoting artifacts between environments or seeding a new instance.

You supply the target base URL, credentials and one or more source-to-target repository pairs.

> The target URL is validated against the SSRF guard: loopback, private, link-local and cloud-metadata addresses are refused unless explicitly allow-listed. The backend often has network access the caller does not, so an unvalidated destination would turn this into a request-forgery tool.

The target password is encrypted before the job payload is stored, rather than sitting in the queue in plaintext for the job's lifetime.
