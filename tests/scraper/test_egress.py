"""scraper.egress: the one door to foreign hosts (images, the URL prober, the `web` drain).

No test here touches the network: the resolver and the socket are faked, and the fetch
loop runs against a fake session, so each refusal is shown happening BEFORE a byte leaves.
The deadline's watchdog is shown against a real local socket (loopback allowed for those
tests only), because what it cuts are loops inside http.client and urllib3.
"""

from __future__ import annotations

import gzip
import io
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
import requests
import urllib3

from scraper import egress

PUBLIC_V4 = "93.184.216.34"


class _Resp:
    def __init__(self, status: int = 200, headers: dict[str, str] | None = None,
                 chunks: tuple[bytes, ...] = (b"<html></html>",), on_chunk: Any = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks
        self._on_chunk = on_chunk
        self.read_chunks = 0

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def iter_content(self, chunk_size: int = 1) -> Any:
        for chunk in self._chunks:
            self.read_chunks += 1
            if self._on_chunk is not None:
                self._on_chunk()
            yield chunk


class _Session:
    def __init__(self, *responses: _Resp) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kw: Any) -> _Resp:
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


# --- the address rules -------------------------------------------------------------------------


@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.8.9.10", "::1",                      # loopback
    "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1",  # private
    "169.254.169.254", "169.254.0.1", "fe80::1", "fe80::1%eth0",  # link-local, metadata
    "fc00::1", "fd12:3456::1",                             # IPv6 ULA (Railway's fd12::/16)
    "::ffff:127.0.0.1", "::ffff:8.8.8.8",                  # IPv4-mapped, any
    "2002:7f00:1::",                                       # 6to4 around loopback
    "100.64.0.1", "0.0.0.0", "::", "224.0.0.1", "240.0.0.1", "255.255.255.255",
])
def test_every_refused_address_class_is_refused(address: str) -> None:
    with pytest.raises(egress.EgressRefused):
        egress.check_address(address)


@pytest.mark.parametrize("address", [PUBLIC_V4, "8.8.8.8", "2606:4700:4700::1111"])
def test_a_public_address_passes(address: str) -> None:
    egress.check_address(address)


@pytest.mark.parametrize("url", [
    "ftp://example.cz/x", "file:///etc/passwd", "gopher://example.cz/", "https:///nohost",
    "http://localhost/x", "http://LOCALHOST./x", "https://api.railway.internal/x",
    "https://metadata.google.internal/computeMetadata/v1/", "http://nas.local/",
    "http://127.0.0.1:8080/", "http://[::1]/", "http://[fd12::5]/", "http://169.254.169.254/latest",
    "http://[::ffff:10.0.0.1]/",
])
def test_a_url_is_refused_before_any_lookup(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _never)
    with pytest.raises(egress.EgressRefused):
        egress.check_url(url)


def _never(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("resolved a name that should have been refused first")


# --- the pinned connection ------------------------------------------------------------------------


class _Resolver:
    """getaddrinfo answering from a script, one answer per call (the last repeats)."""

    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, host: str, port: int, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        self.calls.append(host)
        ips = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return [((socket.AF_INET6 if ":" in ip else socket.AF_INET), socket.SOCK_STREAM, 6, "",
                 (ip, port, 0, 0) if ":" in ip else (ip, port)) for ip in ips]


class _FakeSocket:
    connected: list[Any] = []

    def __init__(self, *_a: Any) -> None:
        pass

    def setsockopt(self, *_a: Any) -> None:
        pass

    def settimeout(self, *_a: Any) -> None:
        pass

    def bind(self, *_a: Any) -> None:
        pass

    def connect(self, sockaddr: Any) -> None:
        _FakeSocket.connected.append(sockaddr)
        raise ConnectionRefusedError("test socket: nothing listens")

    def close(self) -> None:
        pass


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_name_resolving_to_a_private_address_is_refused_at_connect(
        scheme: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = _Resolver(["10.0.0.7"])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(socket, "socket", _FakeSocket)
    _FakeSocket.connected = []
    with pytest.raises(egress.EgressRefused):
        egress.safe_session().get(f"{scheme}://rk-novak.example/byt/1", timeout=2)
    assert resolver.calls == ["rk-novak.example"] and _FakeSocket.connected == []


def test_a_railway_private_name_is_refused_even_when_it_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4]))
    with pytest.raises(egress.EgressRefused):
        egress.safe_session().get("http://api.railway.internal/", timeout=2)


