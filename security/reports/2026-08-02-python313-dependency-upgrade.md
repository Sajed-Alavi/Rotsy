# Python 3.13 + Node LTS + dependency refresh — 2026-08-02

Follow-up to [2026-07-31-full-remediation.md](2026-07-31-full-remediation.md),
which left one open item: "Python pin versions were chosen without a package
index available... `pip-audit` must confirm resolution." This pass had network
access, so every backend and frontend pin was checked against its current
published release rather than chosen conservatively offline.

## Scope

1. **Runtime upgrade**: `python:3.11.11-slim-bookworm` → `python:3.13.14-slim-bookworm`;
   `node:22.23.2-alpine` → `node:24.16.0-alpine` (current LTS, "Krypton").
2. **Dependency refresh**: every pin in `backend/requirements.txt`,
   `backend/requirements-dev.txt`, and `frontend/package.json` re-checked
   against the current PyPI/npm release.
3. Two new backend dependencies for this task's features: `croniter` (scheduled
   backup cron expressions) and `reportlab` (vulnerability report PDF export).

Full before/after version table in
[../cve/dependency-cve-review.md §4](../cve/dependency-cve-review.md#4-2026-08-02-pass--python-313--dependency-refresh).

## Python 3.13: wheel-availability risk, checked before committing to the bump

The one thing that could have forced a different plan was a pinned dependency
with no `cp313` wheel — that forces a source build inside the image, which
needs a Rust/C toolchain this Dockerfile does not install. Checked directly
against each package's published file list: `pydantic-core` (transitive via
`pydantic`), `asyncpg`, `psycopg[binary]`, `bcrypt`, and SQLAlchemy's
`greenlet` all ship `cp313` wheels at the pinned versions. No toolchain
changes were needed in `backend/Dockerfile`.

## Deliberately deferred: three majors, one reason each

Not every "latest" was taken. Three packages stay behind their newest major
release because the newer major changes behavior this app depends on, and
none of it can be confirmed without a runtime/browser test pass — out of
scope for a static-edit dependency refresh:

| Package | Stayed at | Newest major | Why deferred |
|---|---|---|---|
| `redis` | 5.2.1 | 8.x | RESP3-default and sync/async type-overload changes could alter what `app/core/cache.py` gets back from Redis. |
| `sse-starlette` | 2.3.6 | 3.x | Changed task-group/`ExceptionGroup` semantics around stream shutdown — `app/core/sse.py` needs its own pass first. |
| `tailwindcss` | 3.4.17 | 4.x | Not a compatible bump: v4 replaces `tailwind.config.js` with CSS-first `@theme` config and swaps the PostCSS plugin package, touching every styled page. |

Each is a tracked follow-up, not an oversight — see the CVE review for the
verification bar each one needs before it can move.

`starlette` did cross its 1.0 boundary (0.47.2 → 1.3.1): its only breaking
change is removing the old `on_startup`/`on_shutdown` hooks in favor of the
`lifespan` context-manager style that `backend/app/main.py` already uses
exclusively, so this one crossing carried no code risk.

## What this pass did not do

- **No containers, no test run, no `npm install`/`npm ci`** — per this
  session's static-analysis policy. `package-lock.json` still reflects the old
  `package.json` and needs regenerating.
- **No live `pip-audit` or `npm audit` run** — the commands are below; every
  version chosen is the current published release, but "current" is not the
  same claim as "audited clean," and that step is not optional.
- **No browser verification of the Node/frontend runtime bump.**

## Verification (run these; not run in this pass)

```bash
# Backend: confirm no source builds trigger under 3.13, then audit.
docker build --target base -t sharpy-backend-py313-check backend/
docker run --rm sharpy-backend-py313-check pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
docker compose run --rm backend pip-audit -r requirements.txt
docker compose run --rm backend pytest

# Frontend: regenerate the lockfile against the new package.json, then audit + build.
cd frontend && npm install && npm audit && npm run build
```
