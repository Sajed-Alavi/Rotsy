Vulnerability Name: Mass assignment via `RepoCreate.extra` overriding validated fields
Severity: Medium
Affected Component: `backend/app/routers/repositories.py` — `_build_repo_payload` / `RepoCreate.extra` (`POST /api/repositories`)

Description:
`_build_repo_payload` assembles a validated Nexus create-repository payload from typed fields, then merges `body.extra` (an arbitrary caller-supplied dict) in last: `payload.update(body.extra)`. Because it's merged last, any key in `extra` silently overrides the corresponding validated field.

Root Cause:
The "escape hatch for additional Nexus fields" was implemented as an unrestricted dict merged after validation instead of being restricted to keys that aren't already covered by `RepoCreate`'s typed fields.

Security Impact:
A user with `repositories:write` can post `{"name": "x", "format": "docker", "type": "hosted", "extra": {"storage": {"blobStoreName": "other-teams-store", "writePolicy": "ALLOW"}}}` to silently repoint the new repository at a different blob store than the validated `blob_store` field implies, or override other fields the API surface suggests are controlled.

Recommended Fix:
Reject `extra` keys that collide with any top-level key already set by `_build_repo_payload` (`name`, `online`, `storage`, `docker`, `proxy`, `negativeCache`, `httpClient`, `group`), so it can only add genuinely new fields, not override validated ones.

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