def test_one_private_address_among_public_ones_refuses_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4, "fd12::9"]))
    monkeypatch.setattr(socket, "socket", _FakeSocket)
    _FakeSocket.connected = []
    with pytest.raises(egress.EgressRefused):
        egress.safe_session().get("https://mixed.example/", timeout=2)
    assert _FakeSocket.connected == []


def test_dns_rebinding_between_a_check_and_the_connect_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """An earlier lookup said public; the name then answers private. The connection resolves
    for itself and checks what IT got, so the earlier answer vouches for nothing."""
    resolver = _Resolver([PUBLIC_V4], ["127.0.0.1"])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(socket, "socket", _FakeSocket)
    _FakeSocket.connected = []
    egress.resolve_public("rebind.example", 443)  # the "earlier check": public
    with pytest.raises(egress.EgressRefused):
        egress.safe_session().get("https://rebind.example/", timeout=2)
    assert _FakeSocket.connected == []


def test_the_socket_connects_to_exactly_the_address_it_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """One lookup per connection, and the connect goes to that answer: a resolver that would
    say something else on a second asking is never asked again."""
    resolver = _Resolver([PUBLIC_V4], ["127.0.0.1"])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(socket, "socket", _FakeSocket)
    _FakeSocket.connected = []
    with pytest.raises(requests.ConnectionError):
        egress.safe_session().get("http://pinned.example/", timeout=2)
    assert resolver.calls == ["pinned.example"]
    assert _FakeSocket.connected == [(PUBLIC_V4, 80)]


def test_the_session_never_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    session = egress.safe_session({"Accept": "text/html"})
    assert session.trust_env is False
    assert isinstance(session.get_adapter("https://x.example/"), egress._PinnedAdapter)
    assert isinstance(session.get_adapter("http://x.example/"), egress._PinnedAdapter)
    assert "Chrome/" in session.headers["User-Agent"] and session.headers["Accept"] == "text/html"
    assert session.headers["sec-ch-ua-mobile"] == "?0"


# --- redirects ---------------------------------------------------------------------------------


def test_every_redirect_hop_is_checked_before_it_is_requested() -> None:
    session = _Session(_Resp(302, {"Location": "http://10.0.0.1/admin"}))
    with pytest.raises(egress.EgressRefused):
        egress.fetch("https://shortener.example/abc", session=session)
    assert [url for _, url, _ in session.calls] == ["https://shortener.example/abc"]


def test_redirects_are_followed_by_the_egress_never_by_requests() -> None:
    session = _Session(_Resp(301, {"Location": "/byty/15/"}), _Resp(200, {"Content-Type": "text/html"}))
    got = egress.fetch("http://rk-novak.example/byty/15", session=session)
    assert got.status == 200 and got.final_url == "http://rk-novak.example/byty/15/"
    assert got.content_type == "text/html" and got.body == b"<html></html>"
    assert all(kw["allow_redirects"] is False and kw["stream"] is True for _, _, kw in session.calls)


def test_more_than_five_hops_is_refused() -> None:
    loop = [_Resp(302, {"Location": f"/hop{i}"}) for i in range(egress.MAX_REDIRECTS + 1)]
    with pytest.raises(requests.TooManyRedirects):
        egress.fetch("https://loop.example/", session=_Session(*loop))


def test_a_303_turns_the_next_hop_into_a_get_and_a_head_never_follows_when_asked_not_to() -> None:
    session = _Session(_Resp(303, {"Location": "https://b.example/"}), _Resp(200))
    egress.fetch("https://a.example/", method="POST", session=session)
    assert [m for m, _, _ in session.calls] == ["POST", "GET"]
    head = _Session(_Resp(301, {"Location": "https://c.example/"}))
    got = egress.fetch("https://a.example/", method="HEAD", follow_redirects=False, session=head)
    assert (got.status, got.headers["Location"], got.body) == (301, "https://c.example/", b"")


def test_an_error_status_is_returned_never_raised() -> None:
    got = egress.fetch("https://a.example/gone", session=_Session(_Resp(404, chunks=(b"nope",))))
    assert (got.status, got.body) == (404, b"nope")


