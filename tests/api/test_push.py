"""api/push.py, hermetic: RFC 8291's own vector byte for byte, the VAPID JWT against the derived key, the one key
format, the request's headers and the outcome of every answer Apple gives, and the endpoint never leaving the
transport. No test reaches Apple: `requests.post` is faked wherever a send runs."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from api import push

# RFC 8291 Appendix A.
_RFC = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "body": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tlz"
            "AC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}
ENDPOINT = "https://web.push.apple.com/QGuHfVhTMmGc4fZDuWs8fIY4ELbdpXFgx8ibAmN3UDPqx0QvGhIA9xlu7Ok"
ORIGIN = "https://app.example.cz"


def _scalar(b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(push.unb64url(b64), "big"), ec.SECP256R1())


def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def _decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """The user agent's side of RFC 8291, written out independently of api/push.py."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public, record = body[21:21 + idlen], body[21 + idlen:]
    assert rs == 4096 and idlen == 65 and len(record) <= rs
    ua_public = ua_private.public_key().public_bytes(*push._POINT)
    secret = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hkdf(auth, secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    plain = AESGCM(_hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(
        _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12), record, None)
    assert plain.endswith(b"\x02"), "the last record's delimiter"
    return plain[:-1]


@pytest.fixture()
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    raw = push.b64url(os.urandom(32))
    monkeypatch.setenv(push.VAPID_ENV, raw)
    push._JWTS.clear()
    return raw


@pytest.fixture()
def device() -> tuple[ec.EllipticCurvePrivateKey, str, str, bytes]:
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    return ua, push.b64url(ua.public_key().public_bytes(*push._POINT)), push.b64url(auth), auth


class _Resp:
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self) -> Any:
        if self._body is None:
            raise requests.JSONDecodeError("no body", "", 0)
        return self._body


@pytest.fixture()
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> Any:
        calls.append({"url": url, **kwargs})
        return _Resp(201, headers={"apns-id": "6A1C9C5E-2D2B-4F7B-9A43-1F0E6C3B2A10"})

    monkeypatch.setattr(push.requests, "post", post)
    return calls


def _answer(monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    def post(url: str, **kwargs: Any) -> Any:
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(push.requests, "post", post)


# --- RFC 8291 -------------------------------------------------------------------------------


def test_the_rfc_8291_appendix_a_vector_byte_for_byte() -> None:
    server = _scalar(_RFC["as_private"])
    assert push.b64url(server.public_key().public_bytes(*push._POINT)) == _RFC["as_public"]
    body = push.encrypt(_RFC["plaintext"], push.unb64url(_RFC["ua_public"]), push.unb64url(_RFC["auth"]),
                        salt=push.unb64url(_RFC["salt"]), server_key=server)
    assert push.b64url(body) == _RFC["body"]
    assert _decrypt(body, _scalar(_RFC["ua_private"]), push.unb64url(_RFC["auth"])) == _RFC["plaintext"]


def test_a_fresh_encryption_round_trips_with_a_new_salt_and_key_each_time(device: Any) -> None:
    ua, p256dh, _auth_b64, auth = device
    plain = json.dumps({"web_push": 8030, "notification": {"title": "Zlevněno · Praha 5"}}).encode()
    one = push.encrypt(plain, push.unb64url(p256dh), auth)
    two = push.encrypt(plain, push.unb64url(p256dh), auth)
    assert one[:16] != two[:16] and one[21:86] != two[21:86], "a new salt and ephemeral key per push"
    assert _decrypt(one, ua, auth) == plain == _decrypt(two, ua, auth)


def test_one_record_holds_at_most_4079_bytes(device: Any) -> None:
    _ua, p256dh, _a, auth = device
    assert len(push.encrypt(b"x" * 4079, push.unb64url(p256dh), auth)) == 16 + 4 + 1 + 65 + 4096
    with pytest.raises(ValueError):
        push.encrypt(b"x" * 4080, push.unb64url(p256dh), auth)


# --- VAPID ----------------------------------------------------------------------------------


def test_the_key_is_a_base64url_32_byte_scalar_and_its_error_names_no_key() -> None:
    raw = push.b64url(os.urandom(32))
    point = push.unb64url(push.public_key_b64url(raw))
    assert len(point) == 65 and point[0] == 4
    for bad in ("", "abc", push.b64url(os.urandom(31)), "-----BEGIN PRIVATE KEY-----\nMIGHAgEA\n-----END",
                raw + "!!", push.b64url(b"\x00" * 32)):
        with pytest.raises(ValueError) as exc:
            push.private_key(bad)
        assert str(exc.value) == "VAPID_PRIVATE_KEY is not a base64url 32-byte P-256 scalar"


def test_the_vapid_jwt_verifies_with_the_derived_public_key(key: str) -> None:
    now = time.time()
    header = push.vapid_authorization("https://web.push.apple.com", ORIGIN, key, now=now)
    assert header.startswith("vapid t=") and ", k=" in header
    token, k = header[len("vapid t="):].split(", k=")
    assert k == push.public_key_b64url(key)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64url(k))
    claims = jwt.decode(token, public, algorithms=["ES256"], audience="https://web.push.apple.com")
    assert claims["sub"] == ORIGIN and now < claims["exp"] <= now + 12 * 3600 + 1
    assert jwt.get_unverified_header(token) == {"alg": "ES256", "typ": "JWT"}


