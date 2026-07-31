Vulnerability Name: SSRF via unrestricted sync `target_base_url`
Severity: Medium
Affected Component: `backend/app/routers/system.py` (`SyncRequest.target_base_url`), `backend/app/services/job_handlers.py` (`handle_sync`)

Description:
`POST /api/system/sync` accepts an arbitrary `target_base_url` with no destination validation, and `handle_sync` uses it directly as the base URL for outbound requests carrying `target_username`/`target_password`.

Root Cause:
Same pattern as MED-04 — a caller-supplied destination URL is trusted outright because the feature's intended use (syncing to another real Nexus instance) doesn't anticipate a malicious value from an otherwise-authorized user.

Security Impact:
A user with `system:execute` can point a sync job at an internal-only host, causing the backend to send outbound requests (potentially including credentials from the request body) to a destination the attacker chose, from the backend's network position.

Recommended Fix:
Same as MED-04: enforce `http`/`https` scheme and block private/link-local/metadata address ranges by default, with an explicit admin override for legitimate on-prem targets.

Implementation Status: Fixed

Same guard as MED-04. `SyncRequest.target_base_url` (`backend/app/routers/system.py`) gets a `field_validator` calling `validate_outbound_url`, and `handle_sync` (`backend/app/services/job_handlers.py`) re-validates at dispatch time before any outbound request — which also covers jobs queued before the guard existed.

Testing Result:
`backend/tests/test_outbound.py` covers the shared guard. Manual: `POST /api/system/sync` with `target_base_url: http://127.0.0.1:8000` must return 400.
