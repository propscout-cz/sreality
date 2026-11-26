"""The ČÚZK client's HTTPS transport, run for real against a TLS server on 127.0.0.1 (a self-signed certificate
trusted through `tls_context`): what is sent, that nothing is sent once the caller has given up, that a redirect
is returned and never followed, that a slow server times out, and that giving up on a request in flight never
puts plaintext on the wire. The tests elsewhere replace `_transport`; these are the only ones that run it."""

from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Any

import pytest

x509 = pytest.importorskip("cryptography.x509")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from api import wsdp_client as w  # noqa: E402

SECRET = b"TAJNE-HESLO-V-OBALCE"


@pytest.fixture(scope="module")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A self-signed certificate for 127.0.0.1: (cert.pem, key.pem)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    folder = tmp_path_factory.mktemp("tls")
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                       serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    return folder / "cert.pem", folder / "key.pem"


class _Server:
    """One TLS server on a loopback port, behind a plain TCP relay that records every RAW byte the client sent.
    `handle(tls_conn)` decides what the server does with a connection."""

    def __init__(self, certificate: tuple[Path, Path], handle: Any) -> None:
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(*map(str, certificate))
        self._handle, self.requests, self.raw = handle, [], bytearray()
        self._tls = socket.create_server(("127.0.0.1", 0))
        self._relay = socket.create_server(("127.0.0.1", 0))
        self.port = self._relay.getsockname()[1]
        self._threads = [threading.Thread(target=self._serve, daemon=True),
                         threading.Thread(target=self._forward, daemon=True)]
        for thread in self._threads:
            thread.start()

    def _serve(self) -> None:
        while True:
            try:
                plain, _ = self._tls.accept()
            except OSError:
                return
            try:
                with self._context.wrap_socket(plain, server_side=True) as conn:
                    self._handle(self, conn)
            except (OSError, ssl.SSLError):
                pass

    def _forward(self) -> None:
        while True:
            try:
                client, _ = self._relay.accept()
            except OSError:
                return
            upstream = socket.create_connection(self._tls.getsockname())
            threading.Thread(target=self._pump, args=(client, upstream, True), daemon=True).start()
            threading.Thread(target=self._pump, args=(upstream, client, False), daemon=True).start()

    def _pump(self, source: socket.socket, sink: socket.socket, record: bool) -> None:
        try:
            while chunk := source.recv(65536):
                if record:
                    self.raw += chunk
                sink.sendall(chunk)
        except OSError:
            pass
        finally:
            for sock in (source, sink):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def url(self, path: str = "/ws/wsdp/3.1/vyhledat") -> str:
        return f"https://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self._tls.close()
        self._relay.close()


def _read_request(conn: ssl.SSLSocket) -> bytes:
    """One HTTP request with a Content-Length body, as bytes; b'' when the client sent nothing."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return data
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = next((int(line.split(b":")[1]) for line in head.split(b"\r\n")
                   if line.lower().startswith(b"content-length:")), 0)
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            break
        body += chunk
    return head + b"\r\n\r\n" + body


def _answer(status: str, body: bytes, extra: str = "") -> Any:
    def handle(server: _Server, conn: ssl.SSLSocket) -> None:
        server.requests.append(_read_request(conn))
        if server.requests[-1]:
            conn.sendall(f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n{extra}Connection: close\r\n\r\n"
                         .encode() + body)
    return handle


@pytest.fixture()
def trusting(monkeypatch: pytest.MonkeyPatch, certificate: tuple[Path, Path]) -> None:
    """The client trusts the test server's certificate, through the same context it verifies ČÚZK with."""
    def context() -> ssl.SSLContext:
        made = ssl.create_default_context(cafile=str(certificate[0]))
        made.minimum_version = ssl.TLSVersion.TLSv1_2
        return made

    monkeypatch.setattr(w, "tls_context", context)


@pytest.fixture()
def serve(certificate: tuple[Path, Path]) -> Any:
    made: list[_Server] = []

    def start(handle: Any) -> _Server:
        made.append(_Server(certificate, handle))
        return made[-1]

    yield start
    for server in made:
        server.close()