def test_one_jwt_per_audience_and_subject_is_reused_for_eleven_hours(key: str) -> None:
    at = 1_800_000_000.0
    first = push.vapid_authorization("https://web.push.apple.com", ORIGIN, key, now=at)
    assert push.vapid_authorization("https://web.push.apple.com", ORIGIN, key, now=at + 11 * 3600 - 1) == first
    assert push.vapid_authorization("https://web.push.apple.com", ORIGIN, key, now=at + 11 * 3600 + 1) != first
    assert push.vapid_authorization("https://api.push.apple.com", ORIGIN, key, now=at) != first
    assert push.vapid_authorization("https://web.push.apple.com", "https://other.cz", key, now=at) != first


# --- send -----------------------------------------------------------------------------------


def test_a_push_is_encrypted_signed_and_bounded(key: str, device: Any, posted: list[dict[str, Any]]) -> None:
    ua, p256dh, auth_b64, auth = device
    payload = {"web_push": 8030, "notification": {"title": "Nový inzerát · Brno", "lang": "cs"}}
    out = push.send(ENDPOINT, ORIGIN, p256dh, auth_b64, payload, ttl_s=5, topic="a" * 22)
    assert out == push.Outcome("sent", provider_message_id="6A1C9C5E-2D2B-4F7B-9A43-1F0E6C3B2A10")
    [call] = posted
    assert call["url"] == ENDPOINT and call["allow_redirects"] is False and call["timeout"] == (3.05, 6.0)
    headers = call["headers"]
    assert int(headers["TTL"]) >= 60 and headers["TTL"] == "60", "TTL is never below a minute (BadTtl)"
    assert (headers["Urgency"], headers["Topic"], headers["Content-Encoding"]) == ("high", "a" * 22, "aes128gcm")
    token = headers["Authorization"][len("vapid t="):].split(", k=")[0]
    assert jwt.decode(token, options={"verify_signature": False})["aud"] == "https://web.push.apple.com"
    assert json.loads(_decrypt(call["data"], ua, auth)) == payload


def test_a_ttl_above_the_floor_is_sent_as_given(key: str, device: Any, posted: list[dict[str, Any]]) -> None:
    _ua, p256dh, auth_b64, _a = device
    push.send(ENDPOINT, ORIGIN, p256dh, auth_b64, {}, ttl_s=86_000)
    assert posted[0]["headers"]["TTL"] == "86000" and "Topic" not in posted[0]["headers"]


