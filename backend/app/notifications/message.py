"""What a notification *is*, independent of how it is delivered.

Everything here is plain data with no channel-specific markup. That rule is
the whole point: the analysis worker used to build strings containing
``<b>…</b>`` and call ``html.escape`` on the values it interpolated, because
Telegram happens to render HTML. A worker should not know that. It states
what happened; each channel renders it in whatever form that channel speaks
(HTML for Telegram, JSON for a webhook, and so on).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable


class Severity(str, Enum):
    """How much attention the notification wants. Channels map this to their
    own vocabulary — an emoji, a colour, a priority header."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Attachment:
    """A file to deliver alongside the message.

    ``load`` is a callable rather than the bytes themselves so that producing
    the payload — for an analysis report that means an unbounded query over
    every issue and hotspot, then a multi-page render — happens only once
    there is a real recipient on a channel that can carry it. On a deployment
    with no notification channel configured, which is the common case, the
    bytes are never produced at all.
    """

    filename: str
    media_type: str
    load: Callable[[], Awaitable[bytes]]


@dataclass(frozen=True)
class Notification:
    """One thing worth telling somebody about.

    ``title`` is a single short line; ``body`` may be several. ``fields`` are
    label/value pairs a channel may render as a list, a table, or fold into
    the body — the producer states the facts and leaves the layout alone.
    """

    title: str
    audience: "Audience"
    body: str = ""
    severity: Severity = Severity.INFO
    fields: tuple[tuple[str, str], ...] = ()
    attachment: Attachment | None = None
    # Free-form context for channels that carry structured payloads (the
    # webhook channel serialises it). Never put secrets in here.
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Audience:
    """Who should receive a notification, described by role rather than by
    address. Resolving that to concrete recipients is each channel's job,
    because only the channel knows what an address means for it.
    """

    #: Everyone with at least viewer access to this Project.
    project_id: int | None = None
    #: Everyone holding the global ``system:execute`` permission.
    admins: bool = False

    @classmethod
    def project(cls, project_id: int) -> "Audience":
        return cls(project_id=project_id)

    @classmethod
    def administrators(cls) -> "Audience":
        return cls(admins=True)
