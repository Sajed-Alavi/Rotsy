# Building Rotsy: a DevSecOps console for Nexus, GitHub/GitLab, and SonarQube

**Version:** v1.2.0 · **Date:** 2026-08-12

---

Sonatype Nexus Repository Manager is where a lot of teams' Docker images live, but Nexus itself tells you almost nothing about what's actually inside those images. Is `myapp:latest` running a base layer with six unpatched CRITICAL CVEs? Nexus won't say. We built **Rotsy** to answer that question — a FastAPI + React console that sits in front of Nexus, scans everything it holds with Trivy and Grype, and turns "click into Nexus and hope" into an actual security workflow: repository → image → tag → vulnerability report, with scheduled backups, RBAC-scoped access, and PDF export for anyone who needs to hand a report to an auditor.

That was v1.0. Since then Rotsy grew a second centrepiece: connect a GitHub or GitLab repository, and every push clones the commit, runs SonarQube, and reports back — automatically, no CI YAML. This post covers both halves, and it's less "here's our feature list" and more "here's what actually went wrong while building it, and how we fixed it" — because the interesting parts of a project like this are rarely the happy path.

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

## The scan that reported success and analyzed nothing

The second centrepiece — clone a repo, run `sonar-scanner`, report back — sounds simple enough that we shipped it, watched it report "ANALYSIS SUCCESSFUL" on every run, and moved on. Weeks later someone asked why a repository with a few hundred known issues was showing three. The scanner wasn't failing. It was running from the wrong directory — `/app`, the backend's own working directory, not the temporary directory the repository had actually been cloned into — so it dutifully analyzed whatever tiny sliver of source happened to be reachable from there and reported total success, because as far as it knew, it had.

The bug was one missing argument: the subprocess helper that shells out to `sonar-scanner` had no `cwd` parameter, so it silently inherited the caller's. Adding it was a one-line fix. Trusting "successful" as a synonym for "correct" was the actual lesson — a scan that analyzes the wrong thing and a scan that works look identical from the job queue's point of view unless something checks the finding count against expectations.

## Working around a licensing wall without touching the license

SonarQube Community Edition will not analyze more than one branch under a single project — `sonar.branch.name` is rejected outright as a Developer-Edition-and-above feature. That's a real, deliberate limitation, not a bug to route around by lying to the API. But "only ever analyze one branch per repository" wasn't an acceptable answer either, and buying a license wasn't the point of a self-hosted demo stack.

The fix reframes the problem instead of fighting Sonar's licensing: a *branch* doesn't need to share a Sonar project with its repository's other branches, it just needs *a* project. Analyzing a non-default branch now auto-provisions a second Sonar project, named for that branch, the first time it's analyzed — created, assigned the right quality gate, and reused on every later analysis. Sonar's own single-branch limit is still fully respected; the workaround just never asks it to do the thing it can't do. It also had to be genuinely automatic regardless of scale — the explicit requirement was that it work identically whether someone connects one branch or a hundred, since a fix that needs a human to provision each new branch's project by hand isn't really a fix.

## A container recreation that quietly emptied a database

Fixing GitLab webhook delivery required one infrastructure change: telling the standalone GitLab container how to reach the backend at `host.docker.internal`, which meant adding an `extra_hosts` entry and recreating the container to apply it. That recreation came back with an empty database — the test GitLab project, its users, every token, gone. The bind-mounted volumes should have survived a recreation; in this setup, they didn't, and the exact mechanism was never fully pinned down.

Nothing externally valuable was actually lost — the code itself lives in git history, not GitLab, and both branches were re-pushed from the local repository within minutes of noticing. But it's the clearest reminder in this project that "just restart the container to pick up a config change" is not a universally safe operation, and that assuming a bind mount behaves like backup storage is an assumption worth checking, not making.

## A 403 that looked exactly like a bad token

Getting GitLab's webhook registration working end to end took three separate fixes that each looked, from the outside, identical to "the token is wrong": first the callback URL pointed at a browser-facing address GitLab's own SSRF protection rejected; then a webhook-registration call read a token from an open database transaction that hadn't committed yet, so it saw the *previous* (already-invalid) token; and once both of those were fixed, registration still failed — this time with a clean `403 Forbidden` from GitLab itself, on a token that authenticated perfectly fine for read access moments earlier.

