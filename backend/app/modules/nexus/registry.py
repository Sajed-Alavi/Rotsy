"""Dynamic Docker registry discovery — zero client-side configuration.

The vulnerability scanners (Trivy/Grype) pull image manifests over the Docker
Registry v2 API. In Nexus, that API is **not** served on the main Nexus port:
every Docker repository gets its own *connector* port (Nexus calls it the
"HTTP/HTTPS connector"), configured per repository:

    nexus (REST API)        http://nexus-host:8081
    docker repo "team-a"    http://nexus-host:15987/v2/...
    docker repo "team-b"    http://nexus-host:15988/v2/...

Those ports are part of each repository's own configuration, so Nexus itself is
the authoritative source for them. This module asks Nexus and derives the
registry endpoint for every Docker repository automatically. Nothing here is
operator-configurable: add a repository, scale from 7 projects to 12, or move a
connector to a different port, and discovery picks it up on the next refresh
(TTL below) with no UI or env change.

Discovery order (first source that answers wins, per repository):

1. ``GET /service/rest/v1/repositorySettings`` — one call, returns the full
   configuration of every repository including the ``docker`` connector block.
   Requires repository-admin read privileges.
2. ``GET /service/rest/v1/repositories/docker/{type}/{name}`` — per-repository
   fallback for deployments where (1) is unavailable. Repository names/types
   come from the always-public ``/service/rest/v1/repositories`` list.

The registry **host** is the Nexus host itself (connectors listen on the same
network interface as Nexus, only on a different port), so it is derived from the
live Nexus base URL rather than configured separately.

The **scheme** is derived from which connector the repository actually declares:
an ``httpsPort`` means TLS, an ``httpPort`` means plaintext HTTP. This is
deliberately independent of ``NEXUS_VERIFY_SSL`` (which describes the *REST API*
connection) — conflating the two is what made scans fail against a plaintext
connector on an HTTPS Nexus.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from ...core.cache import Cache
from .connector import NexusClient

logger = logging.getLogger(__name__)

# Discovery result cache. Short enough that a new repository or a moved
# connector port is picked up quickly, long enough that a burst of scans does
# not re-interrogate Nexus for every image.
_CACHE_KEY = "scan:docker-registries"
_CACHE_TTL = 120

_SETTINGS_ENDPOINT = "/service/rest/v1/repositorySettings"
_REPOS_ENDPOINT = "/service/rest/v1/repositories"


@dataclass(frozen=True)
class DockerRegistry:
    """A resolved Docker v2 registry endpoint for one Nexus repository."""

    repo: str
    repo_type: str  # hosted | proxy | group
    host: str
    port: int
    scheme: str  # http | https
    source: str  # which discovery step resolved it

    @property
    def authority(self) -> str:
        """``host:port`` — the registry part of an image reference."""
        return f"{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.authority}"

    @property
    def is_plaintext(self) -> bool:
        """True when the connector serves plain HTTP (no TLS)."""
        return self.scheme == "http"

    def image_ref(self, image: str) -> str:
        """Full pullable reference for ``image`` (``name:tag``).

        Nexus connector ports serve the registry at the **root** of the port —
        there is no repository name in the path — so the reference is
        ``host:port/name:tag``.
        """
        return f"{self.authority}/{image.lstrip('/')}"


@dataclass
class DiscoveryResult:
    """Everything discovery learned, including why repos were skipped."""

    registries: dict[str, DockerRegistry]
    unresolved: dict[str, str]  # repo -> human-readable reason
    source: str  # the discovery step that produced the bulk of the map

    def get(self, repo: str) -> DockerRegistry | None:
        return self.registries.get(repo)

    def to_json(self) -> dict[str, Any]:
        return {
            "registries": {k: asdict(v) for k, v in self.registries.items()},
            "unresolved": self.unresolved,
            "source": self.source,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DiscoveryResult:
        return cls(
            registries={k: DockerRegistry(**v) for k, v in (data.get("registries") or {}).items()},
            unresolved=data.get("unresolved") or {},
            source=data.get("source") or "cache",
        )


def _nexus_host(nexus: NexusClient) -> str:
    """Host to reach the Docker connectors on — the Nexus host itself."""
    parsed = urlparse(str(nexus.client.base_url).rstrip("/"))
    return parsed.hostname or "localhost"


def _registry_from_docker_block(
    repo: str, repo_type: str, docker: dict[str, Any], host: str, source: str,
) -> DockerRegistry | str:
    """Build a :class:`DockerRegistry`, or return a reason string on failure.

    Prefers the HTTPS connector when both are configured: if an operator went
    to the trouble of enabling TLS on the connector we should use it.
    """
    https_port = docker.get("httpsPort")
    http_port = docker.get("httpPort")
    if https_port:
        return DockerRegistry(repo, repo_type, host, int(https_port), "https", source)
    if http_port:
        return DockerRegistry(repo, repo_type, host, int(http_port), "http", source)
    return (
        "no Docker connector port configured on this repository — set an HTTP or "
        "HTTPS connector port in Nexus (Repository → Configuration → HTTP/HTTPS)"
    )


async def _fetch_repository_settings(nexus: NexusClient) -> list[dict] | None:
    """The raw rows from the repositorySettings bulk endpoint, or ``None`` on
    any failure — signals the caller to fall back to the per-repository probe."""
    try:
        resp = await nexus.client.get(_SETTINGS_ENDPOINT)
    except Exception as exc:  # noqa: BLE001
        # Network/transport failure — fall through to the per-repository probe.
        logger.debug("repositorySettings unavailable (%s); falling back", exc)
        return None
    if resp.status_code != 200:
        logger.debug("repositorySettings returned HTTP %s; falling back", resp.status_code)
        return None
    try:
        rows = resp.json()
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def _registry_row_from_settings(row: dict, host: str) -> tuple[str, DockerRegistry | None, str | None] | None:
    if (row.get("format") or "").lower() != "docker":
        return None
    name = row.get("name")
    if not name:
        return None
    built = _registry_from_docker_block(
        name, (row.get("type") or "").lower(), row.get("docker") or {}, host, "repositorySettings",
    )
    if isinstance(built, DockerRegistry):
        return name, built, None
    return name, None, built


async def _from_repository_settings(nexus: NexusClient, host: str) -> DiscoveryResult | None:
    """Step 1: one call for every repository's full configuration."""
    rows = await _fetch_repository_settings(nexus)
    if rows is None:
        return None

    registries: dict[str, DockerRegistry] = {}
    unresolved: dict[str, str] = {}
    for row in rows:
        resolved = _registry_row_from_settings(row, host)
        if resolved is None:
            continue
        name, registry, reason = resolved
        if registry is not None:
            registries[name] = registry
        else:
            unresolved[name] = reason
    return DiscoveryResult(registries, unresolved, "repositorySettings")


