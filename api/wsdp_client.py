"""ČÚZK WSDP (the cadastre's SOAP web service): HTTP only. The free status call that proves a login, the free
search of a building's units, and a report's three steps (generate, poll, fetch). Nothing here is retried: three
refused logins block the account for 15 minutes, and a repeated generate is a second purchase. An error names its
kind only, so neither the envelope nor the password can reach a log."""

from __future__ import annotations

import base64
import http.client
import os
import queue
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import certifi

from scraper import contact


PRODUCTION_BASE = "https://katastr.cuzk.gov.cz/ws/wsdp/3.1/"
# The override exists for ČÚZK's free test service. A login is only ever sent to one of ČÚZK's own two hosts, over https.
BASE_ENV = "CUZK_WSDP_BASE"
HOSTS = frozenset({"katastr.cuzk.gov.cz", "wsdptrial.cuzk.gov.cz"})
TIMEOUT = (5, 20)  # connect; one socket read. Each step is bounded, never their sum, nor the DNS lookup before them
# The longest one call can last, by the wall clock. The request runs on its own thread; at this bound the caller
# stops waiting AND ends the request: nothing is sent if it had not been yet, and its socket is shut if it had.
# Bytes the kernel had already queued may still be delivered, and what the service does with a request it received
# is its own: after the bound a request must be treated as possibly received, which is how the login ledger and
# the fetch cool-down treat it. The login lease (api/cuzk_login.py) relies on the bound itself being real.
CALL_MAX_S = 40

_ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:typ="urn:cz:gov:cuzk:iskn:types:wsdp:{service}:3.1"><soapenv:Header>'
    '<wsse:Security soapenv:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/'
    'oasis-200401-wss-wssecurity-secext-1.0.xsd"><wsse:UsernameToken><wsse:Username>{username}</wsse:Username>'
    '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0'
    '#PasswordText">{password}</wsse:Password></wsse:UsernameToken></wsse:Security></soapenv:Header>'
    "<soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>"
)


class WsdpError(Exception):
    """`reason` is refused | timeout | unreachable | status | unreadable | misconfigured | service.

    `authenticated`: the service accepted the login and then answered with an error of its own (`service`: a
    `zprava` at level CHYBA) or with something we cannot read. Not a refused login, whatever else it is."""

    def __init__(self, reason: str, status: int | None = None, *, authenticated: bool = False) -> None:
        super().__init__(f"wsdp {reason} status={status}")
        self.reason, self.status, self.authenticated = reason, status, authenticated


def base_url() -> str:
    base = os.environ.get(BASE_ENV) or PRODUCTION_BASE
    parts = urlsplit(base)
    if parts.scheme != "https" or parts.hostname not in HOSTS or not base.endswith("/"):
        raise WsdpError("misconfigured")
    return base


