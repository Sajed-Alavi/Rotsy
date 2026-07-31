Vulnerability Name: Unmaintained `passlib==1.7.4`
Severity: Low
Affected Component: `backend/requirements.txt`

Description:
`passlib` (used for password hashing via `CryptContext(schemes=["bcrypt"])` in `backend/app/core/security.py`) has had no release since 2020 and has known interop friction with modern `bcrypt` releases.

Root Cause:
A dependency in the password-hashing path is unmaintained; no active CVE is known against it today, but the lack of maintenance means future `bcrypt` interop issues or newly discovered flaws won't get a fix.

Security Impact:
Low today (bcrypt hashing itself is still sound and `bcrypt==4.1.2` is pinned separately and current), but risk increases the longer this goes unaddressed without an active maintainer to respond to future findings.

Recommended Fix:
Migrate password hashing to call `bcrypt` directly (a thin wrapper, since `passlib` is only used for this one `CryptContext`), removing the unmaintained dependency entirely, or switch to a maintained alternative like `argon2-cffi`.

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