HEADERS = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"urn:test:Op"', "User-Agent": "propscout-test"}


def test_the_context_verifies_the_certificate_and_the_hostname_against_certifi() -> None:
    import certifi

    context = w.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert len(context.get_ca_certs()) > 50 and Path(certifi.where()).is_file()


def test_a_server_whose_certificate_is_not_trusted_is_never_sent_the_request(serve: Any) -> None:
    """No `trusting` fixture: the real context does not know the test certificate."""
    server = serve(_answer("200 OK", b"<ok/>"))
    with pytest.raises(w.WsdpError) as untrusted:
        w._transport(server.url(), b"<body>" + SECRET + b"</body>", HEADERS, w._Given())
    assert untrusted.value.reason == "unreachable"
    assert server.requests in ([], [b""]) and SECRET not in server.raw


def test_the_request_is_one_post_with_its_headers_and_body(serve: Any, trusting: None) -> None:
    server = serve(_answer("200 OK", b"<answer/>"))
    body = b"<envelope>" + SECRET + b"</envelope>"
    assert w._transport(server.url("/ws/wsdp/3.1/sestavy"), body, HEADERS, w._Given()) == (200, b"<answer/>")
    (request,) = server.requests
    head, _, sent = request.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    assert lines[0] == b"POST /ws/wsdp/3.1/sestavy HTTP/1.1" and sent == body
    headers = {k.strip().lower(): v.strip() for k, v in (line.split(b":", 1) for line in lines[1:])}
    assert headers[b"host"] == f"127.0.0.1:{server.port}".encode()
    assert headers[b"content-length"] == str(len(body)).encode()
    assert headers[b"content-type"] == b"text/xml; charset=utf-8" and headers[b"soapaction"] == b'"urn:test:Op"'
    assert headers[b"user-agent"] == b"propscout-test"
    assert SECRET not in server.raw, "what crossed the wire was TLS, not the envelope"


def test_nothing_is_sent_once_the_caller_has_given_up(serve: Any, trusting: None) -> None:
    """The order that matters: connect, THEN ask whether the caller still waits, THEN send. A request stuck before
    its send must not leave after the caller stamped it as unanswered."""
    server = serve(_answer("200 OK", b"<answer/>"))
    given = w._Given()
    given.give_up()
    with pytest.raises(w.WsdpError) as gone:
        w._transport(server.url(), b"<envelope>" + SECRET + b"</envelope>", HEADERS, given)
    assert gone.value.reason == "timeout"
    time.sleep(0.2)
    assert server.requests in ([], [b""]), "the server saw a handshake at most, never a byte of the request"


def test_a_redirect_is_returned_and_never_followed(serve: Any, trusting: None) -> None:
    elsewhere = serve(_answer("200 OK", b"<stolen/>"))
    server = serve(_answer("307 Temporary Redirect", b"", f"Location: {elsewhere.url()}\r\n"))
    assert w._transport(server.url(), b"<envelope>" + SECRET + b"</envelope>", HEADERS, w._Given()) == (307, b"")
    time.sleep(0.2)
    assert len(server.requests) == 1 and elsewhere.requests == [], "the password went to one host, once"


