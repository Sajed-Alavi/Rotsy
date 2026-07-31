Vulnerability Name: Backup free-space check has a TOCTOU gap
Severity: Low
Affected Component: `backend/app/services/backup_archive.py` — `_ensure_disk_space` / `create_archive`

Description:
Free disk space is checked once before the run starts and then only every `_DISK_CHECK_EVERY` (50) assets, not before every write. Between checks, a run can still write past the configured `BACKUP_MIN_FREE_BYTES` threshold if assets are large enough or numerous enough within one 50-asset window.

Root Cause:
The check-then-act pattern has an inherent time-of-check/time-of-use gap, widened intentionally for performance (checking `shutil.disk_usage` on every asset would be wasteful) but without a bound on how much can be written within one window (e.g. large Docker layer blobs).

Security Impact:
Availability risk, not a compromise: a backup run can still fill the volume to zero in the gap between checks, contrary to the feature's own stated purpose ("abort a run rather than fill the volume to zero").

Recommended Fix:
Track cumulative bytes written within the current check window and force an out-of-band disk check if that exceeds some threshold (e.g. every N bytes, not just every N assets), so a burst of large assets can't outrun the periodic check.

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
