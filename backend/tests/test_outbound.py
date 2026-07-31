"""MED-04 / MED-05 regression tests: the SSRF destination guard.

Covers the guard itself (``app.core.outbound``) rather than the two routers
that call it, since both sinks — alert ``webhook_url`` and sync
``target_base_url`` — funnel through the same function.

DNS is stubbed via ``socket.getaddrinfo`` so these tests neither touch the
network nor depend on what a real resolver returns.
"""

from __future__ import annotations

import socket

import pytest

from app.core.outbound import OutboundURLError, validate_outbound_url
from conftest import make_settings


@pytest.fixture
def resolves(monkeypatch):
    """Point every hostname at a chosen IP for the duration of a test."""
    def _install(ip: str):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]
        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return _install


# --- scheme ------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://internal:70/_test",
    "ftp://internal/x",
    "//no-scheme.example.com/x",
])
def test_non_http_schemes_rejected(url):
    with pytest.raises(OutboundURLError):
        validate_outbound_url(url, make_settings())


def test_empty_url_rejected():
    with pytest.raises(OutboundURLError):
        validate_outbound_url("", make_settings())


def test_missing_host_rejected():
    with pytest.raises(OutboundURLError):
        validate_outbound_url("http:///just-a-path", make_settings())


# --- blocked address ranges --------------------------------------------------
@pytest.mark.parametrize("ip", [
    "127.0.0.1",        # loopback
    "10.1.2.3",         # RFC1918
    "192.168.0.10",     # RFC1918
    "172.16.5.4",       # RFC1918
    "169.254.169.254",  # cloud metadata — the headline case
    "0.0.0.0",          # unspecified
])
def test_blocked_ip_literals_rejected(ip):
    with pytest.raises(OutboundURLError):
        validate_outbound_url(f"http://{ip}/hook", make_settings())


def test_public_ip_literal_allowed():
    url = "https://93.184.216.34/hook"
    assert validate_outbound_url(url, make_settings()) == url


def test_hostname_resolving_to_private_rejected(resolves):
    """The check follows DNS — a public-looking name is not enough."""
    resolves("10.0.0.5")
    with pytest.raises(OutboundURLError):
        validate_outbound_url("https://totally-external.example.com/hook", make_settings())


def test_hostname_resolving_to_public_allowed(resolves):
    resolves("93.184.216.34")
    url = "https://hooks.example.com/services/abc"
    assert validate_outbound_url(url, make_settings()) == url


def test_unresolvable_host_fails_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(OutboundURLError):
        validate_outbound_url("https://nope.invalid/hook", make_settings())


# --- explicit allow-list -----------------------------------------------------
def test_allow_listed_host_permitted():
    """On-prem escape hatch: an admin-listed internal host is allowed."""
    settings = make_settings(OUTBOUND_ALLOWED_HOSTS="alerts.internal,10.0.0.5")
    url = "http://alerts.internal/hook"
    assert validate_outbound_url(url, settings) == url


def test_allow_list_is_host_specific():
    """Listing one host must not open every private address."""
    settings = make_settings(OUTBOUND_ALLOWED_HOSTS="alerts.internal")
    with pytest.raises(OutboundURLError):
        validate_outbound_url("http://169.254.169.254/latest/meta-data/", settings)


def test_empty_allow_list_blocks_everything_private():
    settings = make_settings(OUTBOUND_ALLOWED_HOSTS="")
    assert settings.outbound_allowed_hosts == set()
    with pytest.raises(OutboundURLError):
        validate_outbound_url("http://127.0.0.1:8000/", settings)
