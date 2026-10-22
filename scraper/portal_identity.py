"""The ONE portal-identity canon: which portal a URL is on, and which ad it names.

`native_id` is the walk's identity (the seven HTML parsers import it) and `resolve_url`
is the paste box's (POST /listings/ingest). The patterns compile identically in Python
and JavaScript ([0-9], no word boundary, no named groups, no lookbehind):
frontend/src/lib/portalIds.ts mirrors them, and tests/fixtures/portal_url_probes.json
holds both languages to the same answers. No DB, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qs, unquote, unquote_plus, urlsplit, urlunsplit

MAX_URL_LENGTH = 512

NO_URL_CS = "V textu není žádný odkaz."
TOO_LONG_CS = "Odkaz je příliš dlouhý."
NOT_ONE_AD_CS = "Tento odkaz nevede na jeden inzerát."
UNKNOWN_HOST_CS = "Tuto stránku zatím nečteme."

# source -> (the exact hosts its ads live on, the detail-id rule on the URL PATH). Exact, not
# by domain suffix: the host is also the host the drain FETCHES, and a sibling subdomain is
# another site (auto.bazos.cz is bazos's car section, same id space). The hosts are
# portalIds.ts's, and every host the walk stores (measured 2026-10-20).
PORTALS: dict[str, tuple[tuple[str, ...], re.Pattern[str]]] = {
    "sreality": (("www.sreality.cz", "sreality.cz"), re.compile(r"/detail/[^?#]*/([0-9]{5,18})/?$")),
    "bazos": (("reality.bazos.cz",), re.compile(r"/inzerat/([0-9]+)/")),
    "bezrealitky": (
        ("www.bezrealitky.cz", "bezrealitky.cz", "www.bezrealitky.com", "bezrealitky.com"),
        re.compile(r"/(?:nemovitosti-byty-domy|properties-flats-houses)/([0-9]+)"),
    ),
    "idnes": (("reality.idnes.cz",), re.compile(r"/detail/[^?#]*?/([0-9a-f]{16,})/?$")),
    "maxima": (("nemovitosti.maxima.cz",), re.compile(r"/nemovitosti/([a-z][0-9]+)/?$")),
    "remax": (("www.remax-czech.cz", "remax-czech.cz"), re.compile(r"/reality/detail/([0-9]+)")),
    "mmreality": (("www.mmreality.cz", "mmreality.cz"), re.compile(r"/nemovitosti/([0-9]+)/?")),
    "ceskereality": (("www.ceskereality.cz", "ceskereality.cz"), re.compile(r"-([0-9]{4,})\.html$")),
    "realitymix": (("www.realitymix.cz", "realitymix.cz"), re.compile(r"-([0-9]{4,})\.html$")),
}
_BY_HOST = {host: source for source, (hosts, _rule) in PORTALS.items() for host in hosts}
_DEFAULT_PORT = {"http": 80, "https": 443}
# Spec §3.1's tracking keys: a wrapper (Facebook's l.php) adds them whatever the operator
# pastes, and detail_ref becomes the shared, stored listings.source_url.
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "gbraid", "wbraid", "mc_eid", "igshid"})

# The same reading of pasted text as the SPA's lib/pastedLink.firstUrl, so the two agree.
_URL_IN_TEXT = re.compile(r"""(?:\bhttps?://|\bwww\.)[^\s<>"'«»“”‘’]+""", re.IGNORECASE)
_BARE_TOKEN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/|$)", re.IGNORECASE)
_TRAILING_PROSE = re.compile(r"[.,;:!?…]+$")
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)

_FACEBOOK_WRAPPERS = frozenset({"l.facebook.com", "lm.facebook.com"})
_GOOGLE_HOST = re.compile(r"(?:www\.)?google(?:\.[a-z]{2,3}){1,2}")
_MAX_UNWRAPS = 3


@dataclass(frozen=True)
class Resolved:
    """One portal ad. `detail_ref` is the cleaned URL (see `_cleaned`); an enqueue still routes
    it through `scraper.db.detail_ref` (sreality is fetched by id, never by URL)."""

    source: str
    native_id: str
    detail_ref: str


@dataclass(frozen=True)
class Refused:
    message_cs: str


