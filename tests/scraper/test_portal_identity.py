"""The portal-identity canon (scraper/portal_identity.py) against the shared probe fixture.

tests/fixtures/portal_url_probes.json is read by BOTH languages: this file runs it through
`native_id` + `resolve_url`, frontend/src/lib/portalIds.test.ts through `parsePortalUrl`, so
the walk's identity and the browser's cannot drift apart. Each row's `kind` says which arms
apply: a `wrapper` names its ad only once unwrapped, `too-long` is refused by the cap, an
`ambiguous` path reads as an id to the walk's rule but is refused as a paste (the fetch would
rewrite it into another ad's page), and a `web` row is any other site: the tenth portal, keyed
by its canonical URL. The browser does not learn `web` (`"browser": null` on those rows: the
SPA leaves every host outside the nine to the server).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scraper import portal_identity as pi
from scraper.portal_identity import Refused, Resolved, native_id, resolve_url

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "portal_url_probes.json"
ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))
KINDS = {"detail", "relative", "wrapper", "not-ad", "unknown", "too-long", "ambiguous", "web"}
PORTAL_IDS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "portalIds.ts"


def _id(row: dict) -> str:
    return f"{row['kind']}:{row['note']}"


def test_fixture_rows_are_well_formed() -> None:
    for row in ROWS:
        assert {"input", "source", "native", "note", "kind"} <= set(row), row
        assert row["kind"] in KINDS, row
        assert row["source"] is None or row["source"] in pi.SOURCES, row
        assert row["kind"] != "web" or row["source"] == pi.WEB, row
        assert row["source"] != pi.WEB or row["kind"] in ("web", "wrapper"), row
        assert row["source"] != pi.WEB or row.get("browser", "missing") is None, row


def test_fixture_covers_every_portal_and_every_kind() -> None:
    absolute = {r["source"] for r in ROWS if r["kind"] == "detail"}
    relative = {r["source"] for r in ROWS if r["kind"] == "relative" and r["native"]}
    assert absolute == set(pi.PORTALS)
    crawlers = set(pi.PORTALS) - {"sreality", "bezrealitky"}
    assert relative == crawlers
    assert {r["kind"] for r in ROWS} == KINDS
    assert any(len(r["input"]) > pi.MAX_URL_LENGTH for r in ROWS if r["kind"] == "too-long")


@pytest.mark.parametrize(
    "row", [r for r in ROWS if r["source"] and r["kind"] not in ("wrapper", "ambiguous")], ids=_id,
)
def test_native_id_reads_the_fixture(row: dict) -> None:
    assert native_id(row["source"], row["input"]) == row["native"]


@pytest.mark.parametrize("row", [r for r in ROWS if r["kind"] != "relative"], ids=_id)
def test_resolve_url_reads_the_fixture(row: dict) -> None:
    got = resolve_url(row["input"])
    if row["kind"] == "too-long":
        assert got == Refused(pi.TOO_LONG_CS)
    elif row["source"] is None:
        assert got == Refused(pi.UNKNOWN_HOST_CS)
    elif row["native"] is None:
        assert got == Refused(pi.NOT_ONE_AD_CS)
    else:
        assert isinstance(got, Resolved)
        assert (got.source, got.native_id) == (row["source"], row["native"])
        assert "#" not in got.detail_ref


def test_patterns_are_written_for_both_languages() -> None:
    for source, (_hosts, rule) in pi.PORTALS.items():
        assert not re.search(r"\\[dbwsDBWS]|\(\?<|\(\?P", rule.pattern), source


def test_detail_ref_keeps_the_query_drops_the_fragment_and_lowercases_the_host() -> None:
    got = resolve_url("https://Reality.IDNES.cz/detail/prodej/byt/praha/6ad7ca9d45dd870c0a0b152a/?s-et=flat#gallery")
    assert got == Resolved(
        "idnes", "6ad7ca9d45dd870c0a0b152a",
        "https://reality.idnes.cz/detail/prodej/byt/praha/6ad7ca9d45dd870c0a0b152a/?s-et=flat",
    )


def test_detail_ref_drops_userinfo_and_a_default_port() -> None:
    got = resolve_url("https://user:pw@www.mmreality.cz:443/nemovitosti/957233/")
    assert isinstance(got, Resolved)
    assert got.detail_ref == "https://www.mmreality.cz/nemovitosti/957233/"


@pytest.mark.parametrize("text, expected", [
    ("Koukni na tohle https://www.mmreality.cz/nemovitosti/957233/ 2+kk, co myslíš?", "957233"),
    ("(viz https://www.mmreality.cz/nemovitosti/957233/).", "957233"),
    ("www.mmreality.cz/nemovitosti/957233/", "957233"),
    ("mmreality.cz/nemovitosti/957233/", "957233"),
    ("první https://www.mmreality.cz/nemovitosti/957233/ druhý https://www.mmreality.cz/nemovitosti/111111/", "957233"),
])
def test_the_first_link_in_pasted_prose_is_the_ad(text: str, expected: str) -> None:
    got = resolve_url(text)
    assert isinstance(got, Resolved)
    assert (got.source, got.native_id) == ("mmreality", expected)
    assert got.detail_ref.startswith("https://")


@pytest.mark.parametrize("text", ["", "   ", "dobrý den, posílám byt", "ftp://reality.bazos.cz/inzerat/1/x.php",
                                  "javascript:alert(1)", "https://"])
def test_text_without_an_http_link_is_refused(text: str) -> None:
    assert resolve_url(text) == Refused(pi.NO_URL_CS)


@pytest.mark.parametrize("url", [
    "https://m.sreality.cz/detail/prodej/byt/2+kk/praha/12345678",
    "https://zpravy.idnes.cz/domaci/clanek.A251020_1",
    "https://auto.bazos.cz/inzerat/187654321/skoda-octavia.php",
])
def test_a_portal_subdomain_we_do_not_fetch_is_refused(url: str) -> None:
    assert resolve_url(url) == Refused(pi.UNKNOWN_HOST_CS)


def test_the_hosts_are_the_browser_mirrors() -> None:
    ts = PORTAL_IDS_TS.read_text(encoding="utf-8")
    mirror = {
        source: tuple(re.findall(r"'([^']+)'", hosts))
        for source, hosts in re.findall(r"source: '(\w+)',\s*hosts: \[([^\]]*)\]", ts)
    }
    assert mirror == {source: hosts for source, (hosts, _rule) in pi.PORTALS.items()}


def test_a_non_default_port_is_another_service() -> None:
    assert resolve_url("https://reality.bazos.cz:22/inzerat/1/x.php") == Refused(pi.UNKNOWN_HOST_CS)
    assert resolve_url("http://reality.bazos.cz:8080/inzerat/1/x.php") == Refused(pi.UNKNOWN_HOST_CS)
    got = resolve_url("http://reality.bazos.cz:80/inzerat/1/x.php")
    assert isinstance(got, Resolved) and got.detail_ref == "http://reality.bazos.cz/inzerat/1/x.php"


@pytest.mark.parametrize("url, sentence", [
    ("https://evil.com\\@reality.bazos.cz/inzerat/1/x.php", pi.UNKNOWN_HOST_CS),
    ("https://reality.bazos.cz/inzerat/1\\..\\..\\inzerat/2/x.php", pi.NOT_ONE_AD_CS),
    ("https://reality.bazos.cz/inzerat/1/%5C..%5Cinzerat/2/x.php", pi.NOT_ONE_AD_CS),
])
def test_a_backslash_never_reaches_the_fetch(url: str, sentence: str) -> None:
    assert resolve_url(url) == Refused(sentence)


def test_tracking_keys_are_dropped_and_every_other_parameter_kept() -> None:
    wrapped = ("https://l.facebook.com/l.php?u=https%3A%2F%2Freality.bazos.cz%2Finzerat%2F1%2Fx.php"
               "%3Fref%3Dhp%26fbclid%3DIwAR0abc%26src%3Dfb%26UTM_Source%3Dfb%26gclid%3Dx&h=AT0")
    got = resolve_url(wrapped)
    assert isinstance(got, Resolved)
    assert got.detail_ref == "https://reality.bazos.cz/inzerat/1/x.php?ref=hp&src=fb"
    plain = resolve_url("https://reality.bazos.cz/inzerat/1/x.php?utm_medium=a&igshid=b&mc_eid=c")
    assert isinstance(plain, Resolved) and plain.detail_ref == "https://reality.bazos.cz/inzerat/1/x.php"


def test_a_lookalike_host_is_not_the_portal() -> None:
    """Another site, so the tenth portal's: never sreality's id, never a fetch by sreality's client."""
    for url in ("https://notsreality.cz/detail/prodej/byt/x/12345678",
                "https://sreality.cz.evil.com/detail/prodej/byt/x/12345678"):
        got = resolve_url(url)
        assert isinstance(got, Resolved) and (got.source, got.native_id) == (pi.WEB, url)


