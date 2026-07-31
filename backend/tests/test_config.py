"""CRIT-02 regression tests: startup fails fast on placeholder/weak secrets."""

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
