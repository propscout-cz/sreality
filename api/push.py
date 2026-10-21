"""Web Push to Apple's push service (notifications push W4, PROGRAM.md § 3.4): RFC 8291 aes128gcm, RFC 8292 VAPID.

A push endpoint is a secret capability URL: nothing here logs it, and no Outcome carries it (a transport error is
its class name). `VAPID_PRIVATE_KEY` is the P-256 scalar, 32 bytes base64url; its public key is derived, never set.
"""

from __future__ import annotations

import base64
import functools
import json
import os
import re
import struct
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import jwt
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VAPID_ENV = "VAPID_PRIVATE_KEY"
# The database's CHECK (migration 654), so a stored endpoint and a POSTed one are the same set.
APPLE_ENDPOINT = re.compile(r"^https://[a-z0-9-]+(\.[a-z0-9-]+)*\.push\.apple\.com/")
RECORD_SIZE = 4096
JWT_LIFETIME_S = 12 * 3600
_POINT = (serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
_WORD = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_JWTS: dict[tuple[str, str, str], tuple[str, float]] = {}


@dataclass(frozen=True)
class Outcome:
    kind: Literal["sent", "gone", "throttled", "retry", "failed"]
    reason: str | None = None
    provider_message_id: str | None = None
    retry_after_s: int | None = None


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)


@functools.lru_cache(maxsize=4)
def private_key(raw: str) -> ec.EllipticCurvePrivateKey:
    try:
        scalar = unb64url(raw.strip())
        if len(scalar) == 32:
            return ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())
    except ValueError:
        pass
    raise ValueError(f"{VAPID_ENV} is not a base64url 32-byte P-256 scalar")


def public_key_b64url(raw: str) -> str:
    """The 65-byte uncompressed point the browser subscribes with (`applicationServerKey`), base64url."""
    return b64url(private_key(raw).public_key().public_bytes(*_POINT))


def vapid_authorization(endpoint_origin: str, device_origin: str, raw: str, *, now: float | None = None) -> str:
    """`vapid t=<ES256 JWT>, k=<public key>`: aud the push service, sub the app's https origin, exp 12 h; one JWT
    per (aud, sub) is reused for 11 h, so every token it hands out has an hour left."""
    now = time.time() if now is None else now
    k = public_key_b64url(raw)
    token, expires = _JWTS.get((endpoint_origin, device_origin, k), ("", 0.0))
    if now > expires - 3600:
        expires = now + JWT_LIFETIME_S
        claims = {"aud": endpoint_origin, "exp": int(expires), "sub": device_origin}
        token = jwt.encode(claims, private_key(raw), algorithm="ES256", headers={"typ": "JWT"})
        _JWTS[(endpoint_origin, device_origin, k)] = (token, expires)
    return f"vapid t={token}, k={k}"


def encrypt(plaintext: bytes, p256dh: bytes, auth: bytes, *, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """One aes128gcm record (RFC 8291 § 3.4, RFC 8188): header salt|rs|idlen|keyid, then the ciphertext."""
    if len(plaintext) + 17 > RECORD_SIZE:
        raise ValueError("the payload does not fit one record")
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = server_key.public_key().public_bytes(*_POINT)
    secret = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh))
    ikm = _hkdf(auth, secret, b"WebPush: info\x00" + p256dh + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + record


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def apple_host(endpoint: str) -> str | None:
    """The push service's host when `endpoint` is https on *.push.apple.com with no userinfo or port, else None."""
    if not endpoint.isascii() or any(c.isspace() for c in endpoint) or not APPLE_ENDPOINT.match(endpoint):
        return None
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname or ""
    if parts.scheme != "https" or port is not None or parts.username or parts.password:
        return None
    return host if host.endswith(".push.apple.com") else None


def send(endpoint: str, device_origin: str, p256dh: str, auth: str, payload: dict[str, Any], *, ttl_s: int,
         urgency: str = "high", topic: str | None = None,
         timeout: tuple[float, float] = (3.05, 6.0)) -> Outcome:
    """POST one encrypted push; the endpoint's host is re-checked first, and redirects are never followed. The
    (connect, read) timeout keeps a pass of ten devices inside the push lane's 2-minute lease."""
    host = apple_host(endpoint)
    if host is None:
        return Outcome("failed", "BadEndpoint")
    body = encrypt(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
                   unb64url(p256dh), unb64url(auth))
    headers = {"Authorization": vapid_authorization(f"https://{host}", device_origin, os.environ.get(VAPID_ENV, "")),
               "TTL": str(max(60, int(ttl_s))), "Urgency": urgency, "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream"}
    if topic:
        headers["Topic"] = topic
    try:
        resp = requests.post(endpoint, data=body, headers=headers, timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        return Outcome("retry", type(exc).__name__)
    return _outcome(resp)


def _outcome(resp: requests.Response) -> Outcome:
    """201 sent; 404, 410 or VapidPkHashMismatch gone; 429 throttled; 5xx retry; anything else failed."""
    status, reason = resp.status_code, _apple_reason(resp)
    if 200 <= status < 300:
        message_id = resp.headers.get("apns-id") or ""
        return Outcome("sent", provider_message_id=message_id if _MESSAGE_ID.match(message_id) else None)
    if status in (404, 410) or reason == "VapidPkHashMismatch":
        return Outcome("gone", reason or str(status))
    if status == 429:
        after = (resp.headers.get("Retry-After") or "").strip()
        wait = max(1, min(int(after), 3600)) if after.isdigit() else 60
        return Outcome("throttled", reason or "429", retry_after_s=wait)
    return Outcome("retry" if status >= 500 else "failed", reason or str(status))


def _apple_reason(resp: requests.Response) -> str | None:
    """Apple's one-word `reason` from a JSON error body, or None: no other text of a response is ever kept."""
    try:
        reason = resp.json().get("reason")
    except (ValueError, AttributeError):
        return None
    return reason if isinstance(reason, str) and _WORD.match(reason) else None
