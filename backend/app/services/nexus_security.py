"""Nexus security-API RBAC helpers for per-repository anonymous access.

Wraps the two Nexus REST calls needed to let anonymous users browse/pull a
single repository:
  * ``POST /service/rest/v1/security/privileges/repository-view`` — create a
    scoped browse+read privilege for the repo.
  * ``GET``/``PUT /service/rest/v1/security/roles/nx-anonymous`` — attach that
    privilege to the built-in anonymous role (Nexus has no append endpoint;
    the role must be fetched, merged, and PUT back in full).

Distinct from ``GET /service/rest/v1/security/anonymous`` (the global
enabled/disabled toggle, read in ``routers/metrics.py``) and from
``routers/roles.py`` (the wrapper app's own Postgres-backed RBAC) — neither
of those is touched here. Runs under the app's own Nexus service account
(``NexusClient``, documented as the ``admin``/``nx-admin`` account), so no
separate Nexus-side authorization is required beyond that account already
having admin rights.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.nexus_client import NexusClient

logger = logging.getLogger(__name__)

_ANON_ROLE_ID = "nx-anonymous"


def _privilege_name(repo_name: str) -> str:
    return f"{repo_name}-anon-view"


async def grant_anonymous_access(nexus: NexusClient, repo_name: str, repo_format: str) -> None:
    """Create a repository-view privilege for ``repo_name`` and attach it to
    the built-in ``nx-anonymous`` role.

    Idempotent: a privilege name collision (Nexus 400) is treated as
    already-granted rather than an error. Raises ``RuntimeError`` on any
    other failure so the caller can decide how to surface it — the repo
    itself already exists in Nexus by the time this is called, so this is
    always a partial-success scenario, never a reason to undo the create.

    Known limitation: concurrent repo creations both touching
    ``nx-anonymous`` race on this fetch-merge-PUT (Nexus has no atomic
    "append privilege to role" endpoint). Acceptable for this admin-only,
    low-frequency action.
    """
    priv_name = _privilege_name(repo_name)
    payload = {
        "name": priv_name,
        "description": f"Auto-granted browse+read for {repo_name} (created via dashboard)",
        "repository": repo_name,
        "format": repo_format,
        "actions": ["browse", "read"],
    }
    resp = await nexus.client.post("/service/rest/v1/security/privileges/repository-view", json=payload)
    if resp.status_code in (200, 201, 204):
        pass  # created
    elif resp.status_code == 400:
        # Nexus 400s on a duplicate privilege name; treat as already-granted.
        logger.info("privilege %s already exists (or rejected), treating as idempotent: %s", priv_name, resp.text[:200])
    else:
        raise RuntimeError(f"Nexus rejected the privilege (HTTP {resp.status_code}): {resp.text[:200]}")

    role_resp = await nexus.client.get(f"/service/rest/v1/security/roles/{_ANON_ROLE_ID}")
    if role_resp.status_code != 200:
        raise RuntimeError(f"Could not read {_ANON_ROLE_ID} role (HTTP {role_resp.status_code}): {role_resp.text[:200]}")
    role: dict[str, Any] = role_resp.json()
    privileges: list[str] = list(role.get("privileges") or [])
    if priv_name not in privileges:
        privileges.append(priv_name)
    role["privileges"] = privileges

    put_resp = await nexus.client.put(f"/service/rest/v1/security/roles/{_ANON_ROLE_ID}", json=role)
    if put_resp.status_code not in (200, 204):
        raise RuntimeError(f"Could not update {_ANON_ROLE_ID} role (HTTP {put_resp.status_code}): {put_resp.text[:200]}")
