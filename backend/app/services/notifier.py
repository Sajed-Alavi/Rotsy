"""Webhook notifier for alerting.

Posts a JSON payload to a user-provided URL (Slack, Discord, or any generic
receiver). Best-effort: failures are logged but never bubble up to crash the
alert evaluator.

The destination is re-checked against :mod:`app.core.outbound` here, not only
when the rule was created. Two reasons this is not redundant: rules stored
before the guard existed have never been validated, and DNS can change between
validation and delivery (rebinding).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from ..config import get_settings
from ..core.outbound import OutboundURLError, validate_outbound_url

logger = logging.getLogger(__name__)


async def send_webhook(url: str, payload: dict, timeout: float = 10.0) -> bool:  # NOSONAR
    """POST ``payload`` to ``url``. Returns True on 2xx, False otherwise.

    ``timeout`` is passed straight to ``httpx.AsyncClient`` — httpx's own
    documented timeout mechanism, not a generic ``asyncio.wait_for`` pattern
    a linter might suggest wrapping in ``asyncio.timeout()`` instead; httpx
    manages its own connection-level timeouts and cancellation internally.
    """
    try:
        validate_outbound_url(url, get_settings())
    except OutboundURLError as exc:
        logger.warning("Refusing webhook delivery to %s: %s", url, exc)
        return False

    envelope = {
        "source": "rotsy",
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
