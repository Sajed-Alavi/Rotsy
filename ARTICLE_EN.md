# Building Rotsy: a security console for Nexus Repository Manager

**Version:** v1.0.0 · **Date:** 2026-08-04

---

Sonatype Nexus Repository Manager is where a lot of teams' Docker images live, but Nexus itself tells you almost nothing about what's actually inside those images. Is `myapp:latest` running a base layer with six unpatched CRITICAL CVEs? Nexus won't say. We built **Rotsy** to answer that question — a FastAPI + React console that sits in front of Nexus, scans everything it holds with Trivy and Grype, and turns "click into Nexus and hope" into an actual security workflow: repository → image → tag → vulnerability report, with scheduled backups, RBAC-scoped access, and PDF export for anyone who needs to hand a report to an auditor.

This post is less "here's our feature list" and more "here's what actually went wrong while building it, and how we fixed it" — because the interesting parts of a project like this are rarely the happy path.

## The event-driven scan ledger

The first design question was: *when* does an image get scanned? The naive answer — "scan everything, on a timer" — falls apart fast. Re-scanning every image in every repository nightly means the scan queue grows without bound as your registry grows, and it means "did this image get scanned recently" is answered by a cache with a TTL rather than a real fact.

Rotsy scans for exactly two reasons: an image was pushed (via a Nexus webhook, with a polling fallback for setups that can't wire up webhooks), or an operator asked. A durable Postgres ledger (`ScannedImage`) tracks every image Rotsy has ever seen and its state — `baseline`, `queued`, `scanned`, `failed` — so nothing gets silently re-scanned because a cache flushed or a process restarted. The first time a repository is observed, its *existing* contents are recorded as baseline history and deliberately left unscanned — onboarding a repository with 500 existing tags doesn't mean 500 scans on day one.

## The bug that taught us cancellation isn't free

A `POST /jobs/{id}/cancel` endpoint already existed before we touched it. It flipped a status field in Redis to `"cancelled"` and called it done. The docstring even said "cooperative cancellation — the worker checks the job status between steps." Nothing did. The subprocess running a multi-hundred-megabyte database download kept running, orphaned, in the background, while the UI cheerfully reported the job as cancelled.

The fix was to stop pretending cancellation was cooperative and make it real: the job runner now tracks the `asyncio.Task` behind every running job, and `cancel()` calls `.cancel()` on it directly. That's not the whole story, though — cancelling an `asyncio.Task` that's awaiting a subprocess doesn't kill the subprocess. We had to thread that through every layer that shells out (the Trivy/Grype/`oras` download loops), catching `asyncio.CancelledError`, explicitly killing the child process, and *then* re-raising. Skip that step and you've just made the bug harder to see — the job disappears from the UI while the download keeps eating bandwidth in the background.

Lesson: "cancel" as a UI affordance is easy. Cancel as an actual guarantee — this process stops, now, and doesn't restart on its own — takes real plumbing at every layer that can outlive the request that started it.

## Detective work: why is the progress bar lying?

A user reported the database download UI showing "~119 MB total" while the actual transfer kept climbing past 250 MB. The number wasn't a bug in the progress bar's math — it was a bug in its *input*. The expected download size was a hardcoded constant (`TRIVY_DB_MB = 50`) written down once and never revisited, while the real published database had quietly grown well past that. The fix: resolve the *actual* size from the image's OCI manifest before downloading — the same technique already used for the other scanner — and only fall back to the hardcoded guess (explicitly labeled "estimated" in the UI) when that lookup fails.

The same debugging instinct paid off again on a CVE remediation pass. A Grype scan of our frontend image reported six CRITICAL nginx CVEs with published fixes at package revisions like `1.28.3-r6`. `apk upgrade` wasn't picking them up. The reason: our base image restricted its own package sources to nginx.org's official apk channel, and *that* channel doesn't carry Alpine's own backported security-revision bumps — the `-r6` fix lived in Alpine's `main` repo, not nginx.org's. The fix was one line — `--repository=https://.../alpine/latest-stable/main` scoped to that one install — but finding it meant understanding that a CVE's "fixed version" string encodes exactly *which* repository will actually serve it, and a plausible-looking `apk upgrade` can silently upgrade nothing that matters.

Removing packages turned out to be a bigger win than patching them, too: `curl` and `gnupg` were installed in the backend image "just in case," invoked by nothing. They accounted for roughly 40 of a 102-finding scan, several with no patch published yet for the installed version. The only real fix for a vulnerability in a package you don't use is not shipping the package.

## What Rotsy actually does today

- **Vulnerability scanning** — Trivy + Grype run against every image, browsable as repository → image → tag → report, with per-CVE detail (installed/fixed version, CVSS) and a **PDF export** button for sharing a report outside the tool.
- **Event-driven, not polled** — scans fire on push (webhook or fallback watcher) or on demand, never on a blind schedule.
- **RBAC with per-repository access rules** — read/write/delete scoped down to individual repos and images, not just role-level all-or-nothing.
- **Scheduled, compressed backups** — daily/weekly/monthly/cron cadence, configurable retention, `.tar.gz` archives instead of raw directory copies.
- **Real-time job progress** — SSE-streamed progress for scans, database downloads and backups, with actual cancellation (see above) instead of a spinner that lies to you.
- **A documentation section built into the app itself** — installation, configuration reference, troubleshooting, upgrade notes, all versioned alongside the code.

## Stack

FastAPI + SQLAlchemy (async) + Postgres on the backend, a Redis-backed job queue (no Celery — a few hundred lines got us pending → running → done/failed/cancelled with SSE progress, which was all we needed), React 19 + Vite on the frontend, Trivy and Grype for scanning. Python 3.13, Node 24 LTS.

## Try it

```bash
git clone https://github.com/Sajed-Alavi/Rotsy.git
cd rotsy
cp .env.example .env   # set JWT_SECRET, bootstrap admin credentials, Postgres creds
docker compose up --build
```

Point it at a Nexus Repository Manager instance with at least one Docker repository, and it discovers repositories automatically — no per-repo config required to get started.

**Repository:** [github.com/Sajed-Alavi/Rotsy](https://github.com/Sajed-Alavi/Rotsy)
**License:** custom attribution-required license — see [`LICENSE`](./LICENSE). Use, modification, and redistribution are permitted; the original copyright notice must be retained and not removed, altered, or obscured.
