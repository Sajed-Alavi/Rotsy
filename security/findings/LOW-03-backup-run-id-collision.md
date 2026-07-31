Vulnerability Name: Backup `run_id` is timestamp-only — same-second collision under concurrency
Severity: Low
Affected Component: `backend/app/services/backup_archive.py` — `_new_run_id`

Description:
`_new_run_id` returns `time.strftime("%Y%m%d-%H%M%S")` — a one-second-resolution timestamp with no additional entropy or uniqueness check. Two `backup_archive` jobs started within the same second produce the same `run_id`, and thus the same `run_dir`.

Root Cause:
No concurrency guard assumes only one backup archive job runs at a time, but nothing in `handle_backup_archive`/the job queue actually enforces that.

Security Impact:
Not a security vulnerability on its own, but a data-integrity bug: two concurrent runs writing into the same directory can interleave writes and corrupt each other's `manifest.json` and asset files, silently producing a backup that looks successful but is corrupted — a availability/integrity concern for a feature whose entire purpose is disaster recovery.

Recommended Fix:
Append a short random suffix or an incrementing sequence to `run_id` (e.g. `time.strftime(...) + "-" + secrets.token_hex(3)`), or take a lock (DB row or Redis) preventing concurrent `backup_archive` jobs from running simultaneously.

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
