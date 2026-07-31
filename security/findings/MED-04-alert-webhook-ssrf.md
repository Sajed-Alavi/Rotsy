Vulnerability Name: SSRF via unrestricted alert `webhook_url`
Severity: Medium
Affected Component: `backend/app/routers/alerts.py` (`AlertCreate`/`AlertUpdate.webhook_url`), `backend/app/services/alerting.py`

Description:
`webhook_url` accepts any string 8–512 characters with no scheme/host allow-list before the backend dispatches a server-side POST to it whenever the alert fires.

Root Cause:
No validation of the webhook destination beyond length — the field is trusted as "a URL an operator wants notified," not treated as a potential SSRF vector into the backend's own network position.

Security Impact:
A user with `alerts:write` can set `webhook_url` to an internal-only address (cloud metadata endpoint, internal admin panel, etc.) and have the backend — which may have network access the attacker doesn't — issue requests to it whenever the alert condition is met.

Recommended Fix:
Validate `webhook_url` at creation time: require `http`/`https` scheme, and by default block loopback/link-local/private (RFC1918) and cloud metadata (`169.254.169.254`) address ranges unless explicitly allow-listed by an admin for on-prem use cases.

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