def test_a_wrapper_without_a_target_is_just_its_own_host() -> None:
    assert resolve_url("https://l.facebook.com/l.php?h=AT0") == Refused(pi.UNKNOWN_HOST_CS)
    assert resolve_url("https://www.google.com/url?q=byty+praha") == Refused(pi.UNKNOWN_HOST_CS)


def test_the_cap_reads_the_unwrapped_link() -> None:
    wrapped = ("https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.mmreality.cz%2Fnemovitosti%2F957233%2F&h="
               + "A" * 600)
    assert len(wrapped) > pi.MAX_URL_LENGTH
    got = resolve_url(wrapped)
    assert isinstance(got, Resolved) and got.native_id == "957233"


def test_native_id_tolerates_empty_and_broken_hrefs() -> None:
    assert native_id("bazos", None) is None
    assert native_id("bazos", "") is None
    assert native_id("bazos", "http://[broken/inzerat/1/") is None


def test_native_id_reads_the_path_not_the_query() -> None:
    assert native_id("remax", "/hledani?next=/reality/detail/450988/x") is None
    assert native_id("bazos", "https://reality.bazos.cz/inzerat/1/a.php?ref=/inzerat/2/") == "1"


@pytest.mark.parametrize("module", [
    "bazos_parser", "idnes_parser", "ceskereality_parser", "realitymix_parser", "remax_parser",
    "maxima_parser", "mmreality_parser", "mmreality_main",
])
def test_the_walk_reads_identity_from_the_canon_only(module: str) -> None:
    import importlib

    mod = importlib.import_module(f"scraper.{module}")
    assert mod.native_id is native_id
    copies = {"_ID_RE", "_REF_ID_RE", "_id_from_href", "_id_from_url"} & set(vars(mod))
    assert not copies, f"scraper.{module} grew a private identity rule again: {sorted(copies)}"


