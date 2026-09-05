# Building Rotsy: a DevSecOps console for Nexus, GitHub/GitLab, and SonarQube

**Version:** v1.02 · **Date:** 2026-09-01

---

Sonatype Nexus Repository Manager is where a lot of teams' Docker images live, but Nexus itself tells you almost nothing about what's inside them. Is `myapp:latest` running a base layer with six unpatched CRITICAL CVEs? Nexus won't say. We built **Rotsy** to answer that — a FastAPI + React console that sits in front of Nexus, scans everything it holds with Trivy and Grype, and turns "click into Nexus and hope" into an actual workflow: repository → image → tag → vulnerability report, with backups, RBAC-scoped access, and PDF export for whoever has to hand something to an auditor.

That was v1.0. Since then Rotsy grew a second centrepiece — connect a GitHub or GitLab repository and every push clones the commit, runs SonarQube, and reports back, no CI YAML — and, most recently, a Telegram bot that pushes results to the people who need them instead of waiting to be visited.

This post covers all three, and it's less "here's our feature list" and more "here's what actually went wrong, and how we fixed it" — because the interesting parts of a project like this are rarely the happy path.

## The event-driven scan ledger

The first design question was *when* an image gets scanned. The naive answer — "scan everything, on a timer" — falls apart fast: re-scanning every image nightly means the queue grows without bound as the registry grows, and "was this scanned recently" ends up answered by a cache with a TTL rather than a fact.

Rotsy scans for exactly two reasons: an image was pushed (Nexus webhook, with a polling fallback), or an operator asked. A durable Postgres ledger tracks every image Rotsy has seen and its state — `baseline`, `queued`, `scanned`, `failed` — so nothing is silently re-scanned because a cache flushed or a process restarted. The first time a repository is observed, its existing contents are recorded as baseline and deliberately left unscanned: onboarding a repository with 500 tags doesn't mean 500 scans on day one.

## The bug that taught us cancellation isn't free

A `POST /jobs/{id}/cancel` endpoint already existed. It flipped a status field in Redis to `"cancelled"` and called it done. The docstring claimed "cooperative cancellation — the worker checks the job status between steps." Nothing did. The subprocess running a multi-hundred-megabyte database download kept going, orphaned, while the UI cheerfully reported the job as cancelled.

The fix was to stop pretending: the job runner now tracks the `asyncio.Task` behind every running job and cancels it directly. That's not the whole story, though — cancelling a task that's awaiting a subprocess doesn't kill the subprocess. We had to thread that through every layer that shells out, catching `asyncio.CancelledError`, explicitly killing the child, and *then* re-raising. Skip that and you've only made the bug harder to see: the job vanishes from the UI while the download keeps eating bandwidth.

"Cancel" as a UI affordance is easy. Cancel as a guarantee — this process stops, now — takes real plumbing at every layer that can outlive the request that started it.

## Detective work: why is the progress bar lying?

The database download UI showed "~119 MB total" while the transfer climbed past 250 MB. Not a bug in the progress bar's math — a bug in its *input*. The expected size was a hardcoded constant written down once and never revisited, while the published database had quietly grown past it. The fix: resolve the real size from the image's OCI manifest before downloading, and only fall back to the guess — explicitly labeled "estimated" — when that lookup fails.

The same instinct paid off on a CVE pass. Grype reported six CRITICAL nginx CVEs with fixes at revisions like `1.28.3-r6`, and `apk upgrade` wasn't picking them up. Our base image restricted its package sources to nginx.org's official apk channel, and that channel doesn't carry Alpine's backported security revisions — the `-r6` fix lived in Alpine's `main` repo. The fix was one line, but finding it meant understanding that a CVE's "fixed version" string encodes exactly *which* repository will serve it, and a plausible-looking `apk upgrade` can silently upgrade nothing that matters.

Removing packages beat patching them, too: `curl` and `gnupg` were in the backend image "just in case," invoked by nothing, and accounted for roughly 40 of 102 findings — several with no patch published yet. The only real fix for a vulnerability in a package you don't use is not shipping it.

