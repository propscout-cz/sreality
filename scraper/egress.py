"""ONE safe outbound HTTP session for every fetch of a host a page or a paste named.

Three fetchers used to reach foreign hosts on plain `requests` sessions: the image
downloader (page-supplied URLs, whose bytes the open `/images/{key}` proxy serves), the
weekly URL prober and, since the tenth portal, the `web` drain (any site an operator
pastes). Each could be aimed at the worker's own network — Railway's private
`*.railway.internal` (IPv6 `fd12::/16`), the cloud metadata address, localhost — by one
URL or one redirect. This module is the one door (docs/design/new-property-from-link.md
§3.4):

  * http(s) only, and a host that is an address literal or a local-network name is
    checked before any request;
  * **the address is pinned by a resolver hook**: the session's connection class resolves
    the name itself, refuses the connection if ANY address is loopback, private (10/8,
    172.16/12, 192.168/16), link-local (169.254/16 — the metadata address — and fe80::/10),
    unique-local IPv6 (fc00::/7), IPv4-mapped / 6to4 / Teredo-embedded, multicast, reserved
    or otherwise not global, and then connects to exactly the address it validated. The
    check and the connect are one step, so no second lookup can be answered differently
    (DNS rebinding); TLS SNI and certificate verification stay on the host NAME;
  * redirects are never followed by `requests`: `fetch` follows at most MAX_REDIRECTS
    hops itself, and every hop is checked and connected the same way;
  * one wall-clock deadline across the whole fetch, enforced by a WATCHDOG: a timer shuts
    down every socket the fetch is using when the deadline passes, so no read can outlive it
    — not a header dripped a byte at a time, not an endless chunked trailer, not a run of
    empty deflate blocks (each loops inside http.client / urllib3, where no check of ours
    runs, and a per-read socket timeout is reset by every byte). The streamed read also
    checks the clock between reads;
  * a cap on DECODED bytes (a gzip bomb stops at the cap, not at its compressed size);
  * a desktop Chrome identity (portal_base's UA + client hints); `trust_env` off, so no
    environment proxy or netrc ever applies;
  * `stop_before`: a caller may refuse to request a hop at all (the `web` drain stops before
    a redirect into one of the nine portals' ads — that portal's own client fetches it).

Name resolution (`getaddrinfo`) is the one step the watchdog cannot cut: the system
resolver's own timeouts bound it.

A request a caller sends through an explicit proxy (the image downloader's one hop through a
portal's residential proxy) is connected by the proxy, not pinned here: the proxy resolves
the name on its own network, where our private names mean nothing.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from scraper.portal import deadline_reached
from scraper.portal_base import _BASE_HEADERS, client_hints

MAX_BYTES = 2_000_000
DEADLINE_S = 20.0
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 10.0
_CHUNK = 16384
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_LOCAL_SUFFIXES = (".localhost", ".internal", ".local", ".home.arpa")


class EgressRefused(ValueError):
    """A URL or an address the egress will not reach. A ValueError, not an OSError, so it
    leaves urllib3's connect path unwrapped and the caller sees why."""


class ResponseTooLarge(ValueError):
    """The decoded body passed the caller's cap."""


class DeadlineExceeded(requests.Timeout):
    """The fetch's wall-clock deadline passed (redirect hops and the streamed read included)."""


@dataclass(frozen=True)
class FetchResult:
    status: int
    final_url: str
    body: bytes
    content_type: str | None
    headers: Mapping[str, str] = field(default_factory=CaseInsensitiveDict)


def raise_for_status(result: FetchResult) -> None:
    """`requests.Response.raise_for_status` for a FetchResult: the HTTPError carries a
    response with the status, which the image phase's gone / dead-URL classifiers read."""
    if result.status < 400:
        return
    response = requests.Response()
    response.status_code, response.url = result.status, result.final_url
    raise requests.HTTPError(f"{result.status} for url: {result.final_url}", response=response)


