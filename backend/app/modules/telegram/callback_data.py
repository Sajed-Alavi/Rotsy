"""Encode/parse the bot's inline-keyboard ``callback_data`` strings.

Telegram caps ``callback_data`` at 64 bytes. Every action here is a short
code plus bare integer ids (Rotsy's own primary keys) or a single-character
role code — nothing free-text, since the bot never accepts typed input, only
button taps — so the longest realistic string is well under the limit and no
hashing/lookup-table indirection is needed.

Actions:
    pl:{page}                    projects list, page N
    p:{project_id}                open project detail
    mm:{project_id}                open members list
    mc:{project_id}:{page}          candidate picker page
    ma:{project_id}:{user_id}        pick candidate -> role picker
    mr:{project_id}:{user_id}:{role}  confirm add with role (v/m/a)
    me:{project_id}:{member_id}      existing-member actions
    mu:{project_id}:{member_id}:{role} change role
    md:{project_id}:{member_id}      remove (asks to confirm)
    mdc:{project_id}:{member_id}     confirmed remove
    pr:{project_id}:{page}          repo/"Run Analysis" list page
    ra:{sonar_project_id}          run analysis
    back:{where}                  generic back-nav (where is itself a
                                   callback_data string, e.g. "back:p:42")
"""

from __future__ import annotations

from dataclasses import dataclass

ROLE_CODES = {"v": "viewer", "m": "member", "a": "admin"}
ROLE_TO_CODE = {v: k for k, v in ROLE_CODES.items()}


class CallbackDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Callback:
    action: str
    args: tuple[str, ...]

    def int_arg(self, index: int) -> int:
        try:
            return int(self.args[index])
        except (IndexError, ValueError) as exc:
            raise CallbackDataError(f"expected an integer argument at position {index}") from exc

    def role_arg(self, index: int) -> str:
        try:
            code = self.args[index]
        except IndexError as exc:
            raise CallbackDataError(f"missing role argument at position {index}") from exc
        role = ROLE_CODES.get(code)
        if role is None:
            raise CallbackDataError(f"unknown role code {code!r}")
        return role


def parse(data: str) -> Callback:
    parts = (data or "").split(":")
    if not parts or not parts[0]:
        raise CallbackDataError("empty callback_data")
    return Callback(action=parts[0], args=tuple(parts[1:]))


def build(action: str, *args: object) -> str:
    encoded = ":".join(str(a) for a in (action, *args))
    if len(encoded.encode("utf-8")) > 64:
        raise CallbackDataError(f"callback_data too long ({len(encoded)} bytes): {encoded!r}")
    return encoded
