Vulnerability Name: Plaintext target-Nexus password queued in Redis job payload
Severity: Low
Affected Component: `backend/app/routers/system.py` (`enqueue_sync`), `backend/app/core/jobs.py` (`JobQueue`)

Description:
`SyncRequest.target_password` is stored via `body.model_dump()` directly into the Redis-backed job queue payload for the `sync` job, with no field-level redaction.

Root Cause:
The job queue payload doubles as both the job's execution input and (implicitly) its persisted/inspectable record, with no separation between "what the handler needs" and "what's safe to expose if the payload is ever surfaced."

Security Impact:
Currently low — there's no `/jobs/{id}` detail endpoint or logging path shown to expose raw payloads today — but the credential sits in Redis in plaintext for the job's lifetime, and any future feature that surfaces job payloads (debug endpoint, admin job inspector, payload logging) would leak it.

Recommended Fix:
Redact `target_password` from the persisted/inspectable payload representation (e.g. store it separately or encrypt it at rest the way the Nexus dashboard connection password already is via `config_store.py`), keeping it available to the handler without it being part of any general-purpose "show me this job" surface.

Implementation Status: Deferred (backlog)

Testing Result: Not applicable — no code change made this pass.
