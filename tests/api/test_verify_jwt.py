"""Phase 1 auth — verify_jwt (real Supabase JWTs only; the legacy static-token
branch that used to grant a synthetic admin identity has been retired)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import dependencies as deps

jwt = pytest.importorskip("jwt")  # PyJWT (api extra)

SECRET = "test-jwt-secret"


def _token(claims: dict) -> str:
    return jwt.encode({"aud": "authenticated", **claims}, SECRET, algorithm="HS256")


def test_missing_header_401():
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=None)
    assert ei.value.status_code == 401
    # RFC 6750 §3.1: a request with no credential gets the bare challenge, no error code.
    assert ei.value.headers["WWW-Authenticate"] == "Bearer"


def test_expired_token_401_names_the_reason(sign_jwt):
    """RFC 6750: the 401 says WHY, in the body and in the WWW-Authenticate challenge.
    A bare "Invalid token" told the 2026-10-13 preset incident nothing."""
    import time

    tok = sign_jwt({"sub": "abc", "exp": int(time.time()) - 60})
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=f"Bearer {tok}")
    assert ei.value.status_code == 401
    assert "expired" in ei.value.detail.lower()
    assert 'error="invalid_token"' in ei.value.headers["WWW-Authenticate"]


def test_legacy_static_token_no_longer_authenticates(monkeypatch):
    """The static API_TOKEN — extractable from the shipped SPA bundle via
    devtools — used to resolve to a synthetic {"is_admin": True, "legacy":
    True} identity here. That branch is gone: presenting it is now just an
    invalid JWT, same as any other garbage bearer value."""
    monkeypatch.setenv("API_TOKEN", "legacy-secret")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization="Bearer legacy-secret")
    assert ei.value.status_code == 401


def test_no_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization="Bearer whatever")
    assert ei.value.status_code == 503


def test_unknown_kid_401_never_echoes_the_token(monkeypatch):
    """The reason is a fixed word per failure class, never PyJWT's text: that text
    echoes the token's unverified `kid`, and a non-latin-1 or CR/LF value there broke
    the response header — a 500 any unauthenticated caller could trigger (W1 review).
    Runs the real PyJWKClient key lookup against a stubbed JWKS fetch."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric import ec

    served = ec.generate_private_key(ec.SECP256R1()).public_key()
    jwks = {"keys": [{**jwt.algorithms.ECAlgorithm.to_jwk(served, as_dict=True), "kid": "served-key"}]}
    client = jwt.PyJWKClient("https://example.supabase.co/auth/v1/.well-known/jwks.json")
    monkeypatch.setattr(client, "fetch_data", lambda: jwks)
    monkeypatch.setattr(deps, "_jwks_client", lambda _url: client)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    hostile = jwt.encode(
        {"aud": "authenticated"}, "any", algorithm="HS256",
        headers={"kid": '☃"\r\nX-Injected: 1'},
    )
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=f"Bearer {hostile}")
    assert ei.value.status_code == 401
    assert ei.value.detail == "Invalid token: unknown signing key"
    assert ei.value.headers["WWW-Authenticate"] == (
        'Bearer error="invalid_token", error_description="unknown signing key"'
    )


def test_asymmetric_es256_via_jwks(sign_jwt, monkeypatch):
    """The project's real path: ES256 verified against the public signing key
    (JWKS fetch stubbed by the fixture; the actual jwt.decode crypto is exercised)."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    claims = deps.verify_jwt(authorization=f"Bearer {sign_jwt({'sub': 'abc'})}")
    assert claims["sub"] == "abc"


def test_valid_supabase_jwt(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    tok = _token({"sub": "11111111-1111-1111-1111-111111111111"})
    claims = deps.verify_jwt(authorization=f"Bearer {tok}")
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"


def test_bad_signature_401(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    forged = jwt.encode({"aud": "authenticated", "sub": "x"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=f"Bearer {forged}")
    assert ei.value.status_code == 401


def test_require_admin_rejects_non_admin(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    tok = _token({"sub": "u", "is_admin": False})
    with pytest.raises(HTTPException) as ei:
        deps.require_admin(deps.verify_jwt(authorization=f"Bearer {tok}"))
    assert ei.value.status_code == 403


def test_require_admin_accepts_real_admin_jwt(monkeypatch):
    """The one path that must keep working: a real Supabase JWT whose
    app_metadata.is_admin claim is true (stamped from the admins table)."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    tok = _token({"sub": "u", "app_metadata": {"is_admin": True}})
    claims = deps.require_admin(deps.verify_jwt(authorization=f"Bearer {tok}"))
    assert claims["sub"] == "u"