# --- the body: the decoded cap and the deadline -----------------------------------------------------


def test_a_declared_length_over_the_cap_is_refused_before_a_byte_is_read() -> None:
    resp = _Resp(200, {"Content-Length": "5000"}, chunks=(b"x" * 10,))
    with pytest.raises(egress.ResponseTooLarge):
        egress.fetch("https://a.example/", max_bytes=1000, session=_Session(resp))
    assert resp.read_chunks == 0


def test_a_body_streaming_past_the_cap_is_refused() -> None:
    resp = _Resp(200, chunks=(b"x" * 600, b"x" * 600, b"x" * 600))
    with pytest.raises(egress.ResponseTooLarge):
        egress.fetch("https://a.example/", max_bytes=1000, session=_Session(resp))
    assert resp.read_chunks == 2


class _RawResp:
    """A requests-shaped response over a real urllib3 body, so `read1` decodes for real."""

    def __init__(self, raw: urllib3.HTTPResponse, headers: dict[str, str]) -> None:
        self.raw, self.headers, self.status_code = raw, headers, 200

    def __enter__(self) -> "_RawResp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def test_the_cap_counts_decoded_bytes_so_a_gzip_bomb_stops_at_it() -> None:
    bomb = gzip.compress(b"\0" * 5_000_000)
    assert len(bomb) < 10_000
    raw = urllib3.HTTPResponse(body=io.BytesIO(bomb), headers={"Content-Encoding": "gzip"},
                               preload_content=False, decode_content=True)
    resp = _RawResp(raw, {"Content-Encoding": "gzip", "Content-Length": str(len(bomb))})
    with pytest.raises(egress.ResponseTooLarge):
        egress.fetch("https://a.example/", max_bytes=100_000, session=_Session(resp))  # type: ignore[arg-type]


def test_a_gzip_page_under_the_cap_is_read_decoded() -> None:
    page = b"<html>" + b"byt " * 1000 + b"</html>"
    raw = urllib3.HTTPResponse(body=io.BytesIO(gzip.compress(page)),
                               headers={"Content-Encoding": "gzip"},
                               preload_content=False, decode_content=True)
    got = egress.fetch("https://a.example/", session=_Session(_RawResp(raw, {})))  # type: ignore[arg-type]
    assert got.body == page


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_deadline_binds_across_the_streamed_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server dripping a byte every few seconds never trips a per-read timeout; the
    wall-clock deadline across the whole read does."""
    clock = _Clock()
    monkeypatch.setattr(egress.time, "monotonic", clock)

    def drip() -> None:
        clock.now += 4.0

    resp = _Resp(200, chunks=tuple(b"x" for _ in range(100)), on_chunk=drip)
    with pytest.raises(egress.DeadlineExceeded):
        egress.fetch("https://slow.example/", deadline_s=20.0, session=_Session(resp))
    assert resp.read_chunks == 5


def test_the_deadline_spans_the_redirect_hops(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(egress.time, "monotonic", clock)

    class _SlowSession(_Session):
        def request(self, method: str, url: str, **kw: Any) -> _Resp:
            clock.now += 8.0
            return super().request(method, url, **kw)

    hops = [_Resp(302, {"Location": f"/h{i}"}) for i in range(4)]
    session = _SlowSession(*hops)
    with pytest.raises(egress.DeadlineExceeded):
        egress.fetch("https://a.example/", deadline_s=20.0, session=session)
    assert len(session.calls) == 3
    assert all(kw["timeout"][1] <= 20.0 for _, _, kw in session.calls)


def test_a_hop_the_caller_stops_before_is_never_requested() -> None:
    """The `web` drain stops before a portal's ad: the redirect itself is the answer."""
    target = "https://www.sreality.cz/detail/prodej/byt/2+kk/praha/2557083724"
    session = _Session(_Resp(301, {"Location": target}))
    got = egress.fetch("https://bit.example/abc", session=session,
                       stop_before=lambda url: "sreality.cz" in url)
    assert (got.status, got.final_url, got.body) == (301, target, b"")
    assert [url for _, url, _ in session.calls] == ["https://bit.example/abc"]


