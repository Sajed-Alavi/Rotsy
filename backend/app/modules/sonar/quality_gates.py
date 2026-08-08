"""Poll a Sonar compute-engine task to completion, then fetch its quality gate.

Sonar analysis finishes asynchronously server-side after sonar-scanner exits;
there is no reliable outbound webhook across editions, so this is polled with
bounded backoff and a hard timeout rather than pushed.
"""

from __future__ import annotations

import asyncio

from .connector import SonarClient

POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 600.0  # 10 minutes


class QualityGateTimeoutError(Exception):
    pass


class QualityGateFailedError(Exception):
    def __init__(self, task_status: str) -> None:
        super().__init__(f"Sonar compute-engine task ended in status {task_status}")
        self.task_status = task_status


async def wait_for_analysis(client: SonarClient, task_id: str) -> None:
    """Block until the compute-engine task reaches a terminal state.

    An ``asyncio.Event`` (a linter's generic preference over a sleep loop)
    doesn't apply here: nothing in this process can set it. The task's status
    only changes on SonarQube's server, reachable solely by asking it — see
    the module docstring on why that's polling, not a webhook.
    """
    elapsed = 0.0
    while elapsed < POLL_TIMEOUT_SECONDS:  # NOSONAR
        status = await client.task_status(task_id)
        if status == "SUCCESS":
            return
        if status in ("FAILED", "CANCELED"):
            raise QualityGateFailedError(status)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
    raise QualityGateTimeoutError(f"analysis task {task_id} did not finish within {POLL_TIMEOUT_SECONDS:.0f}s")


async def fetch_quality_gate(client: SonarClient, project_key: str) -> dict:
    """Quality gate status + conditions, once analysis has finished.

    Raises :class:`SonarError` (from the underlying client) if the fetch
    itself fails — the caller is responsible for turning that into a
    ``failed`` AnalysisRun rather than swallowing it.
    """
    return await client.quality_gate(project_key)
