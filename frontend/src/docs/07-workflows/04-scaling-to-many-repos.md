# Workflow: scaling to many repositories

Going from a handful of repositories to dozens without the system falling over.

## Onboarding is cheap by design

Enabling a repository baselines it: everything already there is recorded as history and **not** scanned. Enabling a repository with a thousand images triggers zero scans.

So going from 7 projects to 12 costs five baselines, not five repositories' worth of scanning. Onboard as many as you like in one sitting.

## Prefer webhooks over polling

With many repositories, the fallback watcher's cost grows linearly — it lists every enabled repository's components every `SCAN_PUSH_POLL_SECONDS`.

Configure the Nexus webhook and set `SCAN_PUSH_POLL_SECONDS=0`. Webhooks react in seconds instead of up to a minute, and the load is proportional to actual pushes rather than to repository count.

## Scope roles as you grow

With one team, everyone seeing everything is fine. With twelve, it is not.

Use access rules to restrict roles to their own images. One rule with a repository wildcard — `prod-*` / `abrisham*` — covers every matching repository, including ones created later, so the configuration does not grow with the repository count. Remember to set the other roles those users hold to `scoped`, or the scoping does nothing. See [the permission model](/docs/permission-model).

## Retention becomes mandatory

Manual deletion does not scale. Write retention policies per repository, preview them, then let the daily sweep run.

Make sure a **Compact blob store** task exists and runs on a schedule in Nexus. Retention that deletes components without compaction frees no disk at all, and you will conclude retention is broken when it is working perfectly.

## Watch the storage trend, not the number

**Metrics** tracks size over time. Set an alert on `blobstore.used_pct` so you find out at 75% rather than at 100%. A blobstore that fills up takes writes down for every repository sharing it.

## Keep the databases fresh

More repositories means more scans means more exposure to a stale database. Set `SCANNER_DB_UPDATE_AT` to a quiet hour and check the Database Management page occasionally — a `stale` badge on a busy instance means many scans were run against old data.
