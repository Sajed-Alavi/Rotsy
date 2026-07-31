Vulnerability Name: Weak/placeholder bootstrap secrets accepted silently
Severity: Critical
Affected Component: `backend/app/config.py`, `.env.example`, `backend/app/db/seed.py`

Description:
`.env.example` ships copy-paste-ready values for two security-critical settings: `JWT_SECRET=generate-a-32-byte-hex-secret` and `BOOTSTRAP_ADMIN_PASSWORD=change-me`. Nothing in the application stopped an unedited copy of this file from reaching a running deployment.

Root Cause:
`Settings` (pydantic-settings) validated these fields only for presence and type, not for whether they were still the documented placeholder. `seed.py` then creates the bootstrap admin account with whatever `BOOTSTRAP_ADMIN_PASSWORD` resolves to, with no check against the well-known example value.

Security Impact:
`JWT_SECRET` signs every access/refresh token; a fixed, publicly-documented value lets anyone forge a valid session for any user, including admin, without ever authenticating. `BOOTSTRAP_ADMIN_PASSWORD=change-me` paired with `BOOTSTRAP_ADMIN_USERNAME=admin` is a public credential pair, not a secret, and grants full `users:manage`/`roles:manage` rights the moment it's seeded.

Recommended Fix:
Fail startup (not just log a warning) when `JWT_SECRET` or `BOOTSTRAP_ADMIN_PASSWORD` match a known-placeholder value or fall under a minimum length. Replace `.env.example`'s values with unmistakably-fake placeholders so an unedited deploy fails loudly instead of booting insecurely.

Implementation Status: Fixed — `backend/app/config.py` adds `_reject_placeholder_jwt_secret` (rejects placeholders and anything under 32 chars) and `_reject_placeholder_admin_password` (rejects placeholders and anything under 12 chars) as pydantic `field_validator`s, so `Settings()` construction raises `ValidationError` at startup. `.env.example` placeholders changed to `REPLACE_WITH_OPENSSL_RAND_HEX_32` / `REPLACE_WITH_A_STRONG_PASSWORD` (also applied to `NEXUS_PASSWORD`, `DATABASE_URL`'s embedded password, and `POSTGRES_PASSWORD` for consistency, though those aren't independently validated by `Settings`).

Testing Result: `backend/tests/test_config.py` — placeholder and under-length values for both fields raise `ValidationError`; realistic values are accepted.