def test_a_server_that_does_not_answer_is_a_timeout_at_the_read_bound(serve: Any, trusting: None,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    def silent(server: _Server, conn: ssl.SSLSocket) -> None:
        server.requests.append(_read_request(conn))
        time.sleep(3)

    server = serve(silent)
    monkeypatch.setattr(w, "TIMEOUT", (2, 0.4))
    began = time.monotonic()
    with pytest.raises(w.WsdpError) as slow:
        w._transport(server.url(), b"<envelope/>", HEADERS, w._Given())
    assert slow.value.reason == "timeout" and time.monotonic() - began < 2.5


def _tls_client(server: _Server, certificate: tuple[Path, Path]) -> ssl.SSLSocket:
    context = ssl.create_default_context(cafile=str(certificate[0]))
    return context.wrap_socket(socket.create_connection(("127.0.0.1", server.port)), server_hostname="127.0.0.1")


def _hold_open(server: _Server, conn: ssl.SSLSocket) -> None:
    conn.recv(1024)
    time.sleep(2)


def test_our_shutdown_leaves_the_tls_socket_its_ssl_object_so_a_late_write_cannot_go_out_raw(
        serve: Any, certificate: tuple[Path, Path]) -> None:
    """The fifth pass's blocker, at its mechanism. `SSLSocket.send` writes RAW once the socket's SSL object is gone,
    and `SSLSocket.shutdown` drops that object before the socket is actually shut: a send racing it could put the
    envelope on the wire unencrypted. The base class's shutdown leaves the object, so a late send fails in TLS."""
    server = serve(_hold_open)
    sock = _tls_client(server, certificate)
    sock.sendall(b"hello")
    w._shutdown(sock)
    assert sock._sslobj is not None, "still a TLS socket: there is no raw path to fall back to"
    with pytest.raises(OSError):
        for _ in range(50):  # a write after the shutdown fails, in the TLS layer or at the closed socket
            sock.sendall(SECRET * 1000)
    time.sleep(0.2)
    assert SECRET not in server.raw
    sock.close()


def test_the_hazard_is_real_in_this_python(serve: Any, certificate: tuple[Path, Path]) -> None:
    """Why the helper exists: the TLS socket's OWN shutdown drops the SSL object. If a future Python stops doing
    so this fails, and the helper's comment can be revisited; until then it must stay the base-class call."""
    server = serve(_hold_open)
    sock = _tls_client(server, certificate)
    sock.sendall(b"hello")
    sock.shutdown(socket.SHUT_RDWR)
    assert sock._sslobj is None
    sock.close()


def test_give_up_uses_that_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    helper, caller = inspect.getsource(w._shutdown), inspect.getsource(w._Given)
    shut: list[Any] = []
    monkeypatch.setattr(w, "_shutdown", shut.append)

    class _Conn:
        sock = object()

    given = w._Given()
    assert given.may_send(_Conn())  # type: ignore[arg-type]
    given.give_up()
    assert shut == [_Conn.sock] and given.up
    assert "socket.socket.shutdown(sock, socket.SHUT_RDWR)" in helper and ".sock.shutdown(" not in caller


def test_giving_up_on_a_request_in_flight_ends_it_and_leaves_no_plaintext(serve: Any, trusting: None) -> None:
    """End to end: the server stops reading, the client blocks inside its body write, the caller gives up from
    another thread; the request ends and every byte the server's port received is TLS. (This cannot force the
    microsecond race itself; the two tests above pin its mechanism.)"""
    stalled = threading.Event()

    def stall(server: _Server, conn: ssl.SSLSocket) -> None:
        conn.recv(1024)  # the start of the request, then nothing more is read
        stalled.set()
        time.sleep(4)

    server = serve(stall)
    given = w._Given()
    body = (b"<envelope>" + SECRET + b"</envelope>") * 400_000  # ~12 MB: far more than the socket buffers hold
    outcome: list[BaseException | tuple] = []

    def send() -> None:
        try:
            outcome.append(w._transport(server.url(), body, HEADERS, given))
        except BaseException as exc:  # noqa: BLE001 - the outcome is the assertion
            outcome.append(exc)

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    assert stalled.wait(10)
    time.sleep(0.3)  # the client is now blocked in its body write
    given.give_up()
    thread.join(10)
    assert not thread.is_alive(), "the request ended when the caller gave up"
    assert isinstance(outcome[0], w.WsdpError) and outcome[0].reason == "timeout"
    time.sleep(0.3)
    assert len(server.raw) > 1000 and SECRET not in server.raw, "nothing of the envelope crossed the wire unencrypted"


def test_give_up_survives_a_request_that_closed_its_own_socket() -> None:
    class _Closed:
        sock = None

    given = w._Given()
    assert given.may_send(_Closed())  # type: ignore[arg-type]
    given.give_up()  # the request's own close() set `sock` to None: nothing to shut, and nothing raised
    assert given.up
