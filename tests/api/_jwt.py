"""The one way API tests mint a human caller: an ES256 token signed by a per-process
key that the conftest's autouse `jwks` fixture serves to `verify_jwt` — the production
verification path (JWKS), never a shared secret (the HS256 fallback was deleted
2026-10-13). A test that needs a human calls `mint({"sub": ...})` and nothing else.

The token is shaped as Supabase Auth issues one (issuer, expiry, role, audience), so a
test names only what it is about; `drop` removes a claim a test wants absent."""

from __future__ import annotations

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

KEY = ec.generate_private_key(ec.SECP256R1())

# The conftest's SUPABASE_URL; verify_jwt pins `<SUPABASE_URL>/auth/v1`.
PROJECT_URL = "https://example.supabase.co"
ISSUER = PROJECT_URL + "/auth/v1"


class JwksClient:
    """Stands in for `deps._jwks_client`: every lookup answers with the test key, as the PyJWK the real client returns."""

    def get_signing_key_from_jwt(self, _token: str) -> jwt.PyJWK:
        return jwt.PyJWK({**jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "alg": "ES256"})


def mint(claims: dict | None = None, *, drop: tuple[str, ...] = ()) -> str:
    body = {"aud": "authenticated", "iss": ISSUER, "role": "authenticated",
            "exp": int(time.time()) + 3600, **(claims or {})}
    for name in drop:
        body.pop(name, None)
    return jwt.encode(body, KEY, algorithm="ES256")