The token turned out to belong to a GitLab bot user — the kind auto-created behind a project or group access token — sitting at Developer role on the project. Developer is enough to read a repository; creating a webhook needs Maintainer. Three different failure modes, three different fixes, and from the caller's side every one of them just looked like "it's not working," which is exactly why each one needed to be actually reproduced and read out of GitLab's own logs rather than guessed at from the error message alone.

## A shared cache, N concurrent scans, one lock

Trivy's vulnerability database is a BoltDB file, and BoltDB allows exactly one process to hold it open at a time. That's invisible with one scan at a time. It stops being invisible the moment two scans land together — a bulk "scan all images" click, or a burst of pushes — and the second `trivy image` process fails outright with "unable to initialize cache: cache may be in use by another process."

The honest first fix was a global lock: every Trivy invocation serialized behind one mutex. It worked, in the sense that the collision went away. It also meant a hundred queued scans now ran one at a time regardless of how much CPU the box actually had, because the lock had no concept of "how many" — only "one or none." That traded a crash for a queue, which is strictly better, but it wasn't actually using the machine.

The real fix separates *what has to be exclusive* from *what doesn't*: nothing about scanning an image requires touching the canonical database directly. So each scan instead checks out one of a small pool of private cache-directory replicas — plain copies of the canonical database, refreshed lazily the first time a scan notices the canonical one moved on — and runs against its own file with no lock needed at all, because no two scans ever share one. A configurable `SCANNER_MAX_CONCURRENCY` (default 4) both bounds how many scans run their actual scanner work at once *and* sizes the replica pool, so raising it is a straight disk-for-throughput trade, not a rewrite. The canonical directory itself still gets a lock — but now only against the rare case of a database update landing mid-refresh, not against every scan that happens to run at the same moment as another one.

## A database download that couldn't survive a bad connection

Trivy's Java-support database is close to a gigabyte, fetched from `ghcr.io` via `oras pull`. On a slow or congested link that transfer doesn't just fail slowly — it fails in a specific, ugly way: `stream error: stream ID 1; PROTOCOL_ERROR`, an HTTP/2-level connection reset that has nothing to do with the artifact being broken. The frustrating part wasn't the reset itself, transient network failures are normal. It was that `oras` has no concept of resuming a pull. Every retry — and there were plenty, because a mirror throttling anonymous traffic resets far more than it completes — paid for the same bytes again, from zero, forever. A link slow enough to need retries was, by construction, also too slow to ever finish one.

Working around a mid-transfer reset without changing the transport was never going to work, so the transport changed. The OCI blob endpoint behind a registry is ordinary HTTPS, and HTTPS honors `Range`. What replaced `oras` as the primary path is a small hand-rolled client — resolve the manifest, walk the registry's bearer-token auth challenge if needed, then `GET` the blob with a `Range: bytes={already-on-disk}-` header — writing to a stable path outside any per-job temp directory, specifically so a transfer interrupted by the job itself ending still has something to resume from the next time an update runs, not just within one job's own retry loop. `oras` stayed on as a second attempt for a while, for the rare case a different tool's transport succeeded where the hand-rolled one didn't — until a routine vulnerability scan of Rotsy's own backend image turned up CVEs in `oras`'s own embedded dependencies, at which point the answer was simpler than patching them: the resumable path had made `oras` a fallback for a fallback, not a fallback for anything load-bearing, so it came out of the image entirely.

One more bug came out of building this: the very first progress tick after a retry sometimes reported an obviously impossible speed — "559680.0 MB/s, 0s left." Not a math error, a state error — the previous, killed attempt's partial bytes were still sitting in the output directory when the retry's timer started, so the first sample saw a full partial file appear in the time it takes to spawn a process. The fix wasn't smarter math, it was cleaning up after the tool that couldn't clean up after itself: wipe the leftover file before a retry starts, so "bytes downloaded since the last sample" is never lying about when those bytes actually arrived.

## A quality gate that fit no repository

Rotsy provisions its own SonarQube quality gate — "block on a new Blocker/Critical issue or on new code under 60% covered, report everything else without blocking" — because Sonar's own default gate fails on *any* new issue regardless of severity, which is too blunt to be useful. Sixty percent coverage is a reasonable bar for a mature, well-tested service. It is not a reasonable bar for a repository three weeks old, or a legacy import with no test suite yet, or an infrastructure repo that's mostly configuration — and until now it was the *only* bar, applied identically everywhere, with no path to a different one short of editing the gate directly in SonarQube and hoping Rotsy's own reconciliation logic didn't quietly put it back.

