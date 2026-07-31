# Initial security audit — 2026-07-31

## Scope

Full-codebase review of Sharpy (FastAPI backend + React frontend, a
management/scanning console in front of Sonatype Nexus): authentication,
authorization/RBAC, database access, configuration/secrets management,
service-layer code (scanners, backups, sync, alerting), all API routers, and
frontend API usage. Dependency versions were checked for known CVEs against
what's pinned in `backend/requirements.txt`.

## Method

Three parallel focused passes (backend auth/config/secrets, backend services,
routers + frontend + dependencies) read every file end-to-end plus the diff
against the previous commit (`bf16f5c`) to distinguish pre-existing issues
from ones introduced by the most recent rework. Every finding below is backed
by a specific file and line, not a generic category — findings without a
concrete, reproducible code path were discarded rather than reported.

## Results

16 findings: 3 Critical, 3 High, 6 Medium, 4 Low. Full detail in
[../VULNERABILITY-INVENTORY.md](../VULNERABILITY-INVENTORY.md) and
[../findings/](../findings/).

**Fixed this pass (all Critical + all High):**

- **CRIT-01** — path traversal in the asset-download Nexus proxy, reachable
  by any user with `repositories:read`, letting the backend's privileged
  Nexus service account be used to hit arbitrary Nexus REST endpoints.
- **CRIT-02** — `.env.example` shipped placeholder `JWT_SECRET` and
  `BOOTSTRAP_ADMIN_PASSWORD` values usable as-is; the app now fails to start
  rather than boot with them.
- **CRIT-03** — path traversal in the byte-level backup archiver's
  repository-name handling, giving a write primitive outside the backup
  volume.
- **HIGH-01** — one of four asset-listing endpoints silently skipped the
  per-image RBAC scope check its siblings enforce.
- **HIGH-02** — the per-image RBAC scope feature was defeated by simply
  holding a second role without scope rows (the common case for any
  multi-role user) — now closable per-role via
  `image_scope_unrestricted=False`.
- **HIGH-03** — `python-jose==3.3.0` (two unfixed CVEs) replaced with the
  actively-maintained `PyJWT`.

Each fix has a regression test under `backend/tests/` (new test
infrastructure — none existed before this pass) and its own finding file
recording implementation status and the specific test that validates it.

**Deferred to backlog (Medium + Low):** documented with root cause and a
concrete recommended fix each, so the next pass doesn't start from scratch —
see MED-01 through MED-06 and LOW-01 through LOW-04 in
[../VULNERABILITY-INVENTORY.md](../VULNERABILITY-INVENTORY.md). Two SSRF
findings (MED-04, MED-05) and one mass-assignment finding (MED-03) are the
highest-value items for the next pass.

## What this audit did not cover

- Live penetration testing / dynamic scanning against a running instance —
  this was a static code review; no container or the app itself was started
  in this session.
- Infrastructure/deployment hardening beyond what's visible in
  `docker-compose.yml` and `.env.example` (e.g. no review of the host
  environment the containers would actually run on).
- The frontend build toolchain's own dependency tree (`frontend/node_modules`)
  was not audited for CVEs — only the backend's `requirements.txt` was.

## Verification

Not run in this session (see project policy on runtime execution). Manual
commands for the user to run themselves are listed in the implementation
plan (`pytest`, a placeholder-secret rejection check, `alembic upgrade head`,
and manual exploit re-checks against a running instance).