## The scan that reported success and analyzed nothing

Clone a repo, run `sonar-scanner`, report back — simple enough that we shipped it, watched it report "ANALYSIS SUCCESSFUL" every run, and moved on. Weeks later someone asked why a repository with a few hundred known issues was showing three.

The scanner wasn't failing. It was running from the wrong directory — `/app`, the backend's own working directory, not the temp directory the repository had been cloned into — so it analyzed whatever sliver of source was reachable from there and reported total success, because as far as it knew, it had. The subprocess helper had no `cwd` parameter and silently inherited the caller's. One-line fix; the lesson was that trusting "successful" as a synonym for "correct" is how this hid for weeks. A scan that analyzes the wrong thing and a scan that works look identical from the job queue unless something checks the finding count against expectations.

## Working around a licensing wall without touching the license

SonarQube Community Edition will not analyze more than one branch under a single project — `sonar.branch.name` is rejected outright as a Developer-Edition feature. That's a deliberate limitation, not something to route around by lying to the API. But "only ever analyze one branch per repository" wasn't acceptable either, and buying a license wasn't the point of a self-hosted stack.

The fix reframes the problem: a *branch* doesn't need to share a Sonar project with its siblings, it just needs *a* project. Analyzing a non-default branch now auto-provisions a second Sonar project named for that branch the first time it's analyzed — created, assigned the right quality gate, reused thereafter. Sonar's single-branch limit is fully respected; the workaround just never asks it to do the thing it can't. It also had to work identically whether someone connects one branch or a hundred — a fix that needs a human to provision each new branch by hand isn't a fix.

## A container recreation that quietly emptied a database

Fixing GitLab webhook delivery needed one infrastructure change: an `extra_hosts` entry so the GitLab container could reach the backend, which meant recreating the container. It came back with an empty database — the test project, its users, every token, gone. The bind-mounted volumes should have survived; here they didn't, and the exact mechanism was never fully pinned down.

Nothing externally valuable was lost — the code lives in git, and both branches were re-pushed within minutes. But it's the clearest reminder in this project that "just restart the container to pick up a config change" is not universally safe, and that assuming a bind mount behaves like backup storage is an assumption worth checking rather than making.

## A 403 that looked exactly like a bad token

Getting GitLab webhook registration working took three separate fixes that each looked identical from outside: "the token is wrong."

First, the callback URL pointed at a browser-facing address GitLab's own SSRF protection rejected. Then a registration call read a token from an open, uncommitted transaction, so it saw the *previous* — already invalid — token. With both fixed, registration still failed, now with a clean `403` on a token that had authenticated fine for reads moments earlier: it belonged to a GitLab bot user sitting at Developer role, and Developer can read a repository but not create a webhook.

Three failure modes, three fixes, and from the caller's side every one just looked like "it's not working" — which is exactly why each needed to be reproduced and read out of GitLab's own logs rather than guessed from the error message.

## A shared cache, N concurrent scans, one lock

Trivy's vulnerability database is a BoltDB file, and BoltDB allows exactly one process to hold it open. Invisible with one scan at a time. Very visible the moment two land together — a bulk "scan all" click, or a burst of pushes — and the second process dies with "cache may be in use by another process."

The honest first fix was a global lock. It worked, in that the collision went away. It also meant a hundred queued scans ran one at a time regardless of available CPU, because the lock had no concept of "how many," only "one or none." That trades a crash for a queue — strictly better, but not actually using the machine.

The real fix separates what has to be exclusive from what doesn't: nothing about scanning an image requires touching the canonical database. Each scan checks out one of a small pool of private cache replicas — plain copies, refreshed lazily when the canonical one moves on — and runs against its own file with no lock at all, because no two scans share one. `SCANNER_MAX_CONCURRENCY` (default 4) bounds concurrent scans *and* sizes the replica pool, so raising it is a disk-for-throughput trade rather than a rewrite. The canonical directory still gets a lock, but now only against an update landing mid-refresh.