class _Watchdog:
    """One fetch's sockets, shut down together when its deadline passes. A shut-down socket
    wakes a read blocked on it with end-of-file, wherever in http.client or urllib3 the read
    loops; `fetch` then raises DeadlineExceeded whatever the interrupted read made of it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: list[HTTPConnection] = []
        self._socks: list[socket.socket] = []
        self.fired = False

    def watch(self, *, conn: HTTPConnection | None = None, sock: socket.socket | None = None) -> None:
        with self._lock:
            if conn is not None:
                self._conns.append(conn)
            if sock is not None:
                self._socks.append(sock)
            fired = self.fired
        if fired:
            _shutdown(sock if sock is not None else getattr(conn, "sock", None))

    def fire(self) -> None:
        with self._lock:
            self.fired = True
            socks = [*self._socks, *(getattr(c, "sock", None) for c in self._conns)]
        for sock in socks:
            _shutdown(sock)


def _shutdown(sock: Any) -> None:
    # The base class's shutdown, so a TLS socket's SSL object is left to its reading thread.
    if sock is None:
        return
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except (OSError, TypeError, ValueError):
        pass


_WATCHDOG: ContextVar[_Watchdog | None] = ContextVar("egress_watchdog", default=None)


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return True
        embedded = ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and _blocked(embedded):
            return True
        if ip.is_site_local:
            return True
    return (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def check_address(address: str) -> None:
    """Refuse an address the egress must never connect to."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        raise EgressRefused(f"not an IP address: {address!r}") from None
    if _blocked(ip):
        raise EgressRefused(f"refused address {ip} (not a public internet address)")


def check_url(url: str) -> None:
    """The checks a URL can fail before any lookup: scheme, a host at all, a local name or a
    refused address literal. The resolved addresses are checked at connect time."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        raise EgressRefused(f"unparsable URL: {url!r}") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise EgressRefused(f"refused scheme {parts.scheme!r}: http(s) only")
    if not host or "\\" in parts.netloc:
        raise EgressRefused(f"refused host in {url!r}")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise EgressRefused(f"refused local-network name {host!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    check_address(host)


def resolve_public(host: str, port: int) -> list[tuple[Any, ...]]:
    """`getaddrinfo` for one connection, refused unless EVERY address is public."""
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise requests.ConnectionError(f"cannot resolve {host!r}: {exc}") from exc
    if not infos:
        raise requests.ConnectionError(f"{host!r} resolved to nothing")
    for *_, sockaddr in infos:
        check_address(str(sockaddr[0]))
    return infos


def _connect_pinned(conn: HTTPConnection) -> socket.socket:
    """Resolve, validate and connect in ONE step to the address that was validated."""
    host = getattr(conn, "_dns_host", conn.host)
    if host == "localhost" or host.rstrip(".").lower().endswith(_LOCAL_SUFFIXES):
        raise EgressRefused(f"refused local-network name {host!r}")
    error: OSError | None = None
    watchdog = _WATCHDOG.get()
    for family, socktype, proto, _canon, sockaddr in resolve_public(host, conn.port):
        sock = socket.socket(family, socktype, proto)
        if watchdog is not None:
            watchdog.watch(sock=sock)
        try:
            for option in conn.socket_options or ():
                sock.setsockopt(*option)
            if isinstance(conn.timeout, (int, float)):
                sock.settimeout(conn.timeout)
            if conn.source_address:
                sock.bind(conn.source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error or OSError(f"could not connect to {host!r}")


def _watch_request(conn: HTTPConnection) -> None:
    # A pooled connection is reused across fetches, so it joins the CURRENT fetch's watchdog
    # each time it sends (its socket is read at fire time: the TLS one once wrapped).
    watchdog = _WATCHDOG.get()
    if watchdog is not None:
        watchdog.watch(conn=conn)


class _PinnedHTTPConnection(HTTPConnection):
    def _new_conn(self) -> socket.socket:
        return _connect_pinned(self)

    def request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        _watch_request(self)
        super().request(*args, **kwargs)


class _PinnedHTTPSConnection(HTTPSConnection):
    def _new_conn(self) -> socket.socket:
        return _connect_pinned(self)

    def request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        _watch_request(self)
        super().request(*args, **kwargs)


class _PinnedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class _PinnedAdapter(HTTPAdapter):
    """requests' adapter with the pinned connection classes in its pool manager (a proxy
    manager, used only when a caller passes `proxies`, keeps urllib3's own)."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _PinnedHTTPPool, "https": _PinnedHTTPSPool}


def safe_session(headers: Mapping[str, str] | None = None) -> requests.Session:
    """A session whose every direct connection is resolved, validated and pinned."""
    session = requests.Session()
    session.trust_env = False
    adapter = _PinnedAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({**_BASE_HEADERS, **client_hints(_BASE_HEADERS["User-Agent"]),
                            **(headers or {})})
    return session


