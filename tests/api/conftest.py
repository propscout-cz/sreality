"""Shared auth fixture for the API tests: a real ES256-signed, Supabase-shaped JWT
verified against a stubbed JWKS — the production path through `verify_jwt`, never a
shared secret. A test that needs a human caller asks for `sign_jwt` and nothing else:
the fixture points `SUPABASE_URL` at a stub project and `deps._jwks_client` at the
key, then `sign_jwt({"sub": ...})` mints a token that key verifies."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from api import dependencies as deps


@pytest.fixture()
def sign_jwt(monkeypatch) -> Callable[..., str]:
    jwt = pytest.importorskip("jwt")  # PyJWT (api extra)
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())

    class _SigningKey:
        key = priv.public_key()

    class _Client:
        def get_signing_key_from_jwt(self, _token: str) -> _SigningKey:
            return _SigningKey()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(deps, "_jwks_client", lambda _url: _Client())

    def _sign(claims: dict | None = None) -> str:
        return jwt.encode({"aud": "authenticated", **(claims or {})}, priv, algorithm="ES256")

    return _sign
