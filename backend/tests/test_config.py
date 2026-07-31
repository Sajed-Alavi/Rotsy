"""Startup-validation regression tests.

CRIT-02 — placeholder/weak bootstrap secrets are rejected.
MED-01  — NEXUS_CONFIG_ENCRYPTION_KEY is required and must differ from JWT_SECRET.
MED-06  — COOKIE_SECURE=false is refused alongside an https FRONTEND_ORIGIN.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import make_settings


@pytest.mark.parametrize("placeholder", ["change-me", "changeme", "CHANGE-ME", "generate-a-32-byte-hex-secret"])
def test_placeholder_jwt_secret_rejected(placeholder):
    with pytest.raises(ValidationError):
        make_settings(JWT_SECRET=placeholder)


def test_short_jwt_secret_rejected():
    with pytest.raises(ValidationError):
        make_settings(JWT_SECRET="too-short")


def test_real_jwt_secret_accepted():
    settings = make_settings(JWT_SECRET="a" * 32)
    assert settings.JWT_SECRET == "a" * 32


@pytest.mark.parametrize("placeholder", ["change-me", "admin", "password"])
def test_placeholder_admin_password_rejected(placeholder):
    with pytest.raises(ValidationError):
        make_settings(BOOTSTRAP_ADMIN_PASSWORD=placeholder)


def test_short_admin_password_rejected():
    with pytest.raises(ValidationError):
        make_settings(BOOTSTRAP_ADMIN_PASSWORD="short1")


def test_real_admin_password_accepted():
    settings = make_settings(BOOTSTRAP_ADMIN_PASSWORD="a-strong-unique-password-42")
    assert settings.BOOTSTRAP_ADMIN_PASSWORD == "a-strong-unique-password-42"


# --- MED-01: dedicated at-rest encryption key -------------------------------
def test_missing_encryption_key_rejected():
    """The JWT_SECRET fallback is gone — an empty key must fail startup."""
    with pytest.raises(ValidationError):
        make_settings(NEXUS_CONFIG_ENCRYPTION_KEY="")


def test_short_encryption_key_rejected():
    with pytest.raises(ValidationError):
        make_settings(NEXUS_CONFIG_ENCRYPTION_KEY="too-short")


@pytest.mark.parametrize("placeholder", ["change-me", "replace-me", "REPLACE_WITH_OPENSSL_RAND_HEX_32"])
def test_placeholder_encryption_key_rejected(placeholder):
    with pytest.raises(ValidationError):
        make_settings(NEXUS_CONFIG_ENCRYPTION_KEY=placeholder)


def test_encryption_key_equal_to_jwt_secret_rejected():
    """Reusing the signing key as the at-rest key reintroduces MED-01."""
    shared = "the-very-same-secret-0123456789abcdef"
    with pytest.raises(ValidationError):
        make_settings(JWT_SECRET=shared, NEXUS_CONFIG_ENCRYPTION_KEY=shared)


def test_distinct_encryption_key_accepted():
    settings = make_settings(
        JWT_SECRET="a" * 32, NEXUS_CONFIG_ENCRYPTION_KEY="b" * 32
    )
    assert settings.NEXUS_CONFIG_ENCRYPTION_KEY == "b" * 32


# --- MED-06: cookie Secure flag ---------------------------------------------
def test_insecure_cookie_with_https_origin_rejected():
    with pytest.raises(ValidationError):
        make_settings(COOKIE_SECURE=False, FRONTEND_ORIGIN="https://sharpy.example.com")


def test_insecure_cookie_with_http_origin_allowed():
    """Local plain-HTTP development stays possible — it only warns."""
    settings = make_settings(COOKIE_SECURE=False, FRONTEND_ORIGIN="http://localhost:8080")
    assert settings.COOKIE_SECURE is False


def test_secure_cookie_with_https_origin_allowed():
    settings = make_settings(COOKIE_SECURE=True, FRONTEND_ORIGIN="https://sharpy.example.com")
    assert settings.COOKIE_SECURE is True