# --- the tenth portal: any other site --------------------------------------------------------


def test_any_other_site_is_a_web_ad_keyed_by_its_canonical_url() -> None:
    got = resolve_url("Mrkni: http://www.RK-Novak.cz/byty/15/?utm_campaign=x&b=1&a=2#foto")
    assert got == Resolved(
        pi.WEB, "https://www.rk-novak.cz/byty/15?a=2&b=1", "http://www.rk-novak.cz/byty/15/?b=1&a=2")


def test_the_canonical_url_is_idempotent_and_its_own_native_id() -> None:
    for row in (r for r in ROWS if r["source"] == pi.WEB):
        canon = pi.canonical_url(row["native"])
        assert canon == row["native"] and native_id(pi.WEB, canon) == canon


def test_a_web_ad_and_its_query_variants_collapse_only_on_order_and_tracking() -> None:
    a = resolve_url("https://www.rk-novak.cz/detail?id=7&lang=cs")
    b = resolve_url("https://www.rk-novak.cz/detail?lang=cs&id=7&gclid=z")
    c = resolve_url("https://www.rk-novak.cz/detail?id=8&lang=cs")
    assert isinstance(a, Resolved) and isinstance(b, Resolved) and isinstance(c, Resolved)
    assert a.native_id == b.native_id != c.native_id


@pytest.mark.parametrize("url", [
    "http://10.0.0.5/inzerat/1", "http://[::1]/x", "http://localhost/x", "https://intranet/x",
    "https://metadata.google.internal/computeMetadata/v1/", "https://nas.local/x",
    "http://2130706433/x", "https://www.bazos.cz/inzerat/1/x.php", "https://zpravy.idnes.cz/clanek",
])
def test_a_host_that_is_no_web_ad_is_refused(url: str) -> None:
    assert resolve_url(url) == Refused(pi.UNKNOWN_HOST_CS)


def test_a_web_path_the_fetch_would_rewrite_names_no_ad() -> None:
    assert resolve_url("https://www.rk-novak.cz/byty/1/../2") == Refused(pi.NOT_ONE_AD_CS)


def test_canonical_url_refuses_what_is_no_absolute_http_url() -> None:
    assert pi.canonical_url("/relative/path") is None
    assert pi.canonical_url("ftp://example.cz/x") is None
    assert pi.canonical_url("https://example.cz/" + "a" * 600) is None
