"""A small in-process event bus, and the domain events published on it.

Background jobs used to call notification code directly: the analysis worker
imported the Telegram module, the backup handler imported it again, the
scanner-database handler a third time — each wrapped in its own
``try/except`` so a chat failure could not fail the job. That is N producers
wired to M channels by hand, and adding email or Slack would mean editing
every one of those call sites again.

Now a job states *what happened* and stops caring who listens.
:func:`publish` never raises and never lets a subscriber's failure reach the
publisher, so emitting an event stays as safe as the ``try/except`` it
replaces — a job's own outcome must never depend on whether anyone was
listening.

Deliberately in-process rather than Redis- or broker-backed. Subscribers here
enqueue work or send a message; none of them need durability beyond the job
that triggered them, and an event that is lost because the process died is an
event whose job also died. Introducing a broker for this would add an
operational dependency to buy a guarantee nothing currently needs.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# One dispatch must not outlive the job that published it (see the same
# reasoning in the notifications app): a subscriber talking to an unreachable
# external service would otherwise hold the publisher for its full timeout.
_DISPATCH_TIMEOUT = 120.0


@dataclass(frozen=True)
class Event:
    """Base for every domain event. Subclasses carry the facts, never the
    decision about what to do with them."""

    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc), kw_only=True,
    )


# --- the catalogue ---------------------------------------------------------
#
# Small on purpose. An event earns its place by having at least one subscriber
# that is genuinely separate from the code that publishes it; anything else is
# a function call wearing a costume.


@dataclass(frozen=True)
class AnalysisCompleted(Event):
    """A ``clone_and_analyze`` run finished, whatever its quality gate said."""

    project_id: int
    analysis_run_id: int
    sonar_project_id: int
    repo_name: str
    ref: str
    commit_sha: str
    quality_gate: str
    issues_count: int
    bugs: int
    vulnerabilities: int
    code_smells: int
    coverage: float | None


@dataclass(frozen=True)
class AnalysisFailed(Event):
    project_id: int
    repo_name: str
    ref: str
    error: str


@dataclass(frozen=True)
class BackupFailed(Event):
    mode: str
    error: str


@dataclass(frozen=True)
class ScannerDatabaseUpdateFailed(Event):
    scanners: tuple[str, ...]
    error: str


# --- the bus ---------------------------------------------------------------

Subscriber = Callable[[Event], Awaitable[None] | None]

_subscribers: dict[type[Event], list[Subscriber]] = defaultdict(list)


def subscribe(event_type: type[Event], handler: Subscriber) -> None:
    """Register ``handler`` for ``event_type``. Idempotent: registering the
    same handler twice is a no-op, so importing a subscriber module more than
    once cannot cause duplicate deliveries."""
    handlers = _subscribers[event_type]
    if handler not in handlers:
        handlers.append(handler)


def clear_subscribers() -> None:
    """Drop every registration. For tests, which need a clean bus per case."""
    _subscribers.clear()


def subscriber_count(event_type: type[Event]) -> int:
    return len(_subscribers.get(event_type, []))


async def publish(event: Event) -> None:
    """Deliver ``event`` to its subscribers. Never raises.

    Subscribers run concurrently, and one failing neither stops the others nor
    reaches the publisher — a notification channel being down is not a reason
    for an analysis that already succeeded to be recorded as failed.
    """
    handlers = list(_subscribers.get(type(event), []))
    if not handlers:
        return

    async def _run(handler: Subscriber) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - a subscriber must never break the publisher
            logger.exception(
                "Event subscriber %r failed for %s", getattr(handler, "__qualname__", handler),
                type(event).__name__,
            )

    try:
        async with asyncio.timeout(_DISPATCH_TIMEOUT):
            await asyncio.gather(*(_run(h) for h in handlers))
    except TimeoutError:
        logger.warning(
            "Event dispatch for %s timed out after %.0fs", type(event).__name__, _DISPATCH_TIMEOUT,
        )


def describe() -> dict[str, Any]:
    """Registered events and their subscriber counts — for diagnostics."""
    return {
        event_type.__name__: [getattr(h, "__qualname__", repr(h)) for h in handlers]
        for event_type, handlers in _subscribers.items()
    }
