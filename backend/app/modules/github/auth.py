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
"""

from __future__ import annotations

import time

import httpx
import jwt

from ...config import Settings
from ...core.cache import Cache

_GITHUB_API = "https://api.github.com"
_APP_JWT_TTL_SECONDS = 570  # under GitHub's 10-minute cap, with margin
_TOKEN_CACHE_KEY = "github:install_token:{installation_id}"
_TOKEN_CACHE_TTL_SECONDS = 3000  # installation tokens live ~1h; refresh well before


class GitHubAuthError(Exception):
    """Raised when App JWT signing or installation-token exchange fails."""


def _app_jwt(settings: Settings) -> str:
    if not settings.GITHUB_APP_ID or not settings.GITHUB_APP_PRIVATE_KEY:
        raise GitHubAuthError("GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY are not configured")
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + _APP_JWT_TTL_SECONDS, "iss": settings.GITHUB_APP_ID}
    return jwt.encode(payload, settings.GITHUB_APP_PRIVATE_KEY, algorithm="RS256")


async def get_installation_token(settings: Settings, cache: Cache, installation_id: int) -> str:
    """Return a live installation access token, from cache or freshly minted."""
    key = _TOKEN_CACHE_KEY.format(installation_id=installation_id)
    cached = await cache.get_json(key)
    if cached:
        return cached

    app_jwt = _app_jwt(settings)
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


def install_url(settings: Settings) -> str:
    """Link that starts the "Connect GitHub" flow — installs the App on an
    org/account the user picks, then GitHub redirects to our callback."""
    if not settings.GITHUB_APP_SLUG:
        raise GitHubAuthError("GITHUB_APP_SLUG is not configured")
    return f"https://github.com/apps/{settings.GITHUB_APP_SLUG}/installations/new"
