"""Correlation ids: tying every log line from one request or job together.

Rotsy's logs are per-component (``metric_loop``, ``telegram_poll_loop``,
``app.core.jobs``) with no thread tying them. Diagnosing "this analysis went
wrong" means reading interleaved lines from a router, a job runner, a worker
and a provider adapter, and guessing which belong to the same piece of work —
a guess that gets harder exactly when it matters, under load.

A :class:`contextvars.ContextVar` carries the id, so it follows an ``await``
into whatever the handler calls without every function growing a parameter,
and stays correct across concurrent requests. The logging filter attaches it
to every record, including records from libraries that know nothing about it.

Deliberately not distributed tracing: this is one process, and a correlation
id in the logs answers the question that actually gets asked, without adding
a collector to operate.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

#: Empty string rather than None so log formatting never renders "None".
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")

#: Echoed back on every response, and accepted from the caller so a client
#: (or a reverse proxy) can supply its own and match the two sides up.
HEADER_NAME = "X-Request-ID"


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def new_correlation_id(prefix: str = "") -> str:
    """A fresh id. ``prefix`` marks where the work came from — ``job`` for a
    background job, empty for an HTTP request — so the origin is readable
    without cross-referencing."""
    token = uuid.uuid4().hex[:16]
    return f"{prefix}-{token}" if prefix else token


class CorrelationIdFilter(logging.Filter):
    """Adds ``correlation_id`` to every record so a formatter can include it.

    A filter rather than a custom Logger: it applies to records from third-party
    libraries too, which never call anything of ours.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get() or "-"
        return True


def install(handler: logging.Handler | None = None) -> None:
    """Attach the filter to the root handlers (or one specific handler)."""
    targets = [handler] if handler is not None else logging.getLogger().handlers
    for target in targets:
        if not any(isinstance(f, CorrelationIdFilter) for f in target.filters):
            target.addFilter(CorrelationIdFilter())
