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

Implementation Status: Deferred (backlog) — `.env.example` comment strengthened this pass (see CRIT-02 diff) but no startup-time enforcement was added.

Testing Result: Not applicable — no enforcement code change made this pass.
