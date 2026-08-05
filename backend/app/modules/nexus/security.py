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

from .connector import NexusClient

logger = logging.getLogger(__name__)

_ANON_ROLE_ID = "nx-anonymous"


def _privilege_name(repo_name: str) -> str:
    return f"{repo_name}-anon-view"


async def grant_anonymous_access(nexus: NexusClient, repo_name: str, repo_format: str) -> dict[str, Any]:
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
    return {"granted": True, "repo": repo_name, "privilege": priv_name}


async def revoke_anonymous_access(nexus: NexusClient, repo_name: str) -> dict[str, Any]:
    """Detach the repo's anonymous privilege from ``nx-anonymous`` and delete it.

    The counterpart to :func:`grant_anonymous_access`, which had none — a
    repository made anonymously readable could not be made private again from
    this app, only in the Nexus UI.

    The role update is the part that matters: once the privilege is off the
    role, access stops. Deleting the now-orphaned privilege is tidy-up, so a
    failure there is reported but not raised.
    """
    priv_name = _privilege_name(repo_name)

    role_resp = await nexus.client.get(f"/service/rest/v1/security/roles/{_ANON_ROLE_ID}")
    if role_resp.status_code != 200:
        raise RuntimeError(f"Could not read {_ANON_ROLE_ID} role (HTTP {role_resp.status_code}): {role_resp.text[:200]}")
    role: dict[str, Any] = role_resp.json()
    privileges: list[str] = list(role.get("privileges") or [])
    if priv_name not in privileges:
        return {"revoked": False, "repo": repo_name,
                "reason": f"{priv_name} is not attached to {_ANON_ROLE_ID}; nothing to revoke"}

    role["privileges"] = [p for p in privileges if p != priv_name]
    put_resp = await nexus.client.put(f"/service/rest/v1/security/roles/{_ANON_ROLE_ID}", json=role)
    if put_resp.status_code not in (200, 204):
        raise RuntimeError(f"Could not update {_ANON_ROLE_ID} role (HTTP {put_resp.status_code}): {put_resp.text[:200]}")

    deleted = False
    try:
        del_resp = await nexus.client.delete(f"/service/rest/v1/security/privileges/{priv_name}")
        deleted = del_resp.status_code in (200, 204)
    except Exception as exc:  # noqa: BLE001 - access is already revoked; this is cleanup
        logger.warning("could not delete orphaned privilege %s: %s", priv_name, exc)

    return {"revoked": True, "repo": repo_name, "privilege": priv_name, "privilege_deleted": deleted}


async def anonymous_overview(nexus: NexusClient) -> dict[str, Any]:
    """The global anonymous toggle plus every repository currently readable anonymously.

    Answers "what is public right now?", which nothing did before: the global
    flag was read only to populate a metrics field, and per-repo grants were
    write-only.

    Repositories are derived from the privileges actually attached to
    ``nx-anonymous`` — the live authorization state — rather than from the
    ``-anon-view`` naming convention alone, so a privilege granted by hand in
    the Nexus UI still shows up.
    """
    out: dict[str, Any] = {"global_enabled": None, "repositories": [], "available": True}

    try:
        resp = await nexus.client.get("/service/rest/v1/security/anonymous")
        if resp.status_code == 200:
            out["global_enabled"] = bool((resp.json() or {}).get("enabled"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read the global anonymous setting: %s", exc)

    try:
        role_resp = await nexus.client.get(f"/service/rest/v1/security/roles/{_ANON_ROLE_ID}")
        if role_resp.status_code != 200:
            out["available"] = False
            out["reason"] = f"could not read the {_ANON_ROLE_ID} role (HTTP {role_resp.status_code})"
            return out
        attached = set((role_resp.json() or {}).get("privileges") or [])
    except Exception as exc:  # noqa: BLE001
        out["available"] = False
        out["reason"] = f"could not read the {_ANON_ROLE_ID} role: {exc}"
        return out

    privileges: list[dict[str, Any]] = []
    try:
        priv_resp = await nexus.client.get("/service/rest/v1/security/privileges")
        if priv_resp.status_code == 200:
            privileges = priv_resp.json() or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not list privileges: %s", exc)

    by_name = {p.get("name"): p for p in privileges}
    repos: list[dict[str, Any]] = []
    for name in sorted(attached):
        priv = by_name.get(name)
        repo = (priv or {}).get("repository")
        if not repo:
            continue
        repos.append({
            "repo": repo,
            "privilege": name,
            "format": (priv or {}).get("format", ""),
            "actions": (priv or {}).get("actions", []),
            "managed_here": name == _privilege_name(repo),
        })

    out["repositories"] = repos
    out["unmapped_privileges"] = sorted(n for n in attached if n not in by_name)
    return out
