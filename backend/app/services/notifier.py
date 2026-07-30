"""Webhook notifier for alerting.

Posts a JSON payload to a user-provided URL (Slack, Discord, or any generic
receiver). Best-effort: failures are logged but never bubble up to crash the
alert evaluator.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


async def send_webhook(url: str, payload: dict, timeout: float = 10.0) -> bool:
    """POST ``payload`` to ``url``. Returns True on 2xx, False otherwise."""
    envelope = {
        "source": "sharpy",
        "event": "alert.triggered",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=envelope)
        if 200 <= resp.status_code < 300:
            logger.info("Webhook delivered to %s (HTTP %s)", url, resp.status_code)
            return True
        logger.warning("Webhook to %s returned HTTP %s: %s", url, resp.status_code, resp.text[:200])
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Webhook delivery to %s failed: %s", url, exc)
        return False