## A database download that couldn't survive a bad connection

Trivy's Java database is close to a gigabyte, fetched from `ghcr.io`. On a congested link it fails in a specific ugly way — `stream error: PROTOCOL_ERROR`, an HTTP/2 reset unrelated to the artifact. Transient failures are normal; the problem was that `oras` has no concept of resuming a pull. Every retry paid for the same bytes again from zero. A link slow enough to need retries was, by construction, too slow to ever finish one.

Working around a mid-transfer reset without changing the transport was never going to work, so the transport changed. The OCI blob endpoint is ordinary HTTPS, and HTTPS honors `Range`. A small hand-rolled client — resolve the manifest, walk the bearer-token challenge, `GET` the blob with `Range: bytes={already-on-disk}-` — writes to a stable path *outside* any per-job temp directory, specifically so a transfer interrupted by the job itself ending still has something to resume from next time, not just within one job's retry loop. `oras` stayed on as a second attempt until a routine scan of Rotsy's own image turned up CVEs in its embedded dependencies; by then the resumable path had made it a fallback for a fallback, so it came out of the image entirely.

One more bug fell out of this: the first progress tick after a retry sometimes reported "559680.0 MB/s, 0s left." Not a math error — a state error. The killed attempt's partial bytes were still on disk when the retry's timer started, so the first sample saw a full file appear in the time it takes to spawn a process. The fix was cleaning up after the tool that couldn't: wipe the leftover before retrying, so "bytes since last sample" never lies about when they arrived.

## A quality gate that fit no repository

Rotsy provisions its own SonarQube quality gate — block on a new Blocker/Critical issue or new code under 60% covered, report everything else — because Sonar's default fails on *any* new issue regardless of severity. Sixty percent is reasonable for a mature service. It is not reasonable for a repository three weeks old, a legacy import with no tests, or an infra repo that's mostly configuration — and it was the *only* bar, applied everywhere.

The fix generalizes one gate into named presets — Strict (80%), Standard (60%, unchanged default), Relaxed (30%), and Bugs & Vulnerabilities Only — each its own Sonar gate, created on first use. A repository failing its gate for reasons unrelated to code quality can switch presets and have it apply on the next analysis. The interesting part wasn't the preset list; it was making switching genuinely additive. The reconciliation logic that stops "Rotsy Standard" drifting back to Sonar's CAYC defaults now runs per preset, so four gates stay four gates instead of quietly converging into one.

## Access control that stopped at the repository

Every permission answers "what may this user do" — `scan:execute`, `repositories:write`. For repositories and images a second axis already answered "where": access rules scoped per repository and image pattern. Projects never got that axis. `projects:read`/`projects:write` were the only gate and they were global, so anyone holding `projects:read` could see and act on every Project. Hand one person a Project and they'd implicitly gotten every other one.

That's easy to miss precisely because it doesn't look broken: every test passes, and a system this size usually has an admin and an operator, both global anyway — nothing appears to leak until a second team or a contractor needs their own Project and nothing else. The fix mirrors the repository model deliberately rather than inventing something new: a `project_members` join table, three roles (viewer, member, admin), and a rule that no row means no access regardless of global permissions. Same closed-by-default posture, one level up. The seeded `admin` role bypasses membership, for the same reason its repository access mode is pinned `unrestricted`: an administrator locked out of a Project has no way back in through the app.

Closing it meant finding every endpoint that could attach data *to* a Project, not just the obvious `/projects/{id}` ones. The repository-mapping endpoints in the GitHub and GitLab modules take a `project_id` from the request *body*, and none had ever checked who was allowed to write to it — global `projects:write` was the whole check. Missing one would have meant the membership system was enforced everywhere a human would look and nowhere a script would hit first.

## A webhook that quietly hung for fifteen seconds

Reconnecting a GitLab repository started returning `200 OK` after taking noticeably longer than before, with the auto-analyze badge still reading "webhook missing." Not a crash — a success response that hadn't finished the job it implied.