async def _resolve_repo_registry(
    nexus: NexusClient, row: dict, host: str,
) -> tuple[str, DockerRegistry | None, str | None] | None:
    """One repository row from the list endpoint, resolved to its connector
    port. Returns ``(name, registry, None)`` on success, ``(name, None,
    reason)`` if unresolved, or ``None`` to skip (not a docker
    hosted/proxy/group repo)."""
    if (row.get("format") or "").lower() != "docker":
        return None
    name = row.get("name")
    repo_type = (row.get("type") or "").lower()
    if not name or repo_type not in ("hosted", "proxy", "group"):
        return None
    try:
        detail = await nexus.client.get(f"{_REPOS_ENDPOINT}/docker/{repo_type}/{name}")
    except Exception as exc:  # noqa: BLE001
        return name, None, f"could not read repository configuration: {exc}"
    if detail.status_code != 200:
        return name, None, (
            f"Nexus returned HTTP {detail.status_code} for the repository configuration — "
            "the service account needs repository-admin read privileges to discover "
            "connector ports"
        )
    try:
        docker_block = (detail.json() or {}).get("docker") or {}
    except ValueError:
        return name, None, "malformed repository configuration response"
    built = _registry_from_docker_block(name, repo_type, docker_block, host, "repository-api")
    if isinstance(built, DockerRegistry):
        return name, built, None
    return name, None, built


