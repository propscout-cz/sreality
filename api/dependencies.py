"""FastAPI dependencies: per-request DB connection, shared SrealityClient, auth."""

from __future__ import annotations

import contextlib
import hmac
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
        # to llm_errors and llm_burn_rate alike. Lazy key (QWEN_API_KEY).
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


_TRANSPORTS: dict[str, Any] | None = None


def _build_transports() -> dict[str, Any]:
    """Construct channel-transport singletons once (the `_build_providers`
    mirror for notification delivery). Each transport reads its own secret
    lazily and raises only on `send()`, so a missing key never fails boot.

    Each transport reads its own secret lazily and raises only on `send()`, so
    a missing key never fails boot; `is_configured()` lets a caller skip an
    unconfigured channel. A transport only delivers once its secret is set AND a
    watchdog/collection opts into its channel (so `target_channels` is non-empty):
    email = Resend (`RESEND_API_KEY` + `EMAIL_FROM`), telegram = Bot API
    (`TELEGRAM_BOT_TOKEN`). Adding a channel is one import + one entry here.
    """
    from api.transports.email_resend import ResendEmail
    from api.transports.telegram import Telegram
    return {"email": ResendEmail(), "telegram": Telegram()}


def get_transports() -> dict[str, Any]:
    global _TRANSPORTS
    if _TRANSPORTS is None:
        _TRANSPORTS = _build_transports()
    return _TRANSPORTS


def get_channel_client(conn: Any = Depends(get_db_conn)) -> Any:
    """Per-request ChannelClient bound to the request's DB connection (the
    `get_llm_client` mirror). Imported lazily to keep import-time light."""
    from api.channel_client import ChannelClient
    return ChannelClient(conn, transports=get_transports())


# ONE caller, ONE credential. A human (the SPA, the extension) presents the Supabase
# session JWT; a machine (ClickUp, a script) presents the static API_TOKEN. The GATE
# decides what it accepts — the client never chooses per request — so a route moves
# between gates without a client change, and no shipped bundle holds a secret.
# `_bearer` is the one reader of the Authorization header; the three gates below
# differ only in what they return.


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
        (jwt.PyJWKClientError, "unknown signing key"),
    ):
        if isinstance(exc, cls):
            return reason
    return "malformed"


def _bearer(authorization: str | None) -> dict | None:
    """Classify the one Authorization header: the static API_TOKEN → None (a known
    machine, no identity); a Supabase JWT → its verified claims; anything else → 401.

    Fails CLOSED: with API_TOKEN unset the API refuses every request (503) UNLESS the
    operator explicitly opts out for local dev with API_AUTH_OPTIONAL=1, which makes
    every caller the identity-less machine. A forgotten prod secret can therefore
    never silently disable auth. The static compare is timing-safe and runs on bytes:
    hmac.compare_digest on two str raises TypeError for a non-ASCII header (Starlette
    decodes headers as latin-1), which would surface as a 500 instead of a clean 401."""
    expected = os.environ.get("API_TOKEN")
    if not expected:
        if os.environ.get("API_AUTH_OPTIONAL") == "1":
            return None
        raise HTTPException(
            status_code=503,
            detail="API auth is not configured (set API_TOKEN, or API_AUTH_OPTIONAL=1 for local dev)",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("Missing bearer token")
    token = authorization[len("Bearer "):]
    if hmac.compare_digest(token.encode("utf-8", "ignore"), expected.encode("utf-8")):
        return None
    # Not the shared secret, so it must be a JWT — where this deployment can verify
    # one. Where it cannot (API_TOKEN only, no SUPABASE_URL: a machine-only
    # deployment, local dev, CI) the static token is the only credential there is,
    # so this is a bad credential (401), not a server fault; verify_jwt's 503 is for
    # routes that NEED a JWT.
    if not os.environ.get("SUPABASE_URL"):
        raise _unauthorized("Invalid token", reason="unknown credential")
    return verify_jwt(authorization)


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Any known caller — the static API_TOKEN or a verified Supabase JWT — with no
    identity returned. A route that needs one takes verify_jwt / tenant_conn."""
    _bearer(authorization)


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
    """Verify a Supabase user JWT and return its claims — the human half of `_bearer`
    and the gate of every identity route.

    One verification path: the project's asymmetric signing keys (ES256/RS256)
    against its public JWKS, built from SUPABASE_URL — no shared secret anywhere
    (the HS256 `SUPABASE_JWT_SECRET` fallback was deleted 2026-10-13; production has
    signed ES256 since the JWKS cutover). The static API_TOKEN is NOT accepted here
    (retired 2026-08-04: that branch made every require_admin route only as protected
    as a token embedded in the shipped SPA bundle). Fails closed (503) with
    SUPABASE_URL unset: an unconfigured auth backend must never authenticate anyone.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("Missing bearer token")
    token = authorization[len("Bearer "):]
    base = os.environ.get("SUPABASE_URL")
    if not base:
        raise HTTPException(status_code=503, detail="Auth is not configured")

    import jwt  # PyJWT (api extra); imported lazily to keep boot light

    jwks_url = base.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token, signing_key.key,
            algorithms=["ES256", "RS256"], audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid token", reason=_jwt_reason(exc)) from exc


def account_scope(
    authorization: str | None = Header(default=None),
    conn: Any = Depends(get_db_conn),
) -> list[str]:
    """Read scope for routes that serve BOTH a browser session and a machine caller
    (MCP / ClickUp / script integrations) over the one Authorization header.

    Returns the account ids the caller may read, ALWAYS including SYSTEM — mirroring
    the estimation_runs_tenant_read policy (migration 291) rather than inventing a
    second definition of tenancy. A verified JWT adds its own account; the static
    token is NOT an identity, so it stays SYSTEM-only (as does the local-dev opt-out,
    which can therefore never widen scope to another account's rows). Fails CLOSED at
    every branch — `_bearer` raises; nothing degrades to a wider or narrower row set.

    Why not require_token + tenant_conn: `listings` and `parsed_url_cache` are
    RLS-enabled-with-zero-policies, so the /estimations list query (which LEFT JOINs
    both for locality_display) returns NULL for every row on a tenant connection —
    silently, no error. The service-role connection plus an explicit predicate is the
    only shape that scopes without blanking data.
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
