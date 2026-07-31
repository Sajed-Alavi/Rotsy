Vulnerability Name: Vulnerable JWT library `python-jose==3.3.0`
Severity: High
Affected Component: `backend/requirements.txt`, `backend/app/core/security.py`

Description:
`python-jose==3.3.0` (used for every JWT sign/verify operation in the app) is affected by CVE-2024-33663 (JWT algorithm-confusion) and CVE-2024-33664 (denial-of-service via crafted JWE decompression), and the project has had no newer release addressing them.

Root Cause:
A dependency in a security-critical path (session token issuance/verification) was pinned to a version with known, unfixed CVEs and no maintained upgrade path within the same library.

Security Impact:
This sits directly on the authentication boundary — every request's session is validated through this code path (`app/dependencies.py`, `app/routers/auth.py`). A library-level flaw here has outsized impact compared to the same class of bug elsewhere.

Recommended Fix:
Migrate to `PyJWT`, which is actively maintained and has no equivalent open CVEs, keeping the same token payload shape and `TokenError` contract so calling code needs no changes.

Implementation Status: Fixed — `backend/app/core/security.py` now uses `pyjwt` (`import jwt`, `jwt.PyJWTError`) instead of `jose`; `_create_token`/`decode_token` keep the same signature and payload shape (`sub`, `type`, `iat`, `exp`), and `decode_token` still passes `algorithms=[settings.JWT_ALGORITHM]` explicitly (PyJWT rejects `alg: none` by default). `backend/requirements.txt`: `python-jose[cryptography]==3.3.0` → `PyJWT==2.9.0`.

Testing Result: `backend/tests/test_security.py` — access/refresh token round-trip, wrong-type rejection, tampered-signature rejection, expired-token rejection, cross-secret rejection, password hash/verify.
