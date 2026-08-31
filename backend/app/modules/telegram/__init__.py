"""Telegram bot integration — view/manage Project membership and trigger
analysis from Telegram, for users an admin has linked (see
``app.models.telegram.TelegramLink``).

Unlike ``modules/github``, ``modules/gitlab`` and ``modules/sonar``, this
module owns no :class:`~app.models.integration.Integration` row — linking a
Telegram chat is an account-level admin action, not a per-Project
integration, so it never registers with :mod:`app.core.integrations`.
"""