def test_the_headers_are_read_case_blind() -> None:
    got = egress.fetch("https://a.example/", method="HEAD", follow_redirects=False,
                       session=_Session(_Resp(302, {"location": "https://b.example/"})))
    assert got.headers["Location"] == "https://b.example/"


@pytest.mark.parametrize("status", [400, 404, 410, 503])
def test_raise_for_status_carries_the_status_like_requests_does(status: int) -> None:
    egress.raise_for_status(egress.FetchResult(200, "https://a.example/", b"", None))
    with pytest.raises(requests.HTTPError) as exc:
        egress.raise_for_status(egress.FetchResult(status, "https://a.example/x", b"", None))
    assert exc.value.response is not None and exc.value.response.status_code == status


def test_the_installed_urllib3_reads_one_socket_read_at_a_time() -> None:
    """`_chunks` and the decoded cap rely on `read1` (urllib3 2.3+; pyproject pins 2.6+)."""
    assert hasattr(urllib3.HTTPResponse, "read1")


# --- the watchdog, against a real socket ------------------------------------------------------------


class _Tarpit:
    """A local server answering ONE request with `script(conn, stop)`; it gives up after
    ten seconds, so a watchdog that failed shows as a slow test, never a hung one."""

    def __init__(self, script: Callable[[socket.socket, threading.Event], None]) -> None:
        self.server = socket.create_server(("127.0.0.1", 0))
        self.server.settimeout(10)
        self.url = f"http://127.0.0.1:{self.server.getsockname()[1]}/byt/1"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, args=(script,), daemon=True)
        self.thread.start()

    def _serve(self, script: Callable[[socket.socket, threading.Event], None]) -> None:
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            request = b""
            try:
                while b"\r\n\r\n" not in request:
                    data = conn.recv(4096)
                    if not data:
                        return
                    request += data
                script(conn, self.stop)
            except OSError:
                return

    def close(self) -> None:
        self.stop.set()
        self.server.close()
        self.thread.join(timeout=5)


def _drip(conn: socket.socket, stop: threading.Event, head: bytes, unit: bytes) -> None:
    conn.sendall(head)
    for _ in range(100):
        if stop.wait(0.1):
            return
        conn.sendall(unit)


def _header_drip(conn: socket.socket, stop: threading.Event) -> None:
    _drip(conn, stop, b"HTTP/1.1 200 OK\r\nX-Drip: ", b"a")


def _endless_trailer(conn: socket.socket, stop: threading.Event) -> None:
    _drip(conn, stop, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n",
          b"X-Trailer: 1\r\n")


def _empty_deflate_blocks(conn: socket.socket, stop: threading.Event) -> None:
    # A zlib header, then stored blocks of length 0 (BFINAL=0): bytes on the wire, nothing
    # decoded, so urllib3's read1 keeps reading inside one call.
    _drip(conn, stop, b"HTTP/1.1 200 OK\r\nContent-Encoding: deflate\r\nConnection: close\r\n\r\n\x78\x9c",
          b"\x00\x00\x00\xff\xff")


@pytest.fixture
def loopback_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(egress, "check_address", lambda address: None)


@pytest.mark.parametrize("script", [_header_drip, _endless_trailer, _empty_deflate_blocks],
                         ids=["header-drip", "endless-trailer", "empty-deflate-blocks"])
def test_the_watchdog_ends_a_tarpit_at_the_deadline(loopback_allowed: None, script: Any) -> None:
    """Each tarpit sends a byte every 0.1 s, so no per-read timeout ever fires, and each
    loops inside http.client / urllib3 where the between-reads check never runs. The
    watchdog shuts the socket at the deadline."""
    pit = _Tarpit(script)
    try:
        started = time.monotonic()
        with pytest.raises(egress.DeadlineExceeded):
            egress.fetch(pit.url, deadline_s=1.0)
        assert time.monotonic() - started <= 2.0
    finally:
        pit.close()


def test_a_page_inside_its_deadline_is_read_whole_with_the_watchdog_armed(loopback_allowed: None) -> None:
    def page(conn: socket.socket, stop: threading.Event) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 13\r\n\r\n<html></html>")

    pit = _Tarpit(page)
    try:
        got = egress.fetch(pit.url, deadline_s=5.0)
        assert (got.status, got.body, got.content_type) == (200, b"<html></html>", "text/html")
    finally:
        pit.close()