def _timeout(deadline: float, read_timeout_s: float) -> tuple[float, float]:
    left = deadline - time.monotonic()
    if left <= 0:
        raise DeadlineExceeded("the fetch deadline passed")
    return (min(CONNECT_TIMEOUT_S, left), min(read_timeout_s, left))


def _chunks(response: Any) -> Iterator[bytes]:
    """Decoded chunks, each from at most one socket read when urllib3 offers `read1`."""
    read1 = getattr(getattr(response, "raw", None), "read1", None)
    if read1 is None:
        yield from response.iter_content(chunk_size=_CHUNK)
        return
    while True:
        data = read1(_CHUNK, decode_content=True)
        if not data:
            return
        yield data


def _read_body(response: Any, max_bytes: int, deadline: float) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared and str(declared).isdigit() and int(declared) > max_bytes:
        raise ResponseTooLarge(f"declared size {declared} exceeds {max_bytes} bytes")
    buf = bytearray()
    for chunk in _chunks(response):
        buf += chunk
        if len(buf) > max_bytes:
            raise ResponseTooLarge(f"body exceeds {max_bytes} bytes")
        if deadline_reached(deadline):
            raise DeadlineExceeded(f"the fetch deadline passed after {len(buf)} bytes")
    return bytes(buf)


def fetch(
    url: str,
    *,
    max_bytes: int = MAX_BYTES,
    deadline_s: float = DEADLINE_S,
    method: str = "GET",
    follow_redirects: bool = True,
    headers: Mapping[str, str] | None = None,
    session: requests.Session | None = None,
    read_timeout_s: float = READ_TIMEOUT_S,
    stop_before: Callable[[str], bool] | None = None,
) -> FetchResult:
    """One page (or HEAD) through the safe session: every hop checked, one deadline, the
    decoded body capped. Any status is returned, never raised; the caller judges it.
    `read_timeout_s` bounds one socket read; the watchdog bounds them all by the deadline.
    `stop_before(url)` true for a redirect's target: that hop is never requested, and the
    redirect itself is returned (its status, `final_url` = the target, an empty body)."""
    session = session or safe_session()
    deadline = time.monotonic() + deadline_s
    watchdog = _Watchdog()
    token = _WATCHDOG.set(watchdog)
    timer = threading.Timer(max(0.0, deadline - time.monotonic()), watchdog.fire)
    timer.daemon = True
    timer.start()
    try:
        result = _fetch_hops(url, session, deadline, method=method, max_bytes=max_bytes,
                             follow_redirects=follow_redirects, headers=headers,
                             read_timeout_s=read_timeout_s, stop_before=stop_before)
    except DeadlineExceeded:
        raise
    except Exception as exc:
        if watchdog.fired:
            raise DeadlineExceeded(f"the fetch deadline passed: {exc}") from exc
        raise
    finally:
        timer.cancel()
        _WATCHDOG.reset(token)
    if watchdog.fired:
        # The read ended because its sockets were shut: what it holds may be cut short.
        raise DeadlineExceeded("the fetch deadline passed")
    return result


def _fetch_hops(
    url: str, session: requests.Session, deadline: float, *, method: str, max_bytes: int,
    follow_redirects: bool, headers: Mapping[str, str] | None, read_timeout_s: float,
    stop_before: Callable[[str], bool] | None,
) -> FetchResult:
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        check_url(current)
        response = session.request(
            method, current, headers=dict(headers or {}), timeout=_timeout(deadline, read_timeout_s),
            stream=True, allow_redirects=False)
        with response:
            location = response.headers.get("Location")
            if follow_redirects and response.status_code in _REDIRECT_STATUSES and location:
                target = urljoin(current, location)
                if stop_before is not None and stop_before(target):
                    return FetchResult(
                        status=int(response.status_code), final_url=target, body=b"",
                        content_type=None, headers=CaseInsensitiveDict(response.headers))
                current = target
                if response.status_code == 303 and method != "HEAD":
                    method = "GET"
                continue
            body = b"" if method == "HEAD" else _read_body(response, max_bytes, deadline)
            return FetchResult(
                status=int(response.status_code), final_url=current, body=body,
                content_type=response.headers.get("Content-Type"),
                headers=CaseInsensitiveDict(response.headers))
    raise requests.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects from {url}")
