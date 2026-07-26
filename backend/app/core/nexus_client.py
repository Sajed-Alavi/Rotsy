"""Async Nexus Repository Manager HTTP client.

This module owns a single shared :class:`httpx.AsyncClient` configured from
the application settings (base URL, basic-auth, TLS verification, timeout).
The client is created in the FastAPI lifespan and closed on shutdown, so
connection pooling is reused across requests.

It also exposes :func:`paginate`, a small helper that follows Nexus'
``continuationToken`` paging convention used by the Assets and Components
REST endpoints.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


# Accept headers used when talking to the Docker v2 registry endpoints. These
# request the manifest formats we know how to parse in ``storage_analyzer`` —
# single-arch v2 manifests, OCI image manifests, and multi-arch manifest lists.
DOCKER_MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json"
)


class NexusClient:
    """Thin async wrapper around the Nexus REST + Docker registry APIs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Create the underlying ``httpx.AsyncClient``. Call once at startup."""
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            base_url=self._settings.NEXUS_URL,
            auth=(self._settings.NEXUS_USERNAME, self._settings.NEXUS_PASSWORD),
            verify=self._settings.NEXUS_VERIFY_SSL,
            timeout=httpx.Timeout(self._settings.ANALYZER_REQUEST_TIMEOUT),
            # We intentionally disable warning-noise around retries; the analyzer
            # and routers handle errors explicitly.
            follow_redirects=True,
        )
        logger.info("NexusClient started -> %s", self._settings.NEXUS_URL)

    async def close(self) -> None:
        """Close the underlying client. Call once on shutdown."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("NexusClient closed")

    async def reconfigure(self, url: str, username: str, password: str, verify_ssl: bool) -> None:
        """Swap the connection target live (dashboard-driven config change).

        Closes the existing httpx client and opens a new one against the
        provided URL/credentials. Safe to call while requests are in flight
        (in-flight requests finish on the old client; new ones use the new).
        """
        # Build the new settings-bearing object without re-validating env.
        from types import SimpleNamespace
        self._settings = SimpleNamespace(
            NEXUS_URL=url.rstrip("/"),
            NEXUS_USERNAME=username,
            NEXUS_PASSWORD=password,
            NEXUS_VERIFY_SSL=verify_ssl,
            ANALYZER_REQUEST_TIMEOUT=self._settings.ANALYZER_REQUEST_TIMEOUT,
        )
        old = self._client
        self._client = httpx.AsyncClient(
            base_url=self._settings.NEXUS_URL,
            auth=(self._settings.NEXUS_USERNAME, self._settings.NEXUS_PASSWORD),
            verify=self._settings.NEXUS_VERIFY_SSL,
            timeout=httpx.Timeout(self._settings.ANALYZER_REQUEST_TIMEOUT),
            follow_redirects=True,
        )
        logger.info("NexusClient reconfigured -> %s", self._settings.NEXUS_URL)
        if old is not None:
            await old.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("NexusClient.start() must be called before use.")
        return self._client

    @property
    def settings(self) -> Settings:
        return self._settings

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------
    async def get(self, url: str, *, params: dict[str, Any] | None = None,
                  headers: dict[str, str] | None = None) -> httpx.Response:
        """Issue an authenticated GET and return the raw response.

        Raises ``httpx.HTTPError`` on network/transport failures. Callers
        decide how to translate HTTP status codes.
        """
        return await self.client.get(url, params=params, headers=headers)

    async def delete(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        """Issue an authenticated DELETE. Used by retention/cleanup (Feature B)."""
        return await self.client.delete(url, headers=headers)

    async def paginate(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        items_key: str = "items",
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every item across all pages of a Nexus list endpoint.

        Nexus paginates via a ``continuationToken`` field. When present it is
        appended to the next request's query params; when absent paging ends.
        """
        params = dict(params or {})
        while True:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            for item in data.get(items_key, []) or []:
                yield item

            token = data.get("continuationToken")
            if not token:
                return
            params["continuationToken"] = token
