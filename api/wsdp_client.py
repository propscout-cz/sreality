"""ČÚZK WSDP (the cadastre's SOAP web service): HTTP only. One call so far, the free status call
that proves a login. Never retried: three refused logins block the account for 15 minutes. An error
names its kind only, so neither the envelope nor the password can reach a log."""

from __future__ import annotations

import os
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
