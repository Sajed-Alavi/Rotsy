# Task manager

**Task Manager** lists Nexus's own scheduled tasks and lets you run or stop them.

## Why you care

The **Compact blob store** task is the one that actually reclaims disk after a delete. Until it runs, deleted images occupy exactly as much space as before. If storage looks unchanged after a cleanup, this is why — run it here.

Nexus does not create a compact task by default. If none exists, create it in Nexus under **Administration → System → Tasks**; it will then appear here.

## Running and stopping

**Run** starts a task immediately, ignoring its schedule. **Stop** requests that a running task halt.

Nexus stops tasks *cooperatively*: the call returns at once and the task winds down at its next checkpoint, so it can legitimately still report `RUNNING` for a while afterwards. That is not the UI failing to refresh.

## If the list is empty

Two different situations, distinguished on the page:

- **"Nexus reports no scheduled tasks"** — the API answered, there is genuinely nothing configured.
- **"Nexus did not serve its task API"** — some Nexus OSS builds do not expose the endpoint. Manage tasks in the Nexus UI instead.

## What happened to Analytics

This page replaces a placeholder called "Analytics & Tasks". The tasks half was real and is now built. The analytics half — bandwidth per repository, top downloads, cache hit rate — was removed rather than implemented: Nexus OSS publishes none of that data, and this app counts no requests. There is no honest source for those numbers, and tiles showing zeros would be worse than no tiles.
