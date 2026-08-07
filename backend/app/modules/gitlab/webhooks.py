"""GitLab webhook verification + payload normalization.

GitLab authenticates webhook deliveries differently from GitHub: a static,
per-webhook secret token echoed back in the ``X-Gitlab-Token`` header,
compared verbatim — not an HMAC signature over the body. Each repository has
its own webhook (and therefore its own secret), unlike GitHub's one
App-level webhook, so verification is keyed by whichever repository the
request's URL path identifies (see ``routers/gitlab.py``).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any


def verify_token(expected_secret: str, provided_token: str) -> bool:
    if not expected_secret or not provided_token:
        return False
    return hmac.compare_digest(expected_secret, provided_token)


@dataclass(frozen=True)
class NormalizedEvent:
    type: str  # "push"
    ref: str
    sha: str


def normalize_push_event(payload: dict[str, Any]) -> NormalizedEvent | None:
    """Map a GitLab ``Push Hook`` payload to Rotsy's internal event shape.

    Returns ``None`` for event kinds we don't act on, or a branch deletion
    (all-zero ``after`` sha) — same policy as the GitHub webhook receiver:
    nothing here is an error, just nothing to analyze.
    """
    if payload.get("object_kind") != "push":
        return None
    ref = payload.get("ref", "")  # "refs/heads/main"
    branch = ref.removeprefix("refs/heads/")
    sha = payload.get("after", "")
    if not sha or set(sha) == {"0"}:
        return None
    return NormalizedEvent(type="push", ref=branch, sha=sha)
