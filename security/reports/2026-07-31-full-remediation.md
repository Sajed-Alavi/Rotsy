# Full remediation pass — 2026-07-31

Follow-up to [2026-07-31-initial-security-audit.md](2026-07-31-initial-security-audit.md),
which fixed 6 of 16 findings and deferred 10 to backlog.

## Scope

Three objectives, all completed:

1. **Resolve every remaining finding.** All 10 deferred items (MED-01…06,
   LOW-01…04), plus the one validation gap the audit flagged against its own
   HIGH-01 fix.
2. **Fix every CVE.** Widened past the initial audit's `backend/requirements.txt`
   to include the npm dependency tree and every container base image — both
   explicitly out of scope last time.
3. **Reorganize the vulnerability-scanning code and this directory.**

A baseline commit (`5fd275d`) was taken before any change, so the whole pass is
reviewable as a diff against a known state.

## Findings resolved

| ID | Fix |
|---|---|
| MED-01 | `NEXUS_CONFIG_ENCRYPTION_KEY` is now required, validated, and must differ from `JWT_SECRET`; the `or settings.JWT_SECRET` fallback is gone. |
| MED-02 | Scope checks resolve the owning image through Nexus's components API (`images.asset_image_map`) and fail closed; the path-parsing heuristic was deleted. |
| MED-03 | `RepoCreate.extra` keys colliding with validated payload fields are rejected with 400. |
| MED-04 | New `app/core/outbound.py` guard on alert `webhook_url`, applied at both creation and delivery. |
| MED-05 | Same guard on sync `target_base_url`, applied at both enqueue and dispatch. |
| MED-06 | `COOKIE_SECURE=true` is the shipped default; `false` + an https origin refuses to boot. |
| LOW-01 | `passlib` removed; `bcrypt` called directly, preserving 72-byte truncation and `$2b$` compatibility. |
| LOW-02 | Sync target password is encrypted before entering the Redis job payload. |
| LOW-03 | Backup `run_id` carries 3 bytes of entropy. |
| LOW-04 | Disk-space checks bounded by bytes written as well as asset count. |

Each finding file carries the full detail and names its regression test.

### Two decisions worth calling out

**MED-01 is an operator-visible change.** A Nexus password already saved through
the dashboard was encrypted with the old `sha256(JWT_SECRET)` key and will not
decrypt under a new dedicated key. `decrypt_password` degrades to `""` rather
than raising, so the upgrade is: set `NEXUS_CONFIG_ENCRYPTION_KEY`, boot,
re-enter the Nexus password once. This is documented in `.env.example` next to
the setting, not only here. **An existing deployment will refuse to start until
that key is set** — that is the intended fail-fast behaviour, but it is a
breaking config change, not a silent one.

**MED-02 and MED-04/05 both chose failing closed over failing quietly.** An
asset Nexus attributes to no component is dropped from a scoped listing and
refused for download, and an unresolvable outbound host is rejected rather than
passed to the HTTP client. In both cases the alternative was to guess, and
guessing is what the original findings were about.

## CVEs

Full table in [../cve/dependency-cve-review.md](../cve/dependency-cve-review.md).
Summary: 6 Python CVEs closed (2× Starlette, 2× python-multipart, cryptography,
PyJWT) plus `passlib` removed; **7 npm advisories closed, `npm audit` now
reports 0**; 8 container images moved off floating tags; `setuptools` and the
base OS package set upgraded inside the backend image; `npm ci` now enforces the
frontend lockfile at build time.

### The npm side turned out bigger than planned

An initial attempt closed the one advisory known at planning time (`esbuild`)
with an `overrides` entry, on the reasoning that a Vite major bump could not be
build-verified here. Regenerating the lockfile then surfaced six further
advisories, and they could not be taken piecemeal: the fixed versions are
chained by peer dependencies.

`react-router` was the forcing function. Its three advisories have no clean 7.x
version — two are fixed in 7.17.1+, the third affects 7.12.0–8.2.0, and
`npm audit fix`'s only suggestion was to **downgrade** to 7.11.0, reinstating an
open-redirect issue that does apply to this SPA. The genuinely fixed version is
8.3.0, which requires React ≥19.2.7. That gives the chain: react-router 8 →
React 19 → `@vitejs/plugin-react` 6 → vite 8 → Node ≥22.22.0 — and the Vite bump
closes three more advisories on the way.

So the frontend moved React 18→19, react-router-dom 6→react-router 8, vite 5→8,
Node 20→22. The React upgrade was assessed before it was taken, not assumed: the
codebase has zero class components, zero `defaultProps`/`propTypes`, no
`findDOMNode`, no string refs, and already used `createRoot` — nothing on React
19's breaking-change list appears in it. `react-router-dom` was dropped rather
than upgraded, since v7 made it a re-export shim and v8 publishes none; the
seven import sites now use `react-router` directly. The `overrides` entry was
removed once esbuild left the tree entirely (vite 8 uses rolldown).

Verified by running `npm ci` and `npm run build`: the build succeeds and
`npm audit` is clean. **The app was not started**, so this is build-verified,
not runtime-verified — exercising the UI once is worth doing.

**One follow-up remains:** Python pin versions were chosen without a package
index available. Every one is at or above its CVE-fixed floor, but `pip-audit`
must confirm resolution.

## Reorganization

**Scanning code.** Five backend modules totalling ~2,600 lines became two
packages split by concern:

```
app/services/scanning/{base,trivy,grype,persistence,registry,events}.py
app/services/scanning/db/{paths,status,process,update,offline}.py
app/routers/scan/{targets,images,events,registry,scanner_db,reports}.py
app/schemas/scan.py
frontend/src/features/scan/{api.js,hooks/,components/}
```

Routes, paths, the OpenAPI tag and the frontend route are unchanged — this was a
move-and-split, not a rewrite. Two structural points: the scan endpoints were
the only feature keeping its Pydantic models inline instead of in
`app/schemas/`, and `ScanPage.jsx` was six components in one 661-line file (now
259 lines of layout). Scanning also went from **zero tests to 3 test modules**,
which is what splitting `scanners.py` bought — the parsers and the webhook
signature check are now reachable without a session or a scanner binary.

`app/services/nexus_security.py` was left alone: despite the name it grants
Nexus anonymous browse access and is not part of this feature.

**This directory.** `findings/` is grouped by severity, `Reports/` is lowercase
`reports/`, and dependency CVEs live in `cve/`. `README.md` also drops the
line-numbers rule it stated but none of the sixteen findings followed —
symbol names are cited instead, since they do not go stale.

## What this pass did not do

- **No containers, no scanners, no backend test run** — per the project's
  static-analysis policy. Backend verification was static: byte-compilation and
  full relative-import resolution across all 85 backend modules. The frontend
  was verified by `npm ci` + `npm run build` (dependency resolution and a
  production build, neither of which starts the stack); the app itself was not
  run. The commands to run the backend suite are in the CVE document.
- **No new audit.** This pass remediates the existing sixteen findings; it did
  not go looking for a seventeenth. The code that changed most (the scanning
  reorganization) is a mechanical move and is the obvious candidate for the
  next review.
- **No live penetration testing**, unchanged from the initial audit.
- **The frontend upgrade was not runtime-verified.** React 19 + react-router 8 +
  vite 8 build cleanly and audit clean, but no page was loaded in a browser.
  The React 19 breaking-change scan came back empty, so the expected risk is
  low — but "builds" is not "works", and this is the one change in the pass
  that touches every page rather than one feature.
