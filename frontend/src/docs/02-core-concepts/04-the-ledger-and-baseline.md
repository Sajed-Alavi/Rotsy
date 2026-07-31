# The ledger and the baseline

The single most important concept for understanding *why* scans happen when they do.

## The ledger

`scan_image_ledger` is the durable record of every image the system knows about.

| State | Meaning |
|---|---|
| `baseline` | Present before scanning was enabled. Never auto-scanned. |
| `queued` | A scan job is in flight. |
| `scanned` | Scanned successfully. Will not be re-scanned implicitly. |
| `failed` | The last attempt failed; the report carries the reason. |

It lives in Postgres, so a restart, a cache flush or a redeploy cannot resurrect work.

That is a deliberate correction. Deduplication used to live in Redis with a 24-hour TTL, which meant every image in every enabled repository was silently re-scanned once a day — and *everything* was re-scanned whenever Redis restarted. On a registry of any size that is hours of pointless work and a lot of registry traffic.

## The baseline

The first time a repository is observed, everything already in it is written to the ledger as `baseline` — history, deliberately unscanned — and the target is stamped with `baseline_at`.

The consequence is the point: **enabling scanning on a repository holding a thousand images triggers zero scans.** Scaling from 7 projects to 12 adds five baselines, not five repositories' worth of scanning.

Baselined images are not invisible or ignored. They appear in the Images view with a `baseline` badge, and you can scan any of them individually with the Scan button. You just do not get an accidental thundering herd on day one.

## What counts as a new push

A tag re-pushed with new content *is* a new push. The ledger compares manifest digests, not tag names, so `myapp:latest` overwritten with a new build is correctly seen as something new to scan.

## There is no "scan everything" button

Intentionally. The endpoint that used to exist fanned a job out per image per repository, which is exactly the pattern the ledger was introduced to prevent. If you genuinely want to re-scan a set of images, do it from the Images view, where you can see what you are asking for.
