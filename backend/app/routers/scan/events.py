"""Trigger (a): the Nexus push webhook, plus its setup/rotate endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...core.config_store import get_or_create_webhook_secret, rotate_webhook_secret
from ...dependencies import RequirePermission, get_session, get_settings
from ...modules.nexus import events as scan_events
from ._common import default_scanners, require_backend

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/events/nexus", status_code=status.HTTP_202_ACCEPTED, include_in_schema=True)
async def nexus_webhook(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Receive a Nexus repository component webhook and scan on push.

    Authenticated by the HMAC signature Nexus puts in
    ``X-Nexus-Webhook-Signature`` — not by a user session, since Nexus calls this
    machine-to-machine. Fetch the shared secret from ``GET /scan/webhook`` and
    paste it into the Nexus webhook capability.

    Always accepted with 202 once the signature checks out, including for events
    that do not lead to a scan (deletions, non-Docker formats, already-known
    content): a webhook receiver that returns errors for uninteresting events
    ends up disabled by the sender.
    """
    body = await request.body()
    signature = request.headers.get("X-Nexus-Webhook-Signature", "")
    secret = await get_or_create_webhook_secret(session, settings)

    if not scan_events.verify_webhook_signature(secret, body, signature):
        logger.warning(
            "Rejected a Nexus webhook delivery with a bad or missing signature (delivery %s)",
            request.headers.get("X-Nexus-Webhook-Delivery", "?"),
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid X-Nexus-Webhook-Signature. Configure the Nexus webhook capability with the "
            "secret from GET /api/scan/webhook.",
        )

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook body is not valid JSON")

    parsed = scan_events.parse_webhook_payload(payload if isinstance(payload, dict) else {})
    if parsed is None:
        return {"scanned": False, "reason": "event is not a Docker component create/update"}

    repo, ref = parsed
    _, cache = require_backend(request)
    result = await scan_events.ingest_push_event(
        session, cache, repo, ref, source="webhook",
        default_scanners=await default_scanners(settings, session),
    )
    if not result.get("scanned"):
        response.status_code = status.HTTP_200_OK
    return result


@router.get("/webhook", dependencies=[Depends(RequirePermission("system:execute"))])
async def webhook_setup(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """The values and steps needed to wire the Nexus push webhook up.

    Creating a Nexus capability is an administrative action in Nexus itself, so
    this endpoint hands the operator the exact values rather than attempting it.
    """
    secret = await get_or_create_webhook_secret(session, settings)
    return {
        "event_id": scan_events.WEBHOOK_EVENT_ID,
        "secret": secret,
        "path": "/api/scan/events/nexus",
        "signature_header": "X-Nexus-Webhook-Signature",
        "instructions": [
            "In Nexus, open Administration → System → Capabilities and click Create capability.",
            "Choose type 'Webhook: Repository'.",
            "Set Repository to the Docker repository you want scanned (repeat per repository), "
            "and tick the 'component' event type.",
            "Set URL to this backend's webhook endpoint, reachable from the Nexus host — "
            "http://localhost:<BACKEND_PORT>/api/scan/events/nexus when Nexus runs on the "
            "Docker host and the backend publishes that port.",
            "Set Secret Key to the 'secret' value above.",
            "Save. The next image pushed to that repository is scanned within seconds.",
        ],
    }


@router.post("/webhook/rotate", dependencies=[Depends(RequirePermission("system:execute"))])
async def webhook_rotate(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Issue a new webhook secret. Update the Nexus capability to match."""
    return {"secret": await rotate_webhook_secret(session)}
