Vulnerability Name: Path traversal → confused-deputy SSRF via asset download proxy
Severity: Critical
Affected Component: `backend/app/routers/repositories.py` — `download_asset` (`GET /api/repositories/{name}/assets/download`)

Description:
The endpoint streams a Nexus asset to the browser by concatenating the caller-supplied `path` query parameter directly into the upstream request URL: `url = f"/repository/{name}{path}"`. httpx normalizes `..` segments when it merges a relative URL against `nexus.client.base_url`, so a crafted `path` (e.g. `/../../../../service/rest/v1/security/users`) escapes the `/repository/{name}/` prefix entirely and reaches arbitrary Nexus REST API endpoints.

Root Cause:
No containment check was applied after string concatenation — the code assumed `path` always stayed under the intended repository prefix because the UI only ever sends well-formed values, not because the server enforced it.

Security Impact:
The backend authenticates to Nexus with its own privileged service account (the user never has Nexus credentials directly — that's the whole point of the proxy). A user holding only `repositories:read` can use this to make the backend issue authenticated requests to any Nexus REST endpoint — including admin-only ones like user/role management — and have the response streamed back to them. It also defeats the per-image RBAC scope check at the same endpoint, since that check resolves the "owning image" from the same raw, unvalidated path.

Recommended Fix:
Normalize `f"/repository/{name}{path}"` with `posixpath.normpath` and reject the request (400) unless the result still starts with `/repository/{name}/`.

Implementation Status: Fixed — `backend/app/routers/repositories.py`, new `_validated_repository_path()` helper, called before any Nexus request is made.

Testing Result: `backend/tests/test_asset_path_guard.py` — covers a normal path (allowed), several `../`-style escape attempts (all rejected with HTTP 400).
