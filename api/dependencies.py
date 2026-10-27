"""FastAPI dependencies: per-request DB connection, shared SrealityClient, auth."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from fastapi import Depends, Header, HTTPException

from scraper import db
from scraper.sreality_client import SrealityClient

if TYPE_CHECKING:
    import psycopg


# The API opens a DB connection PER REQUEST, so the full batch-side handshake
# retry (3x10s) would hang request threads ~30s during a pooler outage. A quick
# single retry rides out a brief pooler blip without holding a thread through a
# sustained outage (the DB is down then anyway, so failing fast is correct). See
# scraper.db.connect's `attempts`/`retry_delay`.
_API_CONNECT_ATTEMPTS = 2
_API_CONNECT_RETRY_DELAY = 1.0


def get_db_conn() -> "Iterator[psycopg.Connection]":
    conn = db.connect(
        attempts=_API_CONNECT_ATTEMPTS, retry_delay=_API_CONNECT_RETRY_DELAY
    )
    try:
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def open_background_conn() -> "Iterator[psycopg.Connection]":
    """Open a dedicated service-role DB connection outside dependency injection.

    The request-scoped `get_db_conn` connection is closed once the HTTP
    response is sent, so background work that runs after the response
    must open its own. A route that may touch the service role only after
    an in-handler gate passes opens it here too, rather than as a dependency.
    """
    conn = db.connect(
        attempts=_API_CONNECT_ATTEMPTS, retry_delay=_API_CONNECT_RETRY_DELAY
    )
    try:
        yield conn
    finally:
        conn.close()


_CLIENT: SrealityClient | None = None


def get_sreality_client() -> SrealityClient:
    """Module-level singleton so the per-instance throttle persists across requests."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SrealityClient()
    return _CLIENT


_PROVIDERS: dict[str, Any] | None = None


def _build_providers() -> dict[str, Any]:
    """Construct provider singletons once. SDK clients lazy-init on first
    `.complete()`, so a missing API key here doesn't fail at boot — it
    fails at the request that tries to use it, with a clear ProviderError.
    """
    from api.providers.anthropic import AnthropicProvider
    from api.providers.gemini import GeminiProvider
    from api.providers.openai import OpenAIProvider
    from api.providers.oss import OssProvider
    from api.providers.qwen import QwenProvider
    return {
        "anthropic": AnthropicProvider(),
        "gemini":    GeminiProvider(),
        # Session-3 bake-off provider, promoted to production so a lane whose
        # app_settings model is a gpt-* id resolves (llm_client.provider_for_model).
        # Lazy key (OPENAI_API_KEY) — absent, it fails only at the call that uses it.
        "openai":    OpenAIProvider(),
        # W2-10's location bake-off candidate. `provider_for_model` has always routed a
        # `qwen*` id here and `llm_calls_provider_check` has always permitted 'qwen'
        # (migration 302) — but NOTHING constructed the provider, and an unregistered
        # provider raises in `LLMClient.call` BEFORE the try/except that writes the
        # failure row, so a qwen misroute left ZERO llm_calls evidence and was invisible
        # to the llm check's provider cells. Lazy key (QWEN_API_KEY).
        "qwen":      QwenProvider(),
        # Our own vLLM pod (autodedup/oss_pod.py), routed by the `oss:` id prefix. Its
        # base_url is an EPHEMERAL pod URL read from OSS_LLM_BASE_URL, so constructing it
        # here costs nothing and reaches nothing: with no pod up, the call — not the boot —
        # fails, and does so inside the try/except that writes the llm_calls failure row.
        "oss":       OssProvider(),
    }


def get_providers() -> dict[str, Any]:
    global _PROVIDERS
    if _PROVIDERS is None:
        _PROVIDERS = _build_providers()
    return _PROVIDERS


def get_llm_client(conn: Any = Depends(get_db_conn)) -> Any:
    """Per-request LLMClient bound to the request's DB connection.

    Imported lazily so the module loads in environments without the
    `anthropic` package (e.g. tests that don't exercise this path).
    """
    from api.llm_client import LLMClient
    return LLMClient(conn, providers=get_providers())


# ONE credential. Every caller — a person in the SPA or the extension, or a machine
# signed in as its own Supabase user — presents a Supabase session JWT; no static key
# exists (deleted 2026-10-13). The GATE decides what it needs from the JWT — the client
# never chooses per request — so a route moves between gates without a client change,
# and no shipped bundle holds a secret. `_bearer` is the one reader of the
# Authorization header; the three gates below differ only in what they return.


