"""Nexus scheduled tasks: list, run, stop.

Two places already drove this API before there was a service for it:
:func:`app.services.images.trigger_compact` (``/service/rest/v1/tasks`` +
``/tasks/{id}/run``) and :func:`app.services.backup.list_backup_tasks`
(``/service/rest/v1/scheduler/tasks``). Both were internal-only, so the operator
could never see or run a Nexus task from this console — the "Compact blob store"
task in particular is the thing that actually reclaims disk after a delete, and
its absence was reported only as a footnote on a delete response.

Nexus exposes the same data under two paths depending on version and edition.
``/service/rest/v1/tasks`` is the documented one; some builds only answer on
``/service/rest/v1/scheduler/tasks``. Both are tried, in that order, rather than
assuming an edition.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..core.nexus_client import NexusClient

logger = logging.getLogger(__name__)

# Ordered by preference: the documented endpoint first, the legacy/alternate
# scheduler path second.
_LIST_PATHS = ("/service/rest/v1/tasks", "/service/rest/v1/scheduler/tasks")


class TaskUnavailable(RuntimeError):
    """Nexus did not serve its task API (wrong version, edition, or permissions)."""


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """One shape for the UI, whichever endpoint answered.

    Nexus varies the field names across versions (``type`` vs ``typeId``,
    ``lastRunResult`` vs ``lastRunResultState``), so the differences are absorbed
    here rather than in the router or the browser.
    """
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or raw.get("id"),
        "type": raw.get("type") or raw.get("typeId") or "",
        "message": raw.get("message") or "",
        "current_state": raw.get("currentState") or raw.get("state") or "unknown",
        "last_run": raw.get("lastRun"),
        "last_run_result": raw.get("lastRunResult") or raw.get("lastRunResultState") or "",
        "next_run": raw.get("nextRun"),
        "enabled": raw.get("enabled", True),
        "visible": raw.get("visible", True),
        "runnable": (raw.get("currentState") or raw.get("state") or "").upper() in ("WAITING", "OK", ""),
    }


async def list_tasks(nexus: NexusClient) -> dict[str, Any]:
    """Every scheduled task Nexus will admit to.

    Returns the tasks plus the endpoint that answered, so the UI can explain an
    empty list ("Nexus returned no tasks") differently from an unavailable API
    ("this Nexus does not expose the task endpoint").
    """
    last_status = None
    for path in _LIST_PATHS:
        try:
            resp = await nexus.client.get(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("task list via %s failed: %s", path, exc)
            last_status = str(exc)
            continue
        if resp.status_code != 200:
            last_status = f"HTTP {resp.status_code}"
            continue
        body = resp.json() or {}
        # /tasks wraps in {"items": [...]}; /scheduler/tasks returns a bare list.
        items = body.get("items", []) if isinstance(body, dict) else body
        return {"tasks": [_normalise(t) for t in items or []], "source": path, "available": True}

    return {
        "tasks": [],
        "source": None,
        "available": False,
        "reason": (
            "Nexus did not serve its task API "
            f"({last_status or 'no response'}). This is normal on some Nexus OSS builds; "
            "tasks can still be managed in the Nexus UI under Administration → System → Tasks."
        ),
    }


async def run_task(nexus: NexusClient, task_id: str) -> dict[str, Any]:
    """Start a task now, regardless of its schedule."""
    tid = quote(str(task_id), safe="")
    for path in (f"/service/rest/v1/tasks/{tid}/run", f"/service/rest/v1/scheduler/run/{tid}"):
        try:
            resp = await nexus.client.post(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("task run via %s failed: %s", path, exc)
            continue
        if resp.status_code in (200, 204):
            logger.info("Started Nexus task %s", task_id)
            return {"ok": True, "task_id": task_id}
        if resp.status_code == 404:
            continue
        return {"ok": False, "task_id": task_id,
                "error": f"Nexus refused to start the task (HTTP {resp.status_code})"}
    raise TaskUnavailable(f"no run endpoint accepted task {task_id}")


async def stop_task(nexus: NexusClient, task_id: str) -> dict[str, Any]:
    """Ask Nexus to stop a running task.

    Nexus stops tasks cooperatively: the call returns immediately and the task
    winds down at its next checkpoint, so a task can still report RUNNING for a
    short while afterwards. Said here because the UI would otherwise look broken.
    """
    tid = quote(str(task_id), safe="")
    for path in (f"/service/rest/v1/tasks/{tid}/stop", f"/service/rest/v1/scheduler/stop/{tid}"):
        try:
            resp = await nexus.client.post(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("task stop via %s failed: %s", path, exc)
            continue
        if resp.status_code in (200, 204):
            logger.info("Requested stop of Nexus task %s", task_id)
            return {"ok": True, "task_id": task_id,
                    "note": "stop requested — Nexus stops tasks at their next checkpoint"}
        if resp.status_code == 404:
            continue
        return {"ok": False, "task_id": task_id,
                "error": f"Nexus refused to stop the task (HTTP {resp.status_code})"}
    raise TaskUnavailable(f"no stop endpoint accepted task {task_id}")
