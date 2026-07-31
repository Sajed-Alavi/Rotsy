Vulnerability Name: Nexus-password encryption key silently derived from `JWT_SECRET`
Severity: Medium
Affected Component: `backend/app/core/config_store.py` (`_fernet`)

Description:
The Fernet key used to encrypt the dashboard-stored Nexus admin password at rest is derived from `NEXUS_CONFIG_ENCRYPTION_KEY` if set, otherwise from `JWT_SECRET` (`hashlib.sha256(seed.encode())`), "so the app still boots."

Root Cause:
Two independent secrets with different threat models (session-signing vs. data-at-rest encryption) were allowed to collapse into one when an operator leaves an optional env var unset — a convenience default that quietly reduces defense in depth.

Security Impact:
Leakage of `JWT_SECRET` (e.g. via a config dump, log capture, or the CRIT-02 placeholder-secret scenario before this pass) also lets an attacker decrypt the stored Nexus admin credentials from a database backup, turning one secret compromise into two independent capabilities (forge sessions AND recover the Nexus admin password).

Recommended Fix:
Require `NEXUS_CONFIG_ENCRYPTION_KEY` to be explicitly set in production (same fail-fast pattern as CRIT-02 for `JWT_SECRET`), or at minimum log a startup warning louder than the current comment-only note, and document that the two secrets must be rotated independently.

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
