"""Push-trigger logic: webhook authentication and payload parsing.

``verify_webhook_signature`` is an authentication boundary — the Nexus webhook
endpoint is reachable without a user session — so it gets the same treatment as
the JWT code.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.modules.nexus.events import parse_webhook_payload, verify_webhook_signature

SECRET = "a-shared-webhook-secret"
BODY = b'{"action":"CREATED"}'


def _sign(body: bytes, algorithm=hashlib.sha1, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, algorithm).hexdigest()


# --- signature verification --------------------------------------------------
def test_valid_sha1_signature_accepted():
    assert verify_webhook_signature(SECRET, BODY, _sign(BODY))


def test_valid_sha256_signature_accepted():
    """Accepted so a future Nexus that upgrades the digest keeps working."""
    assert verify_webhook_signature(SECRET, BODY, _sign(BODY, hashlib.sha256))


def test_signature_is_case_insensitive():
    assert verify_webhook_signature(SECRET, BODY, _sign(BODY).upper())


def test_signature_for_different_body_rejected():
    assert not verify_webhook_signature(SECRET, b'{"action":"DELETED"}', _sign(BODY))


def test_signature_from_different_secret_rejected():
    forged = _sign(BODY, secret="the-attackers-guess")
    assert not verify_webhook_signature(SECRET, BODY, forged)


@pytest.mark.parametrize("secret,signature", [
    ("", _sign(BODY)),   # no secret configured
    (SECRET, ""),        # header absent
    (SECRET, "garbage"),
])
def test_missing_or_malformed_inputs_rejected(secret, signature):
    assert not verify_webhook_signature(secret, BODY, signature)


# --- payload parsing ---------------------------------------------------------
def _payload(**overrides):
    base = {
        "action": "CREATED",
        "repositoryName": "docker-hosted",
        "component": {"format": "docker", "name": "nginx", "version": "1.25"},
    }
    base.update(overrides)
    return base


def test_docker_create_event_parsed():
    repo, ref = parse_webhook_payload(_payload())
    assert repo == "docker-hosted"
    assert ref.image == "nginx:1.25"


def test_non_docker_format_ignored():
    assert parse_webhook_payload(
        _payload(component={"format": "maven2", "name": "a", "version": "1"})
    ) is None


def test_delete_action_ignored():
    assert parse_webhook_payload(_payload(action="DELETED")) is None


@pytest.mark.parametrize("component", [
    {"format": "docker", "name": "nginx"},              # no version
    {"format": "docker", "version": "1.25"},            # no name
    {},
])
def test_incomplete_component_ignored(component):
    assert parse_webhook_payload(_payload(component=component)) is None


def test_missing_repository_ignored():
    assert parse_webhook_payload(_payload(repositoryName=None)) is None


def test_empty_payload_ignored():
    assert parse_webhook_payload({}) is None
