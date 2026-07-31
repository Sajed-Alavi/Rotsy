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

Implementation Status: Fixed

The `or settings.JWT_SECRET` fallback in `_fernet` (`backend/app/core/config_store.py`) is gone: the key now comes only from `NEXUS_CONFIG_ENCRYPTION_KEY`, which `Settings` makes a required field validated by `_reject_weak_encryption_key` (placeholder / <32 chars) and by the `_check_secret_separation` model validator (must not equal `JWT_SECRET`). `.env.example` ships it as a required `REPLACE_WITH_A_DIFFERENT_OPENSSL_RAND_HEX_32` placeholder.

Operator note carried into `.env.example`: a Nexus password saved under the old JWT_SECRET-derived key will not decrypt under the new one. `decrypt_password` already degrades to `""` on `InvalidToken`, so the upgrade step is to set the key and re-enter the Nexus password once in the dashboard.

Testing Result:
`backend/tests/test_config.py` — missing key, short key, placeholder key, and key-equals-JWT_SECRET all rejected; a distinct key accepted.
