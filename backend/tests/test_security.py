"""HIGH-03 regression tests: PyJWT-backed token issuance/verification.

Confirms the python-jose -> PyJWT migration preserves the token contract
(payload shape, type-checking, expiry, signature checking) that
app/dependencies.py and app/routers/auth.py rely on.
"""

from __future__ import annotations

import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from conftest import make_settings


def test_password_hash_and_verify_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_access_token_roundtrip():
    settings = make_settings()
    token = create_access_token(settings, user_id=42)
    payload = decode_token(settings, token, expected_type="access")
    assert payload["sub"] == "42"
    assert payload["type"] == "access"


def test_refresh_token_rejected_when_expecting_access():
    settings = make_settings()
    token = create_refresh_token(settings, user_id=1)
    with pytest.raises(TokenError):
        decode_token(settings, token, expected_type="access")


def test_tampered_signature_rejected():
    settings = make_settings()
    token = create_access_token(settings, user_id=1)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(TokenError):
        decode_token(settings, tampered, expected_type="access")


def test_expired_token_rejected():
    settings = make_settings(JWT_ACCESS_TTL_SECONDS=-1)
    token = create_access_token(settings, user_id=1)
    with pytest.raises(TokenError):
        decode_token(settings, token, expected_type="access")


def test_token_signed_with_different_secret_rejected():
    settings = make_settings()
    other_settings = make_settings(JWT_SECRET="a-completely-different-test-secret-000")
    token = create_access_token(settings, user_id=1)
    with pytest.raises(TokenError):
        decode_token(other_settings, token, expected_type="access")