The fix generalizes one fixed gate into a small set of named presets — Strict (80%), Standard (60%, unchanged default), Relaxed (30%), and Bugs & Vulnerabilities Only (no coverage condition at all) — each its own SonarQube gate, created on first use rather than all four upfront. A repository already connected and already failing its gate for reasons that have nothing to do with code quality can switch presets from its own settings and have it take effect on the next analysis, no disconnect-and-reconnect required. The interesting part wasn't the preset list — it was making sure switching presets is genuinely additive: the reconciliation logic that keeps "Rotsy Standard" from drifting back to Sonar's own CAYC defaults on every check now runs per-preset, so four gates stay four gates instead of quietly converging into one.

## What Rotsy actually does today

- **Vulnerability scanning** — Trivy + Grype run against every image, browsable as repository → image → tag → report, with per-CVE detail (installed/fixed version, CVSS) and a **PDF export** button (named for the repo, tag, *and* scanner, so two reports for the same tag never look identical) for sharing a report outside the tool. Scanners run with bounded concurrency against a private cache replica each (see above) instead of colliding on one shared lock, and either scanner can be turned off from Settings — a disabled one disappears from scanning, database management, and every job, not just from new scans.
- **Code Quality** — connect a GitHub or GitLab repository and every push to a watched branch clones, analyzes with SonarQube, and reports back automatically; run it on demand from a global repository/branch picker instead. Per-branch Sonar projects are auto-provisioned transparently on Community Edition (see above), and each repository picks its own quality-gate preset — Strict, Standard, Relaxed, or Bugs & Vulnerabilities Only (see above) — instead of one coverage bar applied to everything.
- **Smart Insights and a Project Health Score** — deterministic, evidence-backed comparisons between consecutive analyses, and a documented 0–100 score per project, no black box.
- **Resumable vulnerability-database downloads** — a dropped connection continues from the bytes already on disk instead of restarting a multi-hundred-megabyte transfer from zero.
- **Event-driven, not polled** — scans and analyses fire on push (webhook or fallback watcher) or on demand, never on a blind schedule.
- **RBAC with per-repository access rules** — read/write/delete scoped down to individual repos and images, not just role-level all-or-nothing.
- **Cleanup/retention policies** — scheduled bulk deletion of old image tags (`keep last N` per image, or an age cutoff), with the disk-reclaiming "Compact blob store" task triggered automatically after a successful run — not just a rule that removes tags and leaves the blobs behind.
- **Scheduled, compressed backups** — daily/weekly/monthly/cron cadence, configurable retention, `.tar.gz` archives instead of raw directory copies.
- **Real-time job progress** — SSE-streamed progress for scans, analyses, database downloads and backups, with actual cancellation (see above) instead of a spinner that lies to you.
- **PDF export for both scanning and code analysis** — a SonarQube report's PDF includes a Suggested Fixes table pulled live from SonarQube's own rule documentation, not just a dump of findings.
- **A documentation section built into the app itself** — installation, configuration reference, troubleshooting, upgrade notes, all versioned alongside the code.

## Stack

FastAPI + SQLAlchemy (async) + Postgres on the backend, a Redis-backed job queue (no Celery — a few hundred lines got us pending → running → done/failed/cancelled with SSE progress, which was all we needed), React 19 + Vite on the frontend, Trivy and Grype for scanning, `sonar-scanner` driving a SonarQube instance for code analysis, GitHub App / GitLab PAT integrations for source control. Python 3.13, Node 24 LTS.

## Try it

```bash
git clone https://github.com/Sajed-Alavi/Rotsy.git
cd rotsy
cp .env.example .env   # set JWT_SECRET, bootstrap admin credentials, Postgres creds
docker compose up --build
```

Point it at a Nexus Repository Manager instance with at least one Docker repository, and it discovers repositories automatically — no per-repo config required to get started. GitHub, GitLab, and SonarQube are entirely optional and connected the same way, from **Settings → Integrations** — nothing to set up in `.env` to try Code Quality either.

**Repository:** [github.com/Sajed-Alavi/Rotsy](https://github.com/Sajed-Alavi/Rotsy)
**License:** custom attribution-required license — see [`LICENSE`](./LICENSE). Use, modification, and redistribution are permitted; the original copyright notice must be retained and not removed, altered, or obscured.