def native_id(source: str, href: str | None) -> str | None:
    """The ad's native id from a detail URL or a relative index href; None when it names none."""
    if not href:
        return None
    try:
        path = urlsplit(href).path
    except ValueError:
        return None
    m = PORTALS[source][1].search(path)
    return m.group(1) if m else None


def _first_url(text: str) -> str | None:
    """The first URL in pasted text (a scheme, a bare `www.`, or one dotted token)."""
    m = _URL_IN_TEXT.search(text)
    if m:
        return _trim_prose(m.group(0))
    bare = text.strip()
    if bare and not re.search(r"\s", bare) and _BARE_TOKEN.match(bare):
        return _trim_prose(bare)
    return None


def resolve_url(text: str) -> Resolved | Refused:
    """The ad the FIRST link in pasted text names (wrappers unwrapped), or one Czech refusal."""
    url = _first_url(text)
    if url is None:
        return Refused(NO_URL_CS)
    if not _SCHEME.match(url):
        url = f"https://{url}"
    parts = _split(url)
    for _ in range(_MAX_UNWRAPS):
        inner = _unwrapped(parts) if parts is not None else None
        if inner is None:
            break
        parts = _split(inner)
    scheme = parts.scheme.lower() if parts is not None else ""
    if parts is None or scheme not in _DEFAULT_PORT or not parts.hostname:
        return Refused(NO_URL_CS)
    try:
        port = parts.port
    except ValueError:
        return Refused(NO_URL_CS)
    source = _BY_HOST.get(parts.hostname.rstrip("."))
    # requests ends the host at a backslash where urlsplit does not, so one in the netloc
    # could aim the drain's fetch at another host; a non-default port is another service.
    if source is None or "\\" in parts.netloc or port not in (None, _DEFAULT_PORT[scheme]):
        return Refused(UNKNOWN_HOST_CS)
    cleaned = _cleaned(parts)
    if len(cleaned) > MAX_URL_LENGTH:
        return Refused(TOO_LONG_CS)
    if _rewritten_path(parts.path):
        return Refused(NOT_ONE_AD_CS)
    nid = native_id(source, cleaned)
    if nid is None:
        return Refused(NOT_ONE_AD_CS)
    return Resolved(source=source, native_id=nid, detail_ref=cleaned)


def _rewritten_path(path: str) -> bool:
    """A path the fetch would rewrite (a `.` / `..` segment, a backslash, percent-encoded or
    not) names one ad in the id rule and fetches another, so it names no ad."""
    decoded = unquote(path)
    return "\\" in decoded or any(seg in (".", "..") for seg in decoded.split("/"))


def _trim_prose(url: str) -> str:
    out = url
    while True:
        before = out
        out = _TRAILING_PROSE.sub("", out)
        for opening, closing in (("(", ")"), ("[", "]")):
            if out.endswith(closing) and out.count(opening) < out.count(closing):
                out = out[:-1]
        if out == before:
            return out


def _split(url: str) -> SplitResult | None:
    try:
        return urlsplit(url)
    except ValueError:
        return None


def _unwrapped(parts: SplitResult) -> str | None:
    """The target of a known redirect wrapper (Facebook l.php, Google /url), read without a fetch."""
    host = (parts.hostname or "").rstrip(".")
    if host in _FACEBOOK_WRAPPERS and parts.path == "/l.php":
        keys: tuple[str, ...] = ("u",)
    elif _GOOGLE_HOST.fullmatch(host) and parts.path == "/url":
        keys = ("q", "url")
    else:
        return None
    query = parse_qs(parts.query)
    for key in keys:
        for value in query.get(key, ()):
            if value.lower().startswith(("http://", "https://")):
                return value.strip()
    return None


def _cleaned(parts: SplitResult) -> str:
    """The URL the drain fetches: scheme and host lowercased; userinfo, port, tracking keys and
    fragment dropped; every other query parameter kept as it was written."""
    query = "&".join(
        pair for pair in parts.query.split("&")
        if pair and not _is_tracking(unquote_plus(pair.split("=", 1)[0]).lower())
    )
    host = (parts.hostname or "").rstrip(".")
    return urlunsplit((parts.scheme.lower(), host, parts.path, query, ""))


def _is_tracking(key: str) -> bool:
    return key.startswith("utm_") or key in _TRACKING_KEYS