The investigation started with exception handling. `list_repository_branches` caught `GitHubProviderError` but not `GitHubAuthError`, so a missing App private key turned a clean 400 into an unhandled 500. GitLab's router had the same shape of gap in three places with a different missing type: endpoints caught the module's own error but never `httpx.HTTPError`, so a genuine network failure propagated as a 500 instead of "GitLab is unreachable." Reconnect's webhook step ran through a helper with exactly that gap — which is why it spent its whole client timeout hanging, then 200'd on the other half of the operation and swallowed the webhook failure.

A second, unrelated bug surfaced in the same pass: the "already connected" check compared `full_path` alone, unscoped to which GitLab instance it lived on. Two self-managed hosts sharing a `namespace/repo` string collided, and a genuinely new repository was rejected as a duplicate of one living somewhere else. Scoping to `(gitlab_url, full_path)` was the whole fix; finding it needed the false "already connected" to be reported first, because a uniqueness check that looks globally correct doesn't announce which axis it forgot.

None of that explained the timeout — proper exception handling just meant the failure surfaced as a log line instead of vanishing into a 500. That line read `httpcore.ReadTimeout`: the request had reached GitLab, GitLab had accepted it, and then said nothing for fifteen seconds. `docker compose exec gitlab curl http://host.docker.internal:8000/` answered it directly — `Resolving timed out after 5002 milliseconds`. `host.docker.internal` didn't resolve, *despite* the compose file declaring `extra_hosts` for exactly this purpose. The config was correct; the running container wasn't using it. `docker inspect` showed `HostConfig.ExtraHosts` empty, because that container predated the line and `restart: unless-stopped` had faithfully kept the stale container alive across every `docker compose up` since — none of which reapply a compose file to an already-running container.

The lesson sits one layer above the fix: a compose file being correct is not the same claim as a running container reflecting it. Every command that reads *declared* state agreed the setup was right, and none of them could have caught this, because the gap was between the file and the process.

## Pushing results instead of waiting to be visited

Everything above assumes someone opens the dashboard. In practice the people who most need an analysis result — the developer who just pushed, the admin whose backup failed at 3am — are the least likely to be sitting in front of it. So the newest addition is a Telegram bot: an admin links a Telegram account to a Rotsy user, and from chat that person can check which Projects they can reach and at what role, manage membership, and trigger analysis. Report PDFs arrive automatically when a run finishes; analysis, backup, and scanner-database failures raise a notification.

The design constraint was that a chat client must never become a second, weaker authorization system. Every handler re-derives permissions from the same functions the web app uses, on every tap, rather than caching anything at link time — so deactivating a user or dropping their Project membership takes effect on their very next button press, exactly as it would on their next HTTP request.

Two independent reviews of that code found ten real bugs, and the two that mattered most were both about trusting something that looked trustworthy.

The first: Telegram messages are sent with `parse_mode: HTML`, and project names and usernames were being interpolated raw. Telegram's HTML mode supports `<a href>`. A project named `<a href="https://evil.example/login">Rotsy SSO — re-authenticate</a>` would render as a live clickable link, sent by the organisation's own trusted bot, to every other member of that project. The same gap had a quieter failure mode: a project named `Rotsy <v2>` makes Telegram reject the message as malformed, and because the edit call was wrapped in a debug-level catch, tapping that project did *nothing at all* — no error, no log above DEBUG, permanently unreachable.

The second was subtler. The bot identifies a user by chat ID, which is correct for a private chat and completely wrong for a group. Nothing checked which it was — and the documented linking flow is "message the bot, it replies with your chat ID, give that to your admin." Add the bot to a team group, send `/start`, and it hands out the *group's* ID. Link that, and every member of that group — including anyone added later, including people with no Rotsy account at all — acts as whichever user it was linked to. Two fixes: refuse to answer in non-private chats, and reject non-positive chat IDs at the API, since private-chat IDs are always positive and group IDs always negative.

