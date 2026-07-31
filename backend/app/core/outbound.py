"""Destination guard for user-controlled outbound requests (SSRF).

Two features let an authenticated user choose a URL the *backend* then calls:
alert webhooks (``alerts:write``) and Nexus-to-Nexus sync targets
(``system:execute``). The backend sits inside the deployment's network, so an
unvalidated destination turns it into a confused deputy: the caller supplies
``http://169.254.169.254/latest/meta-data/`` or an internal admin panel and the
backend reaches it on their behalf — sometimes carrying credentials from the
request body.

See ``security/findings/medium/MED-04-alert-webhook-ssrf.md`` and
``security/findings/medium/MED-05-sync-target-ssrf.md``.

Guard policy:

* scheme must be ``http`` or ``https`` (no ``file://``, ``gopher://``, ...)
* a host must be present, and every address it resolves to is checked — not
  just the first, since a hostname with both a public and a private A record
  would otherwise pass
* loopback, private (RFC1918), link-local (which covers the ``169.254.169.254``
  cloud metadata endpoint), reserved, multicast and unspecified addresses are
  rejected

On-prem deployments legitimately need private destinations. ``OUTBOUND_ALLOWED_HOSTS``
is the explicit, admin-set escape hatch; it is empty by default.

Callers apply this in **two** places, and both matter:

1. At creation time (pydantic validators), so a bad destination never persists.
2. At dispatch time, because alert rows predate this guard and because DNS can
   change between validation and the request (rebinding).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from ..config import Settings

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}


class OutboundURLError(ValueError):
    """Raised when a destination URL is malformed or not permitted."""


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if ``ip`` is in a range the backend must not be steered towards."""
    return (
        ip.is_private          # RFC1918 + unique-local; also covers loopback/link-local
        or ip.is_loopback
        or ip.is_link_local    # 169.254.0.0/16 — cloud metadata lives here
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``host`` to every address it maps to.

    A bare IP literal short-circuits DNS. Resolution failure is itself an
    error: we fail closed rather than let an unresolvable host through to the
    HTTP client.
    """
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise OutboundURLError(f"could not resolve host {host!r}: {exc}") from exc

    addresses = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not addresses:
        raise OutboundURLError(f"host {host!r} resolved to no usable address")
    return addresses


def validate_outbound_url(url: str, settings: Settings) -> str:
    """Return ``url`` unchanged if the backend may call it, else raise.

    :raises OutboundURLError: on a bad scheme, missing host, unresolvable host,
        or any resolved address inside a blocked range.
    """
    raw = (url or "").strip()
    if not raw:
        raise OutboundURLError("URL must not be empty")

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise OutboundURLError(
            f"URL scheme {parsed.scheme!r} is not allowed — use http or https"
        )

    try:
        host = parsed.hostname
    except ValueError as exc:  # malformed IPv6 literal in the netloc
        raise OutboundURLError(f"malformed URL host: {exc}") from exc
    if not host:
        raise OutboundURLError("URL must include a host")

    if host.lower() in settings.outbound_allowed_hosts:
        logger.info("Outbound host %s permitted via OUTBOUND_ALLOWED_HOSTS", host)
        return raw

    for ip in _resolve(host):
        if _is_blocked_address(ip):
            raise OutboundURLError(
                f"host {host!r} resolves to {ip}, which is in a private, "
                "loopback, link-local or otherwise reserved range. Add it to "
                "OUTBOUND_ALLOWED_HOSTS if this destination is intentional."
            )
    return raw
