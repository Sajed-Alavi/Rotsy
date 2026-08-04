# Upgrade notes & changelog

## System requirements

| Component | Version |
|---|---|
| Python (backend image) | 3.13.14 |
| Node.js (frontend build image) | 24.16.0 (LTS "Krypton", supported through April 2028) |
| PostgreSQL | 16.6 |
| Redis | 7.4.1 |
| Docker + Docker Compose | any recent release |

These are pinned in `backend/Dockerfile`, `frontend/Dockerfile`, and
`docker-compose.yml` — not floating tags, so a rebuild reproduces the same
runtime rather than drifting.

## 2026-08-02 — Python 3.13, Node LTS, dependency refresh, and three fixes

### Upgrade notes (read before deploying over an existing install)

- **Existing `backup-data` Docker volumes need a one-time ownership fix.**
  Older images didn't create `/app/backups` before switching to the non-root
  runtime user, so a volume provisioned by an older build is owned by `root`
  and the backend can't write to it — this is the "Permission denied" backup
  bug. New images fix this for *fresh* volumes automatically; an existing
  volume needs:
  ```bash
  docker compose run --rm -u root backend chown -R app:app /app/backups
  ```
  See [Backups and sync](/docs/backups-and-sync) for detail.
- **A new migration adds `backup_schedules`** and a `schedule_id` column on
  `backup_runs`. Migrations run automatically from `backend/entrypoint.sh` on
  container start — no manual step beyond the usual `docker compose up
  --build`.
- **`package-lock.json` needs regenerating** against the refreshed
  `package.json` before `frontend/Dockerfile`'s `npm ci` will succeed —run
  `cd frontend && npm install` once and commit the updated lockfile.
- Full dependency-by-dependency detail, including which packages were
  deliberately **not** bumped to their newest major (and why), is in
  `security/cve/dependency-cve-review.md` §4 and
  `security/reports/2026-08-02-python313-dependency-upgrade.md` in the
  repository (not part of the in-app docs, since it's an audit trail, not
  user-facing usage documentation).

### Changelog

**Platform**
- Python 3.11.11 → 3.13.14; Node 22.23.2 → 24.16.0 (LTS).
- Backend and frontend dependencies refreshed to current stable releases;
  `redis`, `sse-starlette`, and `tailwindcss` deliberately held back from
  their newest major (breaking-change risk not verifiable without a runtime
  test pass — tracked as a follow-up).

**Fixed**
- Backup archive creation failing with `PermissionError` on `/app/backups`
  when the Docker volume was freshly provisioned.

**Added**
- Scheduled backups: daily/weekly/monthly/cron cadence, compressed `.tar.gz`
  archives, per-schedule retention. See
  [Backups and sync](/docs/backups-and-sync).
- PDF export for a vulnerability scan report (repository, image, tag, scan
  date, severity breakdown, full CVE list, recommendations). See
  [Reading a report](/docs/reading-a-report).

**Changed**
- The vulnerability-scanning **Images** view is now a repository → image →
  tag tree instead of a flat table mixing every repository together. See
  [Reading a report](/docs/reading-a-report).
