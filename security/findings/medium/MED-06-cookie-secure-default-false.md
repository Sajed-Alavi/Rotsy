Vulnerability Name: `COOKIE_SECURE=false` default
Severity: Medium
Affected Component: `.env.example` (`COOKIE_SECURE`), `backend/app/config.py` (`_cookie_kwargs`)

Description:
`.env.example` ships `COOKIE_SECURE=false`, which omits the `Secure` flag from the `access_token`/`refresh_token` httpOnly cookies. The setting is documented as "MUST be true in production behind TLS," but nothing enforces that at startup.

Root Cause:
A convenience default for local HTTP development was left as the example file's default for production deployments too, with only a comment (easy to miss) as the safeguard.

Security Impact:
An operator who deploys the example configuration as-is — even behind a reverse proxy that terminates TLS, if the false value is simply never revisited — ships auth cookies without the `Secure` flag, allowing them to be sent over plaintext HTTP if any part of the path isn't actually TLS-only.

Recommended Fix:
Comment already strengthened this pass; a stronger version would have the app log a prominent startup warning (or refuse to boot) when `COOKIE_SECURE=false` and `FRONTEND_ORIGIN` uses `https://`, since that combination is almost certainly a misconfiguration rather than intentional local HTTP testing.

Implementation Status: Fixed

`.env.example` now ships `COOKIE_SECURE=true`. `Settings._check_cookie_secure` (`backend/app/config.py`) refuses to boot when `COOKIE_SECURE=false` is combined with an `https://` `FRONTEND_ORIGIN` — that pairing is a misconfiguration, not local testing — and logs a prominent startup warning when it is false at all.

Plain-HTTP local development still works: browsers treat `http://localhost` as a secure context, and an all-`http://` origin only warns.

Testing Result:
`backend/tests/test_config.py` — https origin + insecure cookie rejected; http origin + insecure cookie allowed; https origin + secure cookie allowed.