class _Given:
    """One request's link to the caller waiting for it: the caller gives up through it, once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._up = False
        self._conn: http.client.HTTPSConnection | None = None

    def may_send(self, conn: http.client.HTTPSConnection) -> bool:
        """Called once connected, before anything is sent. False: the caller has given up, send nothing."""
        with self._lock:
            if self._up:
                return False
            self._conn = conn
            return True

    @property
    def up(self) -> bool:
        return self._up

    def give_up(self) -> None:
        """The caller stops waiting: a request not yet sent never will be; one in flight loses its socket now."""
        with self._lock:
            self._up, conn = True, self._conn
        if conn is not None:
            _shutdown(conn.sock)  # read once: the request's own close() can set it to None at any moment


def _shutdown(sock: Any) -> None:
    """End a socket another thread is using. The BASE class's shutdown, never the TLS socket's own: SSLSocket.shutdown
    drops its SSL object first, and SSLSocket.send falls back to a RAW write once that object is gone, so a send
    racing this call could put the request, password included, on the wire unencrypted. Left with its SSL object,
    a late send fails inside the TLS layer instead. (As scraper/egress.py `_shutdown`.)"""
    if sock is None:
        return
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except (OSError, TypeError, ValueError):
        pass


def tls_context() -> ssl.SSLContext:
    """Certificate and hostname verified against certifi's bundle: the same authorities `requests` trusted for
    this call before it moved to http.client, so which servers are believed does not depend on what CA store an
    image happens to carry. CI runs this inside the built API image (runner_image.yml)."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _transport(url: str, data: bytes, headers: dict[str, str], given: _Given) -> tuple[int, bytes]:
    """One HTTPS POST (certificate verified, no redirect followed: a redirect would re-send the password to
    whatever host the answer names, and http.client follows none)."""
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(parts.hostname or "", parts.port or 443, timeout=TIMEOUT[0],
                                       context=tls_context())
    try:
        conn.connect()
        if not given.may_send(conn):
            raise WsdpError("timeout")
        conn.sock.settimeout(TIMEOUT[1])
        conn.request("POST", parts.path, body=data, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read()
    except TimeoutError:
        raise WsdpError("timeout") from None
    except (OSError, http.client.HTTPException):
        raise WsdpError("timeout" if given.up else "unreachable") from None
    finally:
        conn.close()


def _send(url: str, data: bytes, headers: dict[str, str], given: _Given, out: queue.SimpleQueue) -> None:
    try:
        out.put(_transport(url, data, headers, given))
    except BaseException as exc:  # noqa: BLE001 - the waiting caller must be told, whatever it was
        out.put(exc)


def _post(url: str, data: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    """One POST, ended at CALL_MAX_S by the wall clock: the caller stops waiting and the request is ended with it
    (`_Given.give_up`). Whether the service had received it by then is unknown to us, which is exactly what
    `timeout` means to the caller."""
    out: queue.SimpleQueue = queue.SimpleQueue()
    given = _Given()
    threading.Thread(target=_send, args=(url, data, headers, given, out), daemon=True, name="wsdp-call").start()
    try:
        answer = out.get(timeout=CALL_MAX_S)
    except queue.Empty:
        given.give_up()
        raise WsdpError("timeout") from None
    if isinstance(answer, BaseException):
        raise answer
    return answer


def _call(service: str, operation: str, body: str, username: str, password: str) -> ET.Element:
    envelope = _ENVELOPE.format(service=service, username=escape(username), password=escape(password), body=body)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"urn:cz:gov:cuzk:iskn:services:wsdp:{service}:{operation}"',
        "User-Agent": contact.USER_AGENT,
    }
    status, content = _post(base_url() + service, envelope.encode("utf-8"), headers)
    if 300 <= status < 400:
        raise WsdpError("status", status)
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise WsdpError("unreadable", status) from None
    fault = next((el for el in root.iter() if el.tag.endswith("}faultcode") or el.tag == "faultcode"), None)
    if fault is not None and "FailedAuthentication" in (fault.text or ""):
        raise WsdpError("refused", status)
    if fault is not None or status >= 400:
        raise WsdpError("status", status)
    return root


def check_login(username: str, password: str) -> str:
    """The login name the service knows this account by; `WsdpError('refused')` on a wrong login."""
    root = _call("vyhledat", "StavWS", "<typ:StavWSRequest></typ:StavWSRequest>", username, password)
    name = next((el.text for el in root.iter() if el.tag.endswith("}prihlasovaciJmeno")), None)
    if not name:
        raise WsdpError("unreadable")
    return name


MAX_PRICE_OBJECTS = 200  # the decree's cap per output; a 201st id is refused by the service (error 505)


@dataclass(frozen=True)
class Unit:
    iskn_id: int
    label: str  # "<č.p.>/<unit number>", built from the service's packed number


@dataclass(frozen=True)
class Report:
    id: int
    state: str  # čeká | vytváří se | zpracován | zaúčtován | …
    price_czk: int | None  # known once the report is made; charged at its first fetch


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


REPORT_GONE = 304  # "sestava neexistuje, nebo je již smazaná" (WSDP 3.1 description, § 3.9.2)


def _result_ok(root: ET.Element) -> None:
    """The service's own verdict. Every WSDP answer carries a `zprava`; one at level CHYBA is a refusal with HTTP
    200. An answer with none is not the service's (a proxy's page, a stub), so it proves nothing about the login."""
    messages = [el for el in root.iter() if _local(el) == "zprava"]
    if not messages:
        raise WsdpError("unreadable")
    for el in messages:
        if el.get("uroven") == "CHYBA":
            code = el.get("kod") or ""
            raise WsdpError("service", int(code) if code.isdigit() else 0, authenticated=True)


def _child_text(el: ET.Element, name: str) -> str:
    return next(((c.text or "").strip() for c in el if _local(c) == name), "")


def unit_label(packed: str) -> str:
    """The service packs a unit as <č.p.><unit number padded to four digits>: 1330019 is 133/19."""
    return f"{int(packed[:-4])}/{int(packed[-4:])}" if packed.isdigit() and len(packed) > 4 else packed


def find_units(building_iskn_id: int, username: str, password: str) -> list[Unit]:
    """The units of one building (free): their cadastre ids and labels."""
    root = _call("vyhledat", "NajdiJednotku",
                 f"<typ:NajdiJednotkuRequest><typ:stavbaId>{int(building_iskn_id)}</typ:stavbaId>"
                 "</typ:NajdiJednotkuRequest>", username, password)
    _result_ok(root)
    units = []
    for el in root.iter():
        if _local(el) == "Jednotka":
            iskn_id = _child_text(el, "idJednotky")
            if iskn_id.isdigit():
                units.append(Unit(int(iskn_id), unit_label(_child_text(el, "cislo"))))
    return units


def _report(root: ET.Element) -> tuple[Report, ET.Element]:
    _result_ok(root)
    el = next((e for e in root.iter() if _local(e) == "report"), None)
    if el is None or not _child_text(el, "id").isdigit():
        raise WsdpError("unreadable", authenticated=True)
    price = _child_text(el, "cena")
    return Report(int(_child_text(el, "id")), _child_text(el, "stav"), int(price) if price.isdigit() else None), el


PRICE_OBJECT_TYPES = ("JED_S", "STA_S")  # units; buildings (a house has no units)


def generate_prices(object_type: str, iskn_ids: list[int], username: str, password: str) -> Report:
    """Order the price report over these properties of ONE type: one paid output for up to 200 of them. Never
    call twice for one purchase: each call is a new report."""
    if object_type not in PRICE_OBJECT_TYPES or not 0 < len(iskn_ids) <= MAX_PRICE_OBJECTS:
        raise ValueError(f"1 to {MAX_PRICE_OBJECTS} properties of one of {PRICE_OBJECT_TYPES}")
    objects = "".join(f"<typ:seznamObjektu>{int(i)}</typ:seznamObjektu>" for i in iskn_ids)
    root = _call("sestavy", "GenerujCenoveUdajeDleNemovitosti",
                 f"<typ:GenerujCenoveUdajeDleNemovitostiRequest><typ:seznamObjektuTyp>{object_type}"
                 f"</typ:seznamObjektuTyp>{objects}<typ:format>xml</typ:format><typ:verze>1.0</typ:verze>"
                 "</typ:GenerujCenoveUdajeDleNemovitostiRequest>",
                 username, password)
    return _report(root)[0]


def report_state(report_id: int, username: str, password: str) -> Report:
    """Where a report stands (free)."""
    root = _call("sestavy", "SeznamSestav",
                 f"<typ:SeznamSestavRequest><typ:idSestavy>{int(report_id)}</typ:idSestavy></typ:SeznamSestavRequest>",
                 username, password)
    return _report(root)[0]


def fetch_report(report_id: int, username: str, password: str) -> tuple[Report, bytes]:
    """The report's file. Its FIRST fetch is the charge; fetching it again is free."""
    root = _call("sestavy", "VratSestavu",
                 f"<typ:VratSestavuRequest><typ:idSestavy>{int(report_id)}</typ:idSestavy></typ:VratSestavuRequest>",
                 username, password)
    report, el = _report(root)
    body = _child_text(el, "souborSestavy")
    if not body:
        raise WsdpError("unreadable", authenticated=True)
    try:
        return report, base64.b64decode(body, validate=True)
    except ValueError:
        raise WsdpError("unreadable", authenticated=True) from None
