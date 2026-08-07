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

## 2026-08-08 — GitLab support, a global Code Quality section, and automatic per-branch analysis

### Upgrade notes

- **A new migration adds GitLab tables** (`gitlab_connections`, `gitlab_repositories`) and columns on `sonar_projects` for the auto-analyze toggle and per-branch Sonar project keys. Runs automatically on container start, same as always.
- If you already had SonarQube connected and repositories analyzed under the old per-Project flow, nothing is lost — existing `SonarProject` rows keep working. New analyses of a non-default branch will provision a second, per-branch Sonar project the first time that branch is analyzed (see below); this is additive, not a migration you need to run yourself.

### Added

- **GitLab as a second source provider**, alongside GitHub: account-level (one token, many repositories) or repository-level (one token, one repository) connections, per-repository webhooks, branch listing. See [Connecting GitLab](/docs/connecting-gitlab).
- **Code Quality**, a new global section under Security: pick any synced GitHub/GitLab repository and branch and run SonarQube analysis, independent of Project grouping. Four tabs — Overview, Analysis Runs, Findings, Settings (connection health, Check for Updates). Replaces the old per-Project Analysis tab.
- **Per-repository, per-branch auto-analyze control** (Project → Repositories tab): which branches trigger analysis on push, on by default for the default branch only.
- **Automatic per-branch Sonar project provisioning** — works around SonarQube Community Edition's inability to analyze more than one branch under a single project, transparently, for any number of branches or repositories.
- **A "Suggested Fixes" table on the SonarQube PDF export** — one row per distinct rule that fired, with a short remediation hint pulled live from SonarQube's own rule documentation.
- **GitHub App creation via GitHub's App Manifest flow** — no more manually setting `GITHUB_APP_ID`/`GITHUB_APP_PRIVATE_KEY`/etc.; clicking Connect creates the App and saves its credentials automatically.
- **Connect a public GitHub repository by name**, without an App installation, for repositories you don't own or administer — manual-analysis-only (no push webhook), a deliberate trade-off.
- A **View** button on each Background Jobs row, linking to that job type's own page (Code Quality Runs, Scan Images, Retention, ...).

### Fixed

- `sonar-scanner` silently analyzing almost nothing because it ran from the wrong working directory, while still reporting a successful analysis.
- Analysis run duration corruption on a same-commit re-run.
- SonarQube's own quality-gate defaults silently reappearing after Rotsy set the intended conditions.
- GitLab webhook deliveries never arriving on a self-managed instance: the callback URL Rotsy registered was the browser-facing address, which GitLab's own SSRF protection rejects outright (`WEBHOOK_BASE_URL` fixes this — see [Configuration reference](/docs/configuration)).
- `pytest` not running at all in the backend image (the test dependencies were never installed) — a dedicated Docker build stage (`docker compose --profile test run --rm backend-test`) fixes this without shipping test tooling in the production image.

### Changed

- Running and browsing SonarQube analysis moved off the Project page entirely, into the new global Code Quality section — a Project now only groups a repository with its Nexus artifacts and carries the Health Score.
- The Background Jobs page's "Analyze all" button was removed (no replacement — Code Quality's per-repository, per-branch flow replaces the old fan-out-everything model).

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