@pytest.mark.parametrize(("resp", "expected"), [
    (_Resp(201, headers={"apns-id": "C2E1-77"}), push.Outcome("sent", provider_message_id="C2E1-77")),
    (_Resp(201, headers={"apns-id": "not an id; drop table"}), push.Outcome("sent")),
    (_Resp(410, {"reason": "Unregistered"}), push.Outcome("gone", "Unregistered")),
    (_Resp(404), push.Outcome("gone", "404")),
    (_Resp(400, {"reason": "VapidPkHashMismatch"}), push.Outcome("gone", "VapidPkHashMismatch")),
    (_Resp(403, {"reason": "VapidPkHashMismatch"}), push.Outcome("gone", "VapidPkHashMismatch")),
    (_Resp(429, {"reason": "TooManyRequests"}, {"Retry-After": "120"}),
     push.Outcome("throttled", "TooManyRequests", retry_after_s=120)),
    (_Resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
     push.Outcome("throttled", "429", retry_after_s=60)),
    (_Resp(429, headers={"Retry-After": "0"}), push.Outcome("throttled", "429", retry_after_s=1)),
    (_Resp(500), push.Outcome("retry", "500")),
    (_Resp(503, {"reason": "ServiceUnavailable"}), push.Outcome("retry", "ServiceUnavailable")),
    (_Resp(400, {"reason": "BadJwtToken"}), push.Outcome("failed", "BadJwtToken")),
    (_Resp(403, {"reason": "ExpiredJwtToken"}), push.Outcome("failed", "ExpiredJwtToken")),
    (_Resp(413, {"reason": "PayloadTooLarge"}), push.Outcome("failed", "PayloadTooLarge")),
    (_Resp(400, {"reason": "see https://web.push.apple.com/x"}), push.Outcome("failed", "400")),
    (_Resp(400, ["not", "an", "object"]), push.Outcome("failed", "400")),
    (_Resp(302, headers={"Location": "https://evil.example/"}), push.Outcome("failed", "302")),
], ids=lambda v: getattr(v, "status_code", None) or getattr(v, "kind", ""))
def test_every_answer_has_one_outcome(monkeypatch: pytest.MonkeyPatch, key: str, device: Any,
                                      resp: _Resp, expected: push.Outcome) -> None:
    _answer(monkeypatch, resp)
    _ua, p256dh, auth_b64, _a = device
    assert push.send(ENDPOINT, ORIGIN, p256dh, auth_b64, {}, ttl_s=600) == expected


@pytest.mark.parametrize("error", [
    requests.ConnectionError(f"HTTPSConnectionPool(host='web.push.apple.com'): Max retries for url: {ENDPOINT}"),
    requests.ReadTimeout(f"Read timed out: {ENDPOINT}"),
    requests.exceptions.SSLError(f"certificate verify failed for {ENDPOINT}"),
])
def test_a_transport_error_is_its_class_name_and_never_the_endpoint(
        monkeypatch: pytest.MonkeyPatch, key: str, device: Any, error: Exception) -> None:
    _answer(monkeypatch, error)
    _ua, p256dh, auth_b64, _a = device
    out = push.send(ENDPOINT, ORIGIN, p256dh, auth_b64, {}, ttl_s=600)
    assert out == push.Outcome("retry", type(error).__name__)
    assert "push.apple.com" not in repr(out) and ENDPOINT not in repr(out)


@pytest.mark.parametrize("endpoint", [
    "http://web.push.apple.com/x",
    "https://evil.example/x",
    "https://web.push.apple.com.evil.example/x",
    "https://user:pw@web.push.apple.com/x",
    "https://web.push.apple.com:8443/x",
    "https://WEB.push.apple.com/x",
    "https://web.push.apple.com",
    "https://web.push.apple.com/x y",
    "https://web.push.apple.com/é",
    "https://fcm.googleapis.com/fcm/send/abc",
])
def test_nothing_is_posted_outside_apples_push_service(key: str, device: Any, posted: list[dict[str, Any]],
                                                       endpoint: str) -> None:
    _ua, p256dh, auth_b64, _a = device
    assert push.apple_host(endpoint) is None
    assert push.send(endpoint, ORIGIN, p256dh, auth_b64, {}, ttl_s=600) == push.Outcome("failed", "BadEndpoint")
    assert posted == []


def test_apples_hosts_pass() -> None:
    assert push.apple_host(ENDPOINT) == "web.push.apple.com"
    assert push.apple_host("https://api.sandbox.push.apple.com/abc") == "api.sandbox.push.apple.com"


def test_the_endpoint_pattern_is_the_databases_check() -> None:
    from tests.migration_defs import MIGRATIONS

    sql = (MIGRATIONS / "654_notifications_w4_push_ledger.sql").read_text(encoding="utf-8")
    assert f"check (endpoint ~ '{push.APPLE_ENDPOINT.pattern}')" in sql


def test_the_unpadded_base64url_is_what_the_browser_sends() -> None:
    raw = os.urandom(65)
    assert push.b64url(raw) == base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert push.unb64url(push.b64url(raw)) == raw
    with pytest.raises(ValueError):
        push.unb64url("not base64url!")
