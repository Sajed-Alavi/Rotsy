"""Thin client over the Telegram Bot API.

Same shape as ``app.modules.sonar.connector.SonarClient`` — one ``_request``
chokepoint that turns every failure (HTTP-status *and* transport-level) into
one exception type. Unlike ``app.services.notifier.send_webhook``'s
fire-and-forget alerts, the bot's own poll loop needs to *detect* failures
to back off correctly, so this raises rather than swallowing.
"""

from __future__ import annotations

import httpx

_API_ROOT = "https://api.telegram.org"


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise TelegramError("Telegram bot token is required")
        self._base_url = f"{_API_ROOT}/bot{token}"

    async def _request(self, method: str, path: str, *, timeout: float = 20.0, **kwargs) -> dict:
        """Every Telegram call goes through here. ``timeout`` is a plain
        parameter, not a fixed client-wide value — ``get_updates`` below
        passes a long-poll ``timeout`` *argument* to Telegram itself (how
        long the server holds the connection open waiting for an update),
        and the httpx-level timeout must be set noticeably higher than that
        or httpx cancels a perfectly healthy long-poll out from under it.

        Telegram's own envelope is ``{"ok": bool, "result": ..., "description": ...}``
        regardless of HTTP status — a failure can be a 200 with ``ok: false``
        (a bad chat_id, a blocked bot) as easily as a 4xx, so both are checked.
        """
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=timeout) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise TelegramError(f"Unable to reach Telegram: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise TelegramError(f"Telegram returned a non-JSON response: {resp.text[:300]}") from exc
        if resp.status_code >= 400 or not data.get("ok", False):
            raise TelegramError(f"{method} {path} failed: {data.get('description') or resp.text[:300]}")
        return data["result"]

    async def get_me(self) -> dict:
        """Bot identity (username, etc.) — the Settings card's "Test" check."""
        return await self._request("GET", "/getMe")

    async def get_updates(self, offset: int | None, *, timeout: int = 25) -> list[dict]:
        """Long-poll for new updates. ``offset`` should be ``last_update_id +
        1`` — Telegram treats any ``getUpdates`` call carrying a given offset
        as acknowledging every update before it, so no separate ack call
        exists to forget. The httpx-level timeout is kept a healthy margin
        above Telegram's own wait so a normal empty long-poll never reads as
        a transport failure.
        """
        params: dict[str, int] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        return await self._request("GET", "/getUpdates", params=params, timeout=timeout + 10.0)

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        body: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        return await self._request("POST", "/sendMessage", json=body)

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None,
    ) -> dict:
        body: dict = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        return await self._request("POST", "/editMessageText", json=body)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict:
        body: dict = {"callback_query_id": callback_query_id}
        if text:
            body["text"] = text
        return await self._request("POST", "/answerCallbackQuery", json=body)
