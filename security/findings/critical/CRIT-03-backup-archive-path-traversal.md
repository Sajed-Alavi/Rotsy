Vulnerability Name: Path traversal in backup archive repo directory join
Severity: Critical
Affected Component: `backend/app/services/backup_archive.py` — `create_archive` (job `backup_archive`, enqueued from `POST /api/system/backup/archive`)

Description:
`create_archive` sanitizes each individual asset's *path* via `_safe_relpath` before writing it to disk, but builds the per-repository output directory directly from the caller-supplied repo name: `repo_dir = run_dir / repo`. In selective mode, `repo` comes straight from `BackupArchiveRequest.repos` (user input). `pathlib`'s `/` operator silently discards the left operand entirely when the right one is absolute, so `repo="/etc"` collapses `repo_dir` to `/etc` outright; a relative `repo="../../etc"` walks out of the run directory the same way asset paths could before `_safe_relpath` existed.

Root Cause:
Asset-path sanitization was added deliberately (per the module's own docstring) but the repository-name segment — one directory level up from the sanitized asset path — was missed, even though it comes from the same untrusted source (the request body).

Security Impact:
A user with `system:execute` can direct the backup job to write arbitrary downloaded Nexus asset bytes to arbitrary filesystem locations reachable by the backend process, limited only by the process's own filesystem permissions — a write-primitive outside the intended backup volume.

Recommended Fix:
Validate every target repo name is a single, safe path segment (no `/`, `\`, `.`, `..`, or empty) before creating any directory, and reject the whole run if any name fails that check rather than silently substituting a placeholder.

Implementation Status: Fixed — `backend/app/services/backup_archive.py` adds `safe_repo_dirname()` / `InvalidRepositoryName`, called for every `target_repos` entry before any directory is created. Also enforced earlier, at the API boundary: `backend/app/routers/system.py`'s `BackupArchiveRequest` model validator now rejects unsafe repo names with HTTP 400 before the job is even enqueued.

Testing Result: `backend/tests/test_backup_archive.py` — `safe_repo_dirname` rejects `/etc`, `../../etc`, `..`, `.`, embedded separators, and empty strings; accepts normal repo names. `_safe_relpath` regression coverage retained.
