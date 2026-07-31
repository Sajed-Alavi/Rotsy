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

Implementation Status: Fixed

`passlib[bcrypt]` removed from `backend/requirements.txt`. `backend/app/core/security.py` calls `bcrypt` directly (`bcrypt.hashpw` / `bcrypt.checkpw`).

Two compatibility details preserved deliberately, since stored hashes were not migrated: passwords are truncated to 72 bytes explicitly (passlib did this silently; raw bcrypt raises), and `verify_password` catches `ValueError`/`TypeError` on a malformed hash and returns `False` rather than turning a failed login into a 500. Existing `$2b$` hashes verify unchanged.

Testing Result:
`backend/tests/test_security.py` — round-trip, `$2b$` format, a hash generated outside the wrapper still verifying, >72-byte and multibyte passwords, and malformed/empty hashes returning False.
