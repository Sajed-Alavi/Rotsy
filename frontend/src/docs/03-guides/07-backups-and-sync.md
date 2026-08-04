# Backups and sync

Both live under **System & Scripts**.

## Metadata export

Nexus OSS does not expose a backup API. Instead, Rotsy produces a **metadata export**: a downloadable JSON containing every repository's configuration plus asset manifests.

This is often more useful than a raw database dump — it is version-independent and can be fed into the sync service — but be clear about what it is not. It contains metadata, not artifact content. It will not restore your blobs.

## Archive backup

An archive backup downloads actual asset content into a run directory on the backend, producing a manifest and the files themselves.

Two modes: **full** (everything) and **selective** (named repositories).

Guards worth knowing about:

- Repository names are validated before they are used as directory names. A name that is absolute or contains traversal segments is rejected before the job is queued — otherwise it would be a write primitive for anyone who can start a backup.
- Free disk space is checked before the run and continuously during it, both every N assets and every N bytes written. A burst of large layer blobs cannot outrun the check and fill the volume.
- Each run gets a collision-proof id, so two runs starting in the same second cannot interleave writes into the same directory and silently corrupt each other.

Completed runs are listed with a download link.

### "Permission denied" writing to the backup directory

If an archive run fails with a permission error on `/app/backups`, the backup
volume was created before the image knew to seed it with the right ownership.
Docker creates a brand-new named volume owned by `root`, and the backend
runs as a non-root user — current images fix this at build time by creating
and `chown`ing `/app/backups` before switching to that user, but an **already
existing** volume from an older image build keeps its old (root) ownership.
Fix it once:

```bash
docker compose run --rm -u root backend chown -R app:app /app/backups
```

## Scheduled backups

Rather than triggering an archive by hand, a **backup schedule** runs one
automatically — same full/selective target as above, plus a cadence:

- **daily** / **weekly** / **monthly**, each at a fixed time of day, or
- a **custom cron expression**, for anything the presets don't cover.

All schedule times are evaluated in UTC. Configure schedules under **System →
Scheduled backups**: name, target (full or a list of repositories), cadence,
and a retention rule (keep the last *N* archives, delete archives older than
*N* days, or both).

Two things make a scheduled run different from a manual one:

- **The archive is a single compressed `.tar.gz`**, not a plain directory of
  files — each asset is streamed to a small scratch file and immediately
  folded into the archive and deleted, so extra disk usage never exceeds the
  size of the single largest in-flight asset, however large the whole backup
  is.
- **Its own archives are pruned automatically** once they age out of the
  schedule's retention rule. A schedule only ever prunes archives *it*
  created — a manual, on-demand backup is never touched by any schedule's
  retention.

Use **preview next run** on the schedule editor to sanity-check a cadence
(especially a cron expression) before saving, and **run now** to trigger an
out-of-band run without waiting for the next scheduled time.

## Nexus-to-Nexus sync

Copies components from repositories on this Nexus to repositories on another one — for promoting artifacts between environments or seeding a new instance.

You supply the target base URL, credentials and one or more source-to-target repository pairs.

> The target URL is validated against the SSRF guard: loopback, private, link-local and cloud-metadata addresses are refused unless explicitly allow-listed. The backend often has network access the caller does not, so an unvalidated destination would turn this into a request-forgery tool.

The target password is encrypted before the job payload is stored, rather than sitting in the queue in plaintext for the job's lifetime.
