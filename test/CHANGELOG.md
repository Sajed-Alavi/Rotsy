# Changelog

## v5 (current)

### Critical bug fixes
- **#9 Storage search black screen** — `flatMap` returning arrays of JSX
  inside `<tbody>` crashed on filtered results with null `versions`. Rewrote
  to use `React.Fragment` + defensive null checks. Search now works without
  crashing.
- **#6 Scan failures (0/0/0/0)** — `_registry_ref()` read
  `nexus.settings.NEXUS_URL` which was stale/empty after dashboard
  reconfiguration. Fixed to read the live httpx client's `base_url` so
  scans use the correct registry address after Settings changes.
- **#5 Backup 502** — `/service/rest/v1/backup` is Pro-only and returns 404
  on Nexus OSS. Now returns 404 with a clear message instead of 502.

### Monitoring improvements
- **#3 Health check categorization** — probes are now categorized as
  `critical` (blobstore, CPU, DB), `security` (advisory: default creds,
  encryption key), or `info` (ECR tokens, NuGet V2). `HealthTile` colors
  accordingly: red for critical failures, amber for advisory, green for
  healthy. No more confusing false-positive red tiles.

### Vulnerability scanning overhaul
- **#7 Scan page rework** — reports table now has per-row delete buttons,
  a "Clear all" button, and clickable rows that open a detail view.
- **#12 Detailed vulnerability view** — clicking a successful report opens
  a modal with severity summary tiles + filterable finding list (CVE,
  package, installed/fixed version, CVSS).
- **#1/#13 Manual DB download** — DB status card shows version + date +
  size + freshness; "Refresh vuln DBs" button triggers a background job
  with live progress (polls dir size every 2s).
- **Scanner target dropdown** — only lists Docker-format repos (uses
  `?format=docker` filter).
- **Scanner proxy** — configurable from Settings UI (stored in DB), used
  by all scanner subprocesses.

### Retention improvements
- **#4 Repo dropdown** — retention policy form now shows a dropdown of all
  repositories instead of free text.
- Daily scheduler runs all enabled policies at `RETENTION_RUN_AT`.

### Job management
- **#13 Cancellable jobs** — running/pending jobs show a "cancel" button
  in Background Jobs that marks them as cancelled (cooperative).

### Settings
- **Scanner proxy section** — admin can set/change the proxy for DB
  downloads without editing `.env`.
- Nexus Connection section — URL/username/password/SSL + test button.
- Profile + password change sections.

### Backend additions
- `GET /storage/repos?format=docker` — filter repos by format (for scanner
  dropdowns).
- `POST /jobs/{id}/cancel` — cooperative job cancellation.
- `DELETE /scan/reports/{id}` + `DELETE /scan/reports` — delete one or all
  scan reports.
- `GET /scan/reports/{id}/vulnerabilities` — detailed findings per report.
- `GET /settings/scanner-proxy` + `PUT /settings/scanner-proxy` — proxy
  config stored in DB.
- `GET /metrics/health` — now returns categorized probes
  (`critical`/`security`/`info`).

## v4
- Scanner DB download via `oras` (OCI registry) + proxy support.
- Dashboard-managed Nexus connection (encrypted in DB).
- Idle logout (30 min).
- Trivy + Grype + oras in backend Docker image.

## v3
- Background job framework (Redis queue), historical metrics, alerting,
  real-time monitoring endpoints, self-service settings, theme toggle.

## v2
- JWT auth, RBAC with fine-grained permissions, Postgres persistence,
  dark console UI rebuild.

## v1
- Initial scaffolding, deep storage analyzer (Docker), Docker setup.
