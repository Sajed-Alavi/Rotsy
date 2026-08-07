"""GitHub App authentication.

Two token types, never confused:

  * The **App JWT** — signed with the App's own RSA private key, ten minutes
    max lifetime, proves "I am this App". Used only to mint installation
    tokens.
  * The **installation access token** — obtained by exchanging the App JWT at
    ``POST /app/installations/{id}/access_tokens``, scoped to exactly the
    repos GitHub granted the installation, ~1 hour lifetime. This is what
    every repo-scoped API call and clone actually uses.

Installation tokens are cached in Redis (via the existing :class:`Cache`) with
a TTL short of their real expiry, so a burst of webhook deliveries for the
same installation doesn't mint a fresh token per event.

Takes an explicit ``app_id``/``private_key`` pair rather than ``Settings``
directly — same reasoning as ``SonarClient`` taking ``url``/``token``: the
App's credentials are dashboard-managed (see
:func:`app.core.config_store.get_github_app_config`, DB-first with env
fallback), normally populated by the App Manifest "Connect to GitHub" flow
rather than typed into ``.env`` by hand.
"""

from __future__ import annotations

import time

import httpx
import jwt

from ...core.cache import Cache

_GITHUB_API = "https://api.github.com"
_APP_JWT_TTL_SECONDS = 570  # under GitHub's 10-minute cap, with margin
_TOKEN_CACHE_KEY = "github:install_token:{installation_id}"
_TOKEN_CACHE_TTL_SECONDS = 3000  # installation tokens live ~1h; refresh well before


class GitHubAuthError(Exception):
    """Raised when App JWT signing or installation-token exchange fails."""


def _app_jwt(app_id: str, private_key: str) -> str:
    if not app_id or not private_key:
        raise GitHubAuthError("The GitHub App is not configured yet — connect it from Settings -> Integrations.")
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + _APP_JWT_TTL_SECONDS, "iss": app_id}
    try:
        return jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as exc:  # noqa: BLE001 — PyJWT/cryptography raise several types for a bad key
        # The single most common cause: the PEM private key was mangled going
        # into the dashboard form (escaped/collapsed newlines, or a truncated
        # paste) — this raised a raw cryptography exception ("Could not
        # deserialize key data") that every caller above (get_latest_commit_sha,
        # fetch_source, ...) surfaced verbatim as an opaque failure. Naming
        # the actual cause here is what makes it fixable from Settings ->
        # Integrations -> GitHub instead of "verify the connection".
        raise GitHubAuthError(
            "The GitHub App's private key could not be used to sign a request "
            f"({exc}). Re-paste the full .pem private key (including the "
            "BEGIN/END lines) in Settings -> Integrations -> GitHub."
        ) from exc


async def get_installation_token(app_id: str, private_key: str, cache: Cache, installation_id: int) -> str:
    """Return a live installation access token, from cache or freshly minted."""
    key = _TOKEN_CACHE_KEY.format(installation_id=installation_id)
    cached = await cache.get_json(key)
    if cached:
        return cached

    app_jwt = _app_jwt(app_id, private_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if resp.status_code >= 400:
        raise GitHubAuthError(
            f"Failed to mint installation token for installation {installation_id}: "
            f"{resp.status_code} {resp.text[:300]}"
        )
    token = resp.json()["token"]
    await cache.set_json(key, token, ttl=_TOKEN_CACHE_TTL_SECONDS)
    return token


def install_url(app_slug: str) -> str:
    """Link that starts the "Connect GitHub" flow — installs the App on an
    org/account the user picks, then GitHub redirects to our callback."""
    if not app_slug:
        raise GitHubAuthError("The GitHub App is not configured yet — connect it from Settings -> Integrations.")
    return f"https://github.com/apps/{app_slug}/installations/new"