def _unauthorized(detail: str, *, reason: str | None = None) -> HTTPException:
    """Every 401 this module raises, RFC 6750-shaped: the `WWW-Authenticate: Bearer`
    challenge names the scheme, and `error="invalid_token"` + its description say WHY
    (expired, bad signature, not a token at all) instead of a bare "Invalid token".
    `reason` is always one of this module's fixed words — see `_jwt_reason`."""
    challenge = "Bearer"
    if reason is not None:
        detail = f"{detail}: {reason}"
        challenge += f' error="invalid_token", error_description="{reason}"'
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": challenge})


def _jwt_reason(exc: Exception) -> str:
    """A fixed, printable reason per PyJWT failure class — never the exception text,
    which echoes unverified token content (the header's `kid`): a non-latin-1 or
    CR/LF value there broke the response header, a 500 any unauthenticated caller
    could trigger."""
    import jwt

    for cls, reason in (
        (jwt.ExpiredSignatureError, "expired"),
        (jwt.InvalidSignatureError, "bad signature"),
        (jwt.InvalidAudienceError, "wrong audience"),
        (jwt.InvalidIssuerError, "wrong issuer"),
        (jwt.MissingRequiredClaimError, "missing claim"),
        (jwt.PyJWKClientError, "unknown signing key"),
    ):
        if isinstance(exc, cls):
            return reason
    return "malformed"


def _bearer(authorization: str | None) -> dict | None:
    """Classify the one Authorization header: a verified Supabase JWT → its claims;
    nothing else is a credential (401; no verifier configured → 503). Local dev may
    switch this gate off with API_AUTH_OPTIONAL=1, which makes every caller anonymous
    (None) — never set it in production: it would open every require_token route."""
    if os.environ.get("API_AUTH_OPTIONAL") == "1":
        return None
    return verify_jwt(authorization)


# Any one marks a Railway deployment: its documented system variables, plus the legacy name the spec cites.
_RAILWAY_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_ENVIRONMENT_ID",
                    "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID")


