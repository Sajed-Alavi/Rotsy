"""Everything this package needs from the application around it.

**This is the only module in `notifications/` that imports from `app`.** Every
other module imports from here. That is the whole point: the package sits in
its own top-level folder, and lifting it out — into a separate service, a
shared library, another project — means rewriting this one file and nothing
else.

Keeping the surface visible also keeps it honest. It is short, and it should
stay short; if a new dependency does not obviously belong on this list, that
is the signal to reconsider rather than to add it.

The current surface:

* the event bus — the source of everything this package reacts to;
* a database session factory — recipients live in the application's tables;
* who a user is and what they may see — permissions and Project membership,
  re-derived per delivery so a deactivated user stops receiving immediately;
* the Telegram bot connection and client, for that channel only.

Nothing here writes application data. This package reads to decide who should
be told, and sends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# --- the event bus this package subscribes to ------------------------------
from app.core import events  # noqa: F401

# --- database --------------------------------------------------------------
from app.db.session import get_session_factory  # noqa: F401

# --- identity and access ---------------------------------------------------
from app.core import project_access  # noqa: F401
from app.dependencies import user_permissions  # noqa: F401
from app.models import TelegramLink, User  # noqa: F401

# --- configuration ---------------------------------------------------------
from app.config import Settings, get_settings  # noqa: F401
from app.core.config_store import get_telegram_connection  # noqa: F401

# --- the Telegram adapter (used only by channels/telegram.py) --------------
from app.modules.telegram.auth import linked_user  # noqa: F401
from app.modules.telegram.client import (  # noqa: F401
    TelegramClient,
    TelegramError,
    escape_html,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401

__all__ = [
    "events",
    "get_session_factory",
    "project_access",
    "user_permissions",
    "TelegramLink",
    "User",
    "Settings",
    "get_settings",
    "get_telegram_connection",
    "linked_user",
    "TelegramClient",
    "TelegramError",
    "escape_html",
]
