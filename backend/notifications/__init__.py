"""Notifications — a self-contained application, in its own folder.

Telling somebody that something happened is its own concern, not a detail of
whichever job happened to notice. This package owns all of it, and lives at
the top level of ``backend/`` rather than inside ``app/`` so the boundary is
visible in the directory listing, not just in the imports.

    message.py      what a notification is — plain data, no channel markup
    service.py      dispatching one across every configured channel
    renderers.py    producing attachment bytes, lazily, without knowing how
    channels/       one module per delivery mechanism, behind a Protocol
    subscribers.py  which domain events become notifications, and the wording
    ports.py        the ONLY file that imports from `app`

**Dependency direction.** Producers depend on nothing here: a job publishes a
domain event (``app.core.events``) and is done. This package subscribes. So
adding a channel, changing wording, or dropping a notification never touches a
job handler, and a producer cannot accidentally acquire a dependency on
Telegram — which is exactly what had happened before, with three handlers
importing the Telegram module directly.

**Moving it elsewhere.** Everything this package needs from the surrounding
application is named in ``ports.py``, and nothing else here imports ``app``.
Lifting it into a separate service, a shared library or another project means
rewriting that one file: point it at whatever provides a session, an identity
and a Telegram client. The event subscription in ``subscribers.py`` would
become a queue consumer, and ``Attachment`` is already a serialisable
*description* rather than a closure precisely so a notification can survive
crossing a process boundary. Nothing else changes.

It runs in-process today, which is the right call at this size: no extra
container, no transport to operate, and a failure is contained by ``dispatch``
never raising.

**Using it directly.** Event-driven is the default, but anything holding a
session can send one:

    from notifications import Audience, Notification, Severity, dispatch

    await dispatch(Notification(
        title="Retention sweep removed 412 tags",
        audience=Audience.administrators(),
        severity=Severity.INFO,
    ))

``dispatch`` never raises and returns how many recipients were reached, so a
caller may ignore the result entirely.

**Wiring.** :func:`setup` registers the channels a deployment has and
subscribes to events; the application lifespan calls it once at startup.
Nothing here starts a background task or holds a connection.
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