def refuse_auth_optional_on_railway() -> None:
    """Boot guard: on Railway the local-dev opt-out would open every require_token route to anyone (M0, #2059)."""
    marker = next((m for m in _RAILWAY_MARKERS if os.environ.get(m)), None)
    if os.environ.get("API_AUTH_OPTIONAL") == "1" and marker:
        raise RuntimeError(
            f"API_AUTH_OPTIONAL=1 is a local-dev switch and is refused on Railway ({marker} is set): "
            "unset it on this service"
        )


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Any signed-in caller — a verified Supabase JWT — with no identity returned.
    A route that needs one takes verify_jwt / tenant_conn."""
    _bearer(authorization)


def token_claims(authorization: str | None = Header(default=None)) -> dict | None:
    """require_token's gate, returning the claims (None under the local-dev opt-out) for a flag gated on
    admin, as a forced refresh is; never a scope: identity routes take verify_jwt / tenant_conn."""
    return _bearer(authorization)


# Fixed system account id (mirrors migrations/286_accounts_foundation.sql). The
# fallback owner for a run/write whose caller has no resolvable account.
SYSTEM_ACCOUNT_ID = "00000000-0000-0000-0000-000000000000"

_JWKS_CLIENT: Any = None


# PyJWT's default JWKS cache lifespan is 300 s, so once every five minutes the
# NEXT identity-gated request pays a blocking outbound HTTPS fetch to Supabase's
# JWKS endpoint before it may even look at the token — on one uvicorn worker that
# stalls the whole process, on top of the ~270-410 ms floor every Railway call
# already pays. Rotation stays safe at an hour: PyJWKClient.get_signing_key falls
# through to get_signing_keys(refresh=True) whenever the token's `kid` is absent
# from the cached set, so a newly rotated key is fetched on its first use rather
# than waited for. The lifespan is the staleness ceiling for keys we no longer
# need, not the latency of adopting a new one.
_JWKS_CACHE_SECONDS = 3600


def _jwks_client(jwks_url: str) -> Any:
    """Cached PyJWKClient — fetches + caches the project's public signing keys
    (no per-request network call). Instantiated once per process."""
    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        import jwt
        _JWKS_CLIENT = jwt.PyJWKClient(jwks_url, lifespan=_JWKS_CACHE_SECONDS)
    return _JWKS_CLIENT


def verify_jwt(authorization: str | None = Header(default=None)) -> dict:
    """Verify a Supabase user JWT and return its claims — the credential check behind
    `_bearer` and the gate of every identity route.

    One verification path: the project's asymmetric signing keys (ES256/RS256)
    against its public JWKS, built from SUPABASE_URL — no shared secret anywhere
    (the HS256 `SUPABASE_JWT_SECRET` fallback was deleted 2026-10-13; production has
    signed ES256 since the JWKS cutover). Fails closed (503) with SUPABASE_URL unset:
    an unconfigured auth backend must never authenticate anyone.
    """
    # The verifier check comes FIRST: a deployment that cannot verify anyone answers
    # 503 to every request, header or not — a server fault must never read as a
    # client's missing credential.
    base = os.environ.get("SUPABASE_URL")
    if not base:
        raise HTTPException(status_code=503, detail="Auth is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("Missing bearer token")
    token = authorization[len("Bearer "):]

    import jwt  # PyJWT (api extra); imported lazily to keep boot light

    # A user's session only as Supabase Auth issues one (M0, #2059): this project's issuer (GoTrue stamps
    # `<API_EXTERNAL_URL>/auth/v1`), audience and role `authenticated`, a subject and an expiry. A token
    # carrying `client_id` went to an OAuth client, for another audience.
    issuer = base.rstrip("/") + "/auth/v1"
    try:
        signing_key = _jwks_client(issuer + "/.well-known/jwks.json").get_signing_key_from_jwt(token)
        # The PyJWK, not its raw key: PyJWT then verifies with the key's own algorithm, so a header naming
        # another one fails as a token error (401) instead of a TypeError (500).
        claims = jwt.decode(
            token, signing_key,
            algorithms=["ES256", "RS256"], audience="authenticated", issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub", "role"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid token", reason=_jwt_reason(exc)) from exc
    if claims["role"] != "authenticated":
        raise _unauthorized("Invalid token", reason="wrong role")
    if "client_id" in claims:
        raise _unauthorized("Invalid token", reason="client token")
    return claims


def account_scope(
    authorization: str | None = Header(default=None),
    conn: Any = Depends(get_db_conn),
) -> list[str]:
    """Read scope for the account-scoped estimation reads, over the one Authorization
    header.

    Returns the account ids the caller may read, ALWAYS including SYSTEM — mirroring
    the estimation_runs_tenant_read policy (migration 291) rather than inventing a
    second definition of tenancy. A verified JWT adds its own account; a caller with
    no resolvable account, and the local-dev opt-out, stay SYSTEM-only (the opt-out can
    therefore never widen scope to another account's rows). Fails CLOSED at every
    branch — `_bearer` raises; nothing degrades to a wider or narrower row set.

    Why not require_token + tenant_conn: `listings` is RLS-enabled-with-zero-policies,
    so the /estimations list query (which LEFT JOINs it for locality_display) returns
    NULL for every row on a tenant connection — silently, no error. The service-role
    connection plus an explicit predicate is the only shape that scopes without
    blanking data.
    """
    claims = _bearer(authorization)
    if claims is None:
        return [SYSTEM_ACCOUNT_ID]
    from api import tenant_pool  # lazy: tenant_pool imports this module

    account_id = tenant_pool.resolve_account_id(conn, claims)
    if account_id is None:
        return [SYSTEM_ACCOUNT_ID]
    return [str(account_id), SYSTEM_ACCOUNT_ID]


def is_admin(claims: dict) -> bool:
    """The is_admin claim, top-level or under app_metadata — require_admin's test."""
    meta = claims.get("app_metadata") or {}
    return claims.get("is_admin") is True or meta.get("is_admin") is True


def require_admin(claims: dict = Depends(verify_jwt)) -> dict:
    """Gate admin-only routes on the is_admin claim (stamped onto the JWT's
    app_metadata from the admins table). Requires a real Supabase JWT — see
    verify_jwt's docstring."""
    if not is_admin(claims):
        raise HTTPException(status_code=403, detail="Admin only")
    return claims


def require_admin_to_force(force_refresh: bool, claims: dict | None) -> None:
    """A forced refresh re-buys a cached LLM answer: an admin's alone (403, the opt-out's anonymous caller
    included); a plain read of the cached answer stays open to the route's callers (M0, #2059)."""
    if force_refresh and (claims is None or not is_admin(claims)):
        raise HTTPException(status_code=403, detail="Admin only: force_refresh")
