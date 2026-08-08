"""GitHub webhook signature verification + payload normalization.

Mirrors the shape of :func:`app.modules.nexus.events.verify_webhook_signature`
(HMAC over the raw body, constant-time compare) — GitHub's variant signs with
SHA-256 only and prefixes the header with ``sha256=``.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """Verify ``X-Hub-Signature-256``. Empty secret or header always fails
    closed — a webhook must never be accepted as "verified" by accident."""
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    candidate = signature_header[len("sha256="):].strip().lower()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, candidate)


@dataclass(frozen=True)
class NormalizedEvent:
    type: str  # "push" | "pull_request"
    repo_full_name: str
    ref: str
    sha: str
    installation_id: int


def _push_event(payload: dict[str, Any], installation_id: int) -> NormalizedEvent | None:
    repo = payload.get("repository") or {}
    ref = payload.get("ref", "")  # "refs/heads/main"
    branch = ref.removeprefix("refs/heads/")
    sha = payload.get("after", "")
    # A branch deletion push has an all-zero "after" sha — nothing to analyze.
    if not sha or set(sha) == {"0"}:
        return None
    return NormalizedEvent(
        type="push", repo_full_name=repo.get("full_name", ""), ref=branch, sha=sha,
        installation_id=installation_id,
    )


def _pull_request_event(payload: dict[str, Any], installation_id: int) -> NormalizedEvent | None:
    if payload.get("action") not in ("opened", "synchronize", "reopened"):
        return None
    repo = payload.get("repository") or {}
    pr = payload.get("pull_request") or {}
    head = pr.get("head") or {}
    return NormalizedEvent(
        type="pull_request", repo_full_name=repo.get("full_name", ""), ref=head.get("ref", ""),
        sha=head.get("sha", ""), installation_id=installation_id,
    )


def normalize_event(event_type: str, payload: dict[str, Any]) -> NormalizedEvent | None:
    """Map a GitHub webhook payload to Rotsy's internal event shape.

    Returns ``None`` for event types/actions we don't act on (GitHub sends
    many event types to an App subscribed to "Repository contents" etc.) —
    the caller should 202 these without enqueueing work, same policy as the
    Nexus webhook receiver.
    """
    installation_id = (payload.get("installation") or {}).get("id")
    if installation_id is None:
        return None
    if event_type == "push":
        return _push_event(payload, installation_id)
    if event_type == "pull_request":
        return _pull_request_event(payload, installation_id)
    return None