async def _from_per_repo_api(nexus: NexusClient, host: str) -> DiscoveryResult:
    """Step 2: per-repository config lookups (fallback for step 1)."""
    registries: dict[str, DockerRegistry] = {}
    unresolved: dict[str, str] = {}
    try:
        resp = await nexus.client.get(_REPOS_ENDPOINT)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Docker registry discovery failed to list repositories: %s", exc)
        return DiscoveryResult({}, {}, "unavailable")

    for row in rows if isinstance(rows, list) else []:
        resolved = await _resolve_repo_registry(nexus, row, host)
        if resolved is None:
            continue
        name, registry, reason = resolved
        if registry is not None:
            registries[name] = registry
        else:
            unresolved[name] = reason
    return DiscoveryResult(registries, unresolved, "repository-api")


async def discover(
    nexus: NexusClient,
    cache: Cache | None = None,
    *,
    refresh: bool = False,
) -> DiscoveryResult:
    """Return the Docker registry endpoint for every Docker repository.

    Cached for :data:`_CACHE_TTL` seconds. Pass ``refresh=True`` to re-probe
    Nexus immediately (used when a scan targets a repository that is missing
    from the cached map — e.g. it was created seconds ago).
    """
    if cache is not None and not refresh:
        cached = await cache.get_json(_CACHE_KEY)
        if cached:
            try:
                return DiscoveryResult.from_json(cached)
            except (TypeError, KeyError):
                logger.debug("Discarding malformed cached registry map")

    host = _nexus_host(nexus)
    result = await _from_repository_settings(nexus, host)
    if result is None or (not result.registries and not result.unresolved):
        result = await _from_per_repo_api(nexus, host)

    if cache is not None and result.source != "unavailable":
        await cache.set_json(_CACHE_KEY, result.to_json(), ttl=_CACHE_TTL)
    logger.info(
        "Discovered %d Docker registry endpoint(s) via %s%s",
        len(result.registries), result.source,
        f" ({len(result.unresolved)} unresolved)" if result.unresolved else "",
    )
    return result


async def invalidate(cache: Cache | None) -> None:
    """Drop the cached map so the next lookup re-interrogates Nexus.

    Called when the Nexus connection changes — connector ports discovered
    against the old target say nothing about the new one.
    """
    if cache is not None:
        await cache.delete(_CACHE_KEY)


async def resolve(
    nexus: NexusClient,
    repo: str,
    cache: Cache | None = None,
) -> DockerRegistry:
    """Resolve one repository's registry endpoint, or raise with the reason.

    Re-probes Nexus once (bypassing the cache) when the repository is not in
    the cached map, so a freshly created repository is usable immediately.
    """
    result = await discover(nexus, cache)
    found = result.get(repo)
    if found is None and repo not in result.unresolved:
        result = await discover(nexus, cache, refresh=True)
        found = result.get(repo)
    if found is not None:
        return found
    reason = result.unresolved.get(repo)
    if reason:
        raise RegistryUnavailable(f"repository '{repo}': {reason}")
    raise RegistryUnavailable(
        f"repository '{repo}' is not a Docker repository known to Nexus, or its "
        "configuration could not be read"
    )


async def probe(nexus: NexusClient, registry: DockerRegistry) -> dict[str, Any]:
    """Check that the discovered endpoint really answers the Docker v2 API.

    Used by the diagnostics endpoint and as a scan preflight: a clear
    "connector port refused the connection" beats an opaque scanner exit code.
    A ``401`` counts as reachable — the endpoint is a registry, it just wants
    the credentials the scanners will supply.
    """
    url = f"{registry.base_url}/v2/"
    try:
        resp = await nexus.client.get(url)
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "url": url, "error": str(exc)}
    reachable = resp.status_code in (200, 401, 403)
    return {
        "reachable": reachable,
        "url": url,
        "status_code": resp.status_code,
        "error": None if reachable else f"unexpected HTTP {resp.status_code} from {url}",
    }


class RegistryUnavailable(RuntimeError):
    """Raised when a repository's Docker registry endpoint cannot be resolved."""