The rest were reliability, and one is worth repeating because it's a pattern rather than an incident. The bot's polling loop had its `getUpdates` call wrapped in a `try`, but the database read at the top of each iteration didn't. A single transient Postgres blip — a restart, a reset pooled connection — would propagate out, end the task, and get swallowed by the shutdown handler. The bot would stop responding to everyone, permanently, **with no log output at all**, while the Settings card still read "Connected." A sibling loop in the same file carries a comment about the identical failure mode biting once before; this reintroduced it fifteen lines away from that warning. Silent, permanent death from a transient error is the failure mode worth designing against, not the transient error itself.

The deployment had a networking twist too: `api.telegram.org` is DNS-blocked on the target network, while every other integration resolves fine. The tempting fix is an environment-wide `HTTPS_PROXY`, which would reroute GitHub, Nexus, SonarQube and every registry pull through whatever happens to be fixing Telegram. Instead the proxy is a setting scoped to the Telegram client alone — the smallest change that solves the actual problem, and the one that doesn't quietly become load-bearing for six other integrations.

One last fix from that review is a good closing note on cost. Report PDFs were being rendered *before* anything checked whether Telegram was configured or had any recipients — an unbounded query over every issue and hotspot in the run, then a multi-page render, discarded milliseconds later on every deployment that doesn't use the bot at all. Resolving recipients first and building the PDF only if someone is listening turned a per-analysis cost into a zero. Work you throw away is still work you paid for.

## What Rotsy does today

- **Vulnerability scanning** — Trivy + Grype against every image, browsable as repository → image → tag → report, with per-CVE detail and PDF export. Bounded concurrency against a private cache replica each; either scanner can be disabled and disappears from scanning, database management, and every job.
- **Code Quality** — GitHub or GitLab repository, analyzed by SonarQube on every push to a watched branch, or on demand. Per-branch Sonar projects auto-provisioned on Community Edition; per-repository quality-gate presets.
- **Smart Insights and Project Health Score** — deterministic comparisons between consecutive analyses, and a documented 0–100 score. No black box.
- **Projects with per-project access control** — membership (viewer/member/admin) decides who sees which Project, layered on top of the existing repository/image access rules.
- **Telegram bot** — Project access checks, membership management, and analysis triggering from chat; automatic report-PDF delivery and failure notifications, with permissions re-derived live on every interaction.
- **Resumable database downloads** — a dropped connection continues from the bytes already on disk.
- **Event-driven, not polled** — scans and analyses fire on push or on demand, never on a blind schedule.
- **RBAC with per-repository access rules** — read/write/delete scoped to individual repos and images.
- **Retention and scheduled backups** — bulk tag cleanup with automatic blob compaction; daily/weekly/monthly/cron `.tar.gz` archives with configurable retention.
- **Real-time job progress** — SSE-streamed, with real cancellation instead of a spinner that lies.
- **Documentation built into the app**, versioned alongside the code.

## Stack

FastAPI + SQLAlchemy (async) + Postgres, a Redis-backed job queue (no Celery — a few hundred lines got us pending → running → done/failed/cancelled with SSE progress), React 19 + Vite, Trivy and Grype, `sonar-scanner` driving SonarQube, GitHub App / GitLab PAT integrations, and the Telegram Bot API over long-polling. Python 3.13, Node 24 LTS.

## Try it

```bash
git clone https://github.com/Sajed-Alavi/Rotsy.git
cd rotsy
cp .env.example .env   # set JWT_SECRET, bootstrap admin credentials, Postgres creds
docker compose up --build
```

Point it at a Nexus instance with at least one Docker repository and it discovers the rest automatically. GitHub, GitLab, SonarQube and Telegram are all optional and all connected the same way, from **Settings → Integrations** — nothing to put in `.env` to try any of them.

**Repository:** [github.com/Sajed-Alavi/Rotsy](https://github.com/Sajed-Alavi/Rotsy)
**License:** custom attribution-required license — see [`LICENSE`](./LICENSE). Use, modification, and redistribution are permitted; the original copyright notice must be retained and not removed, altered, or obscured.
