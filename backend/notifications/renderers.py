"""Producing the bytes for an attachment, without knowing how.

An :class:`~notifications.message.Attachment` *describes* a file — a ``kind``
and the parameters for it — rather than carrying bytes or a closure. Whoever
owns the data registers a renderer for that kind at startup; this package
never imports the analysis domain, the scanner, or anything else that knows
how a report is built.

Two reasons it is shaped this way rather than the obvious "pass a callable":

* **Laziness.** A renderer runs only after a configured channel has resolved a
  real recipient. An analysis report is an unbounded query over every issue
  and hotspot plus a multi-page render, and on a deployment with no channel
  configured — the common case — it must never run at all.
* **Portability.** A description survives serialisation; a closure does not.
  If this package is ever lifted out into its own service, the notification
  can be rebuilt from a JSON event on the other side of a queue and the only
  thing that changes is where the renderer is registered.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from .message import Attachment

logger = logging.getLogger(__name__)

Renderer = Callable[[dict], Awaitable[bytes]]

_renderers: dict[str, Renderer] = {}


def register(kind: str, renderer: Renderer) -> None:
    """Teach this package how to produce attachments of ``kind``."""
    _renderers[kind] = renderer


def registered() -> list[str]:
    return sorted(_renderers)


def clear() -> None:
    """For tests."""
    _renderers.clear()


def can_render(attachment: Attachment | None) -> bool:
    return attachment is not None and attachment.kind in _renderers


async def render(attachment: Attachment) -> bytes | None:
    """The attachment's bytes, or ``None`` if it cannot be produced.

    Never raises: an attachment that fails to render degrades the notification
    to its text, which is strictly better than losing the notification.
    """
    renderer = _renderers.get(attachment.kind)
    if renderer is None:
        logger.debug("No renderer registered for attachment kind %r", attachment.kind)
        return None
    try:
        return await renderer(attachment.params)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to render attachment %r", attachment.kind)
        return None
