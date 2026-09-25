# notifications

Telling somebody that something happened. Its own folder beside `app/`, not
inside it, so the boundary is visible in the directory listing rather than
only in the imports.

```
notifications/
├── message.py      what a notification is — plain data, no channel markup
├── service.py      dispatch one across every configured channel
├── renderers.py    produce attachment bytes, lazily, without knowing how
├── subscribers.py  which domain events become notifications, and the wording
├── channels/       one module per delivery mechanism, behind a Protocol
│   ├── base.py     NotificationChannel
│   └── telegram.py
└── ports.py        the ONLY file that imports from `app`
```

## The rule that makes it portable

**`ports.py` is the single point of contact with the rest of the backend.**
Nothing else in this folder imports `app`. Verify it:

```bash
grep -rn "from app\|import app" backend/notifications --include=*.py | grep -v ports.py
```

That should print nothing. If it prints something, the boundary has leaked and
this folder is no longer liftable.

## Moving it somewhere else

Rewrite `ports.py` and nothing else. It names four things: the event bus, a
database session factory, identity/access lookups, and the Telegram client.
Point them at whatever the new home provides.

Two design choices exist specifically to keep that cheap:

* **`Attachment` is a description, not a closure.** It carries a `kind` and
  `params`; whoever owns the data registers a renderer for that kind. A
  description survives serialisation, so a notification can be rebuilt from a
  JSON message on the far side of a queue. A closure could not.
* **Subscribing is one function.** `subscribers.register()` is the only thing
  tying this to an in-process bus. Replace it with a queue consumer that maps
  a message onto the same builders and the rest of the package is unchanged.

It runs in-process today, which is right at this size — no extra container, no
transport to operate, and `dispatch` never raises so a failure stays contained.

## Adding a channel

Implement `NotificationChannel` (`name`, `is_configured`, `deliver`) in
`channels/`, then register it in `setup()`. Nothing else changes — not the
producers, not the wording, not the dispatcher.

## Using it directly

Event-driven is the default, but anything with a session can send one:

```python
from notifications import Audience, Notification, Severity, dispatch

await dispatch(Notification(
    title="Retention sweep removed 412 tags",
    audience=Audience.administrators(),
    severity=Severity.INFO,
))
```

`dispatch` never raises and returns how many recipients it reached, so the
result can be ignored.

## Why producers do not import this

A job publishes a domain event and is done. This package subscribes. That
means adding a channel or changing wording never touches a job handler — and a
worker cannot accidentally acquire a dependency on Telegram, which is exactly
what had happened before: three separate handlers imported the Telegram module
directly, each with its own `try/except` so a chat failure could not fail the
job.

## Tests

`backend/tests/test_notifications.py` — dispatch, the channel contract, lazy
attachment rendering, and the event wiring. Nothing there imports a job
handler, which is the point.
