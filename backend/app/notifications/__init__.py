"""Notifications — a self-contained application inside Rotsy.

Telling somebody that something happened is its own concern, not a detail of
whichever job happened to notice. This package owns all of it:

    message.py      what a notification is — plain data, no channel markup
    service.py      dispatching one across every configured channel
    channels/       one module per delivery mechanism, behind a Protocol
    subscribers.py  which domain events become notifications, and the wording

**Dependency direction.** Producers depend on nothing here: a job publishes a
domain event (``core/events.py``) and is done. This package subscribes. So
adding a channel, changing wording, or dropping a notification entirely never
touches a job handler, and a producer cannot accidentally acquire a
dependency on Telegram — which is exactly what happened before, with three
separate handlers importing the Telegram module directly.

**Using it directly.** Event-driven is the default, but anything holding a
session can send one:

    from app.notifications import Audience, Notification, Severity, dispatch

    await dispatch(Notification(
        title="Retention sweep removed 412 tags",
        audience=Audience.administrators(),
        severity=Severity.INFO,
    ))

``dispatch`` never raises and returns how many recipients were reached, so a
caller may ignore the result entirely.

**Wiring.** :func:`setup` registers the channels a deployment has and the
event subscribers; the application lifespan calls it once at startup. Nothing
here starts a background task or holds a connection, so a worker process can
call the same function.
"""

from __future__ import annotations

from .message import Attachment, Audience, Notification, Severity
from .service import clear, dispatch, register, registered

__all__ = [
    "Attachment", "Audience", "Notification", "Severity",
    "dispatch", "register", "registered", "clear", "setup",
]


def setup() -> None:
    """Register the available channels and subscribe to domain events.

    Safe to call more than once: channel registration replaces by name and
    event subscription ignores duplicates, so a re-import or a second worker
    cannot produce double delivery.
    """
    from . import subscribers
    from .channels.telegram import TelegramChannel

    register(TelegramChannel())
    subscribers.register()
