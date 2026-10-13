"""The one way API tests mint a human caller: an ES256 token signed by a per-process
key that the conftest's autouse `jwks` fixture serves to `verify_jwt` — the production
verification path (JWKS), never a shared secret (the HS256 fallback was deleted
2026-10-13). A test that needs a human calls `mint({"sub": ...})` and nothing else."""

from __future__ import annotations

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

KEY = ec.generate_private_key(ec.SECP256R1())


class SigningKey:
    key = KEY.public_key()


class JwksClient:
    """Stands in for `deps._jwks_client`: every lookup answers with the test key."""

    def get_signing_key_from_jwt(self, _token: str) -> SigningKey:
        return SigningKey()


def mint(claims: dict | None = None) -> str:
    return jwt.encode({"aud": "authenticated", **(claims or {})}, KEY, algorithm="ES256")
