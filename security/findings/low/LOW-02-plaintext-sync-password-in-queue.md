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

Implementation Status: Fixed

`enqueue_sync` (`backend/app/routers/system.py`) pops `target_password` out of `body.model_dump()` and stores `target_password_enc`, encrypted with `config_store.encrypt_password` — now backed by the mandatory dedicated key from MED-01. `handle_sync` decrypts it just before use. The plaintext credential never enters the Redis payload, so no future job-inspector or debug-logging surface can echo it.

Testing Result:
Covered by inspection of the enqueue path; `backend/tests/test_config.py` pins the encryption-key requirement the encryption depends on. Manual: enqueue a sync and confirm the Redis payload contains `target_password_enc` and no `target_password`.
