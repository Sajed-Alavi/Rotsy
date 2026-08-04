# Audit log

**Audit Log** records who changed what, and when.

## What is recorded

Mutating actions against tracked resources: repositories, scan targets, roles, users and similar. Each entry carries the actor, the action, the resource type and id, a timestamp, and any relevant detail.

Filter by resource type with the tabs.

## What is not recorded

Reads. The log answers "who changed this?", not "who looked at this?". Recording every read would drown the signal and would itself be a privacy consideration.

## Retention

Entries live in Postgres and are not automatically pruned. If you need them somewhere durable and tamper-evident, export them — anyone with `roles:manage` can read `GET /api/audit`, so a scheduled export with an [API token](/docs/tokens-and-webhooks) scoped to that permission is straightforward.

## A caveat worth stating

This is an application-level audit log. It records what was done *through Rotsy*. Someone acting directly in the Nexus UI, or with the Nexus service account credentials, does not appear here. For a complete picture you need Nexus's own logs as well.
