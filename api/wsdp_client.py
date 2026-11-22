"""ČÚZK WSDP (the cadastre's SOAP web service): HTTP only. The free status call that proves a login, the free
search of a building's units, and a report's three steps (generate, poll, fetch). Nothing here is retried: three
refused logins block the account for 15 minutes, and a repeated generate is a second purchase. An error names its
kind only, so neither the envelope nor the password can reach a log."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests

PRODUCTION_BASE = "https://katastr.cuzk.gov.cz/ws/wsdp/3.1/"
# The override exists for ČÚZK's free test service. A login is only ever sent to one of ČÚZK's own two hosts, over https.
BASE_ENV = "CUZK_WSDP_BASE"
HOSTS = frozenset({"katastr.cuzk.gov.cz", "wsdptrial.cuzk.gov.cz"})
TIMEOUT = (5, 20)

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
    """`reason` is refused | timeout | unreachable | status | unreadable | misconfigured."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(f"wsdp {reason} status={status}")
        self.reason, self.status = reason, status


def base_url() -> str:
    base = os.environ.get(BASE_ENV) or PRODUCTION_BASE
    parts = urlsplit(base)
    if parts.scheme != "https" or parts.hostname not in HOSTS or not base.endswith("/"):
        raise WsdpError("misconfigured")
    return base


def _call(service: str, operation: str, body: str, username: str, password: str) -> ET.Element:
    envelope = _ENVELOPE.format(service=service, username=escape(username), password=escape(password), body=body)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"urn:cz:gov:cuzk:iskn:services:wsdp:{service}:{operation}"',
    }
    try:
        # Never a redirect: it would re-send the password to whatever host the answer names.
        resp = requests.post(base_url() + service, data=envelope.encode("utf-8"), headers=headers, timeout=TIMEOUT,
                             allow_redirects=False)
    except requests.Timeout:
        raise WsdpError("timeout") from None
    except requests.RequestException:
        raise WsdpError("unreachable") from None
    if 300 <= resp.status_code < 400:
        raise WsdpError("status", resp.status_code)
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        raise WsdpError("unreadable", resp.status_code) from None
    fault = next((el for el in root.iter() if el.tag.endswith("}faultcode") or el.tag == "faultcode"), None)
    if fault is not None and "FailedAuthentication" in (fault.text or ""):
        raise WsdpError("refused", resp.status_code)
    if fault is not None or resp.status_code >= 400:
        raise WsdpError("status", resp.status_code)
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


def _result_ok(root: ET.Element) -> None:
    """The service's own verdict: a `zprava` at level CHYBA is a refusal with HTTP 200."""
    for el in root.iter():
        if _local(el) == "zprava" and el.get("uroven") == "CHYBA":
            raise WsdpError("status", int(el.get("kod") or 0))


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
        raise WsdpError("unreadable")
    price = _child_text(el, "cena")
    return Report(int(_child_text(el, "id")), _child_text(el, "stav"), int(price) if price.isdigit() else None), el


def generate_unit_prices(unit_iskn_ids: list[int], username: str, password: str) -> Report:
    """Ask for the price report over these units: one paid output for up to 200 of them. Never call twice for
    one purchase: each call is a new report."""
    if not 0 < len(unit_iskn_ids) <= MAX_PRICE_OBJECTS:
        raise ValueError(f"1 to {MAX_PRICE_OBJECTS} units per report")
    objects = "".join(f"<typ:seznamObjektu>{int(i)}</typ:seznamObjektu>" for i in unit_iskn_ids)
    root = _call("sestavy", "GenerujCenoveUdajeDleNemovitosti",
                 "<typ:GenerujCenoveUdajeDleNemovitostiRequest><typ:seznamObjektuTyp>JED_S</typ:seznamObjektuTyp>"
                 f"{objects}<typ:format>xml</typ:format><typ:verze>1.0</typ:verze>"
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
        raise WsdpError("unreadable")
    return report, base64.b64decode(body)
