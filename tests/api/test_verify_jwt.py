"""verify_jwt — real Supabase JWTs only, verified against the project's JWKS. The
legacy static-token branch (a synthetic admin identity) went 2026-08-04; the HS256
shared-secret fallback went 2026-10-13."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from api import dependencies as deps

jwt = pytest.importorskip("jwt")  # PyJWT (api extra)
from tests.api._jwt import mint  # noqa: E402


def test_missing_header_401():
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=None)
    assert ei.value.status_code == 401
    # RFC 6750 §3.1: a request with no credential gets the bare challenge, no error code.
    assert ei.value.headers["WWW-Authenticate"] == "Bearer"


def test_expired_token_401_names_the_reason():
    """RFC 6750: the 401 says WHY, in the body and in the WWW-Authenticate challenge.
    A bare "Invalid token" told the 2026-10-13 preset incident nothing."""
    tok = mint({"sub": "abc", "exp": int(time.time()) - 60})
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
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization="Bearer legacy-secret")
    assert ei.value.status_code == 401
    assert ei.value.detail == "Invalid token: malformed"


def test_unconfigured_fails_closed(monkeypatch):
    """No SUPABASE_URL → no verifier → 503, never a pass. There is no second
    verification path to fall back to."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-secret-nothing-reads")
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=f"Bearer {mint({'sub': 'abc'})}")
    assert ei.value.status_code == 503


def test_unknown_kid_401_never_echoes_the_token(monkeypatch):
    """The reason is a fixed word per failure class, never PyJWT's text: that text
    echoes the token's unverified `kid`, and a non-latin-1 or CR/LF value there broke
    the response header — a 500 any unauthenticated caller could trigger (W1 review).
    Runs the real PyJWKClient key lookup against a stubbed JWKS fetch."""
    from cryptography.hazmat.primitives.asymmetric import ec

    served = ec.generate_private_key(ec.SECP256R1()).public_key()
    jwks = {"keys": [{**jwt.algorithms.ECAlgorithm.to_jwk(served, as_dict=True), "kid": "served-key"}]}
    client = jwt.PyJWKClient("https://example.supabase.co/auth/v1/.well-known/jwks.json")
    monkeypatch.setattr(client, "fetch_data", lambda: jwks)
    monkeypatch.setattr(deps, "_jwks_client", lambda _url: client)
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


def test_valid_supabase_jwt(monkeypatch):
    """The project's real path: ES256 verified against the public signing key
    (JWKS fetch stubbed; the actual jwt.decode crypto is exercised)."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    claims = deps.verify_jwt(authorization=f"Bearer {mint({'sub': 'abc'})}")
    assert claims["sub"] == "abc"


def test_hs256_token_is_rejected_even_with_a_secret_set(monkeypatch):
    """A token signed with a shared secret never verifies: only the JWKS algorithms
    are accepted, and SUPABASE_JWT_SECRET is read by nothing."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "shared")
    hs = jwt.encode({"aud": "authenticated", "sub": "x"}, "shared", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=f"Bearer {hs}")
    assert ei.value.status_code == 401


def test_bad_signature_401():
    from cryptography.hazmat.primitives.asymmetric import ec

    forged = jwt.encode(
        {"aud": "authenticated", "sub": "x"},
        ec.generate_private_key(ec.SECP256R1()), algorithm="ES256",
    )
    with pytest.raises(HTTPException) as ei:
        deps.verify_jwt(authorization=f"Bearer {forged}")
    assert ei.value.status_code == 401
    assert ei.value.detail == "Invalid token: bad signature"


def test_require_admin_rejects_non_admin():
    tok = mint({"sub": "u", "is_admin": False})
    with pytest.raises(HTTPException) as ei:
        deps.require_admin(deps.verify_jwt(authorization=f"Bearer {tok}"))
    assert ei.value.status_code == 403


def test_require_admin_accepts_real_admin_jwt():
    """The one path that must keep working: a real Supabase JWT whose
    app_metadata.is_admin claim is true (stamped from the admins table)."""
    tok = mint({"sub": "u", "app_metadata": {"is_admin": True}})
    claims = deps.require_admin(deps.verify_jwt(authorization=f"Bearer {tok}"))
    assert claims["sub"] == "u"
