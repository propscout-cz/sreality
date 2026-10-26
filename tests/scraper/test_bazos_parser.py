"""Hermetic tests for scraper.bazos_parser against hand-authored fixtures.

These use minimal, synthetic HTML that mirrors the documented bazos markup
(div.inzeraty.inzeratyflex index blocks, the detail details-table, the
Google-Maps locality link).
"""

from __future__ import annotations

import re

from scraper.bazos_parser import _resolve_coords, parse_detail, parse_index
from scraper.hashing import digest
from scraper.scraped_listing import ScrapedListing

INDEX_HTML = """
<!DOCTYPE html><html><body>
<div class="listadvert">Zobrazeno 1-20 inzerátů z 6 990</div>
<div class="inzeraty inzeratyflex">
  <h2 class="nadpis"><a href="/inzerat/219122924/prodam-byt-2-kk-letovice.php">Prodám byt 2+kk Letovice</a></h2>
  <span class="velikost10">[12.5. 2026]</span>
  <div class="popis">Pěkný byt 2+kk o výměře 65 m² v klidné lokalitě.</div>
  <div class="inzeratycena">5 499 000 Kč</div>
  <div class="inzeratylok">Letovice<br>679 61</div>
  <div class="inzeratyview">123 x</div>
  <img class="obrazek" src="https://www.bazos.cz/img/1/219/219122924.jpg">
</div>
<div class="inzeraty inzeratyflex">
  <h2 class="nadpis"><a href="/inzerat/219122925/prodam-byt-3-1-brno.php">Prodám byt 3+1 Brno</a></h2>
  <span class="velikost10">[11.5. 2026]</span>
  <div class="popis">Prostorný byt 3+1, 82 m2.</div>
  <div class="inzeratycena">Dohodou</div>
  <div class="inzeratylok">Brno<br>602 00</div>
  <div class="inzeratyview">45 x</div>
  <img class="obrazek" src="https://www.bazos.cz/img/1/219/219122925.jpg">
</div>
<div class="strankovani">
  <a href="/prodam/byt/">1</a>
  <a href="/prodam/byt/20/">2</a>
  <a href="/prodam/byt/20/">Další</a>
</div>
</body></html>
"""

DETAIL_HTML = """
<!DOCTYPE html><html><body>
<div class="drobky"><a href="https://www.bazos.cz/">Hlavní stránka</a> > <a href="https://reality.bazos.cz/">Reality</a> > <a href="https://reality.bazos.cz/prodam/">Prodej</a> > <a href="https://reality.bazos.cz/prodam/byt/">Byty</a> > <b>Inzerát č. 219122924</b></div>
<h1 class="nadpisdetail">Prodám byt 2+kk Letovice</h1>
<span class="velikost10">[12.5. 2026]</span>
<table class="listadvalues">
  <tr><td>Cena:</td><td class="listadvalue">5 499 000 Kč</td></tr>
  <tr><td>Lokalita:</td><td><a href="https://www.google.com/maps/place/49.863882,16.333580/">Letovice 679 61 <span>Zobrazit na mapě</span></a></td></tr>
  <tr><td>Vidělo:</td><td>123 x</td></tr>
  <tr><td>Jméno:</td><td>agent@example.cz</td></tr>
</table>
<div class="popisdetail">Pěkný byt 2+kk o celkové výměře 65 m² v klidné lokalitě.</div>
<img src="https://www.bazos.cz/img/1/924/219122924.jpg">
<img src="https://www.bazos.cz/img/1t/924/219122924.jpg">
<img src="https://www.bazos.cz/img/2t/924/219122924.jpg">
<img src="https://www.bazos.cz/img/3t/924/219122924.jpg">
<div class="podobne"><img src="https://www.bazos.cz/img/1t/777/219777777.jpg"></div>
</body></html>
"""

DOHODOU_DETAIL_HTML = """
<!DOCTYPE html><html><body>
<h1 class="nadpisdetail">Pronájem byt 3+1 Brno</h1>
<table>
  <tr><td>Cena:</td><td>Dohodou</td></tr>
  <tr><td>Lokalita:</td><td>Brno 602 00</td></tr>
</table>
<div class="popisdetail">Byt 3+1 o výměře 82 m2.</div>
</body></html>
"""

# Verbatim Lokalita row captured live from reality.bazos.cz (2026): a THREE-cell
# layout — label, a map-icon cell, then a cell holding the maps link (PSČ as its
# anchor text) followed by a separate town-listings anchor, i.e. "PSČ Town" order.
# The hand-authored 2-cell "Town PSČ" fixtures above had diverged from this and
# hid a 100%-NULL-locality bug.
LIVE_LOKALITA_DETAIL_HTML = """
<!DOCTYPE html><html><body>
<div class="drobky"><a href="https://www.bazos.cz/">Hlavní stránka</a> > <a href="https://reality.bazos.cz/">Reality</a> > <a href="https://reality.bazos.cz/pronajmu/">Pronájem</a> > <a href="https://reality.bazos.cz/pronajmu/byt/">Byty</a> > <b>Inzerát č. 219722150</b></div>
<h1 class="nadpisdetail">Pronájem bytu 2+1, 82 m², Plzeň, ul. Koterovská</h1>
<table class="listadvalues">
  <tr><td height="22px" width="25%">Lokalita:</td><td width="9%"><img src="https://www.bazos.cz/obrazky/map.svg" alt="Mapa" height="17"></td><td><a href="https://www.google.com/maps/place/49.720928,13.421173/@49.720928,13.421173,12z/data=!4m2!3m1!1s0x0:0x0" title="Přibližná lokalita" rel="nofollow">326 00</a> <a href="https://reality.bazos.cz/inzeraty/plzen-26/32600/">Plzeň</a></td></tr>
  <tr><td height="22px" width="25%">Cena:</td><td colspan=2>15 900 Kč</td></tr>
</table>
<div class="popisdetail">Nabízíme k pronájmu prostorný a světlý byt 2+1.</div>
<img src="https://www.bazos.cz/img/1/150/219722150.jpg">
</body></html>
"""


def test_parse_detail_live_three_cell_lokalita_yields_locality():
    """Regression for the production bug: the live 3-cell Lokalita layout must
    yield a real locality."""
    listing = parse_detail(
        LIVE_LOKALITA_DETAIL_HTML,
        source_url="https://reality.bazos.cz/inzerat/219722150/x.php",
        category_main="byt", category_type="prodej",
    )
    # Locality + PSČ now parse from the third cell, in "PSČ Town" order.
    assert listing.locality == "Plzeň"
    assert listing.raw["psc"] == "326 00"
    # W0 item 0c: the PSČ reaches listings.zip, not just raw_json.
    assert listing.zip == "326 00"
    # Breadcrumb is authoritative for the category.
    assert (listing.category_main, listing.category_type) == ("byt", "pronajem")
    # No parser street any more: the text lane reads it (2026-09-30).
    assert listing.street is None and "street" not in listing.raw["coords"]
    # The ad's own maps-link pin is the ONLY coordinate bazos publishes.
    assert listing.raw["coords"]["source"] == "link"
    assert (listing.lat, listing.lon) == (49.720928, 13.421173)


def test_parse_index_total_and_items():
    page = parse_index(INDEX_HTML)
    assert page.total == 6990
    assert len(page.items) == 2
    assert page.next_offset == 20

    first = page.items[0]
    assert first.source_id_native == "219122924"
    assert first.detail_path == "/inzerat/219122924/prodam-byt-2-kk-letovice.php"
    assert first.title == "Prodám byt 2+kk Letovice"
    assert first.price_text == "5 499 000 Kč"
    assert "Letovice" in (first.locality_text or "")


def test_next_offset_handles_locality_query_string():
    # A locality-filtered search appends ?hlokalita=…&humkreis=… AFTER the
    # offset path segment; the pager parse must still find the offset, else the
    # walk stops after page 1 and never proves completeness.
    html = """
<!DOCTYPE html><html><body>
<div class="strankovani">
  <a href="/prodam/byt/?hlokalita=Praha&humkreis=10">1</a>
  <a href="/prodam/byt/20/?hlokalita=Praha&humkreis=10">2</a>
  <a href="/prodam/byt/20/?hlokalita=Praha&humkreis=10">Další</a>
</div>
</body></html>
"""
    assert parse_index(html).next_offset == 20


def test_parse_index_ignores_non_listing_divs():
    # The strankovani / header divs must not produce phantom items.
    page = parse_index(INDEX_HTML)
    assert all(item.source_id_native.isdigit() for item in page.items)


def test_parse_detail_full():
    url = "https://reality.bazos.cz/inzerat/219122924/prodam-byt-2-kk-letovice.php"
    listing = parse_detail(
        DETAIL_HTML, source_url=url, category_main="byt", category_type="prodej"
    )

    assert listing.source == "bazos"
    assert listing.source_id_native == "219122924"
    assert listing.source_url == url
    assert listing.category_main == "byt"
    assert listing.category_type == "prodej"
    assert listing.price_czk == 5_499_000
    assert listing.price_unit == "za nemovitost"
    assert listing.area_m2 == 65.0
    # bazos has no structured area field at all — the headline is the free-text
    # scrape, and the stamp says exactly that.
    assert listing.area_basis == "unknown"
    assert listing.disposition == "2+kk"
    assert listing.lat == 49.863882
    assert listing.lon == 16.333580
    assert listing.locality == "Letovice"
    assert listing.description.startswith("Pěkný byt")
    assert listing.raw["psc"] == "679 61"
    # cover (/img/1/) + thumbnail strip (/img/1t/,2t,3t) → 3 full-size, deduped;
    # the "podobné inzeráty" thumbnail (a different ad id) is excluded.
    assert listing.raw["image_urls"] == [
        "https://www.bazos.cz/img/1/924/219122924.jpg",
        "https://www.bazos.cz/img/2/924/219122924.jpg",
        "https://www.bazos.cz/img/3/924/219122924.jpg",
    ]


def test_parse_detail_published_at_from_bracketed_date():
    # span.velikost10 "[12.5. 2026]" — bazos's posting date. It re-stamps on
    # every bump / TOP renewal (a last-bump date), but it's the tightest
    # publish bound the portal exposes.
    from datetime import date

    url = "https://reality.bazos.cz/inzerat/219122924/prodam-byt-2-kk-letovice.php"
    listing = parse_detail(
        DETAIL_HTML, source_url=url, category_main="byt", category_type="prodej"
    )
    assert listing.published_at == date(2026, 5, 12)


def test_parse_detail_published_at_none_when_no_date_span():
    listing = parse_detail(
        DOHODOU_DETAIL_HTML, source_url="https://reality.bazos.cz/inzerat/1/x.php",
        category_main="byt", category_type="pronajem",
    )
    assert listing.published_at is None


def test_parse_detail_extracts_floor_from_description():
    # parse_detail feeds the title+description haystack to the shared floor miner;
    # an explicit unit-floor cue lands as a ground=0 integer (the fixture above has
    # no floor cue, so listing.floor stays None there — no regression).
    html = (
        "<html><body>"
        '<h1 class="nadpisdetail">Prodám byt 2+kk</h1>'
        '<div class="popisdetail">Byt se nachází ve 3. patře cihlového domu.</div>'
        "</body></html>"
    )
    listing = parse_detail(
        html,
        source_url="https://reality.bazos.cz/inzerat/1/x.php",
        category_main="byt",
        category_type="prodej",
    )
    assert listing.floor == 3


def test_parse_detail_images_are_fullsize_deduped_and_own():
    # bazos detail pages carry the full cover + a thumbnail strip + a "similar ads"
    # footer; store the full-size variant of every OWN photo, never a thumbnail,
    # never the same photo twice, and never another ad's image.
    url = "https://reality.bazos.cz/inzerat/219122924/prodam-byt-2-kk-letovice.php"
    urls = parse_detail(
        DETAIL_HTML, source_url=url, category_main="byt", category_type="prodej"
    ).raw["image_urls"]
    assert all("/img/" in u and "t/" not in u.split("/img/")[1] for u in urls)
    assert len(urls) == len(set(urls))
    assert all("219122924" in u for u in urls)
    assert not any("219777777" in u for u in urls)


def test_parse_detail_category_from_breadcrumb_overrides_fallback():
    # The page breadcrumb is authoritative: a "Pronájem" page parses as a rental
    # even when the caller passes the sale fallback (the drain's primary scope).
    # Flipping the breadcrumb LINK segments (prodam -> pronajmu) is what a real
    # rental page looks like — the regression that mis-tagged ~6.6k rentals as
    # sales was the parser reading the localised TEXT, not these href segments.
    html = DETAIL_HTML.replace("/prodam/", "/pronajmu/")
    url = "https://reality.bazos.cz/inzerat/219122924/pronajmu-byt-2-kk-letovice.php"
    listing = parse_detail(
        html, source_url=url, category_main="byt", category_type="prodej"
    )
    assert listing.category_main == "byt"
    assert listing.category_type == "pronajem"   # breadcrumb wins over the fallback
    assert listing.price_unit == "za mesic"      # price unit follows the real type


def test_parse_detail_category_from_real_breadcrumb_markup():
    # Verbatim breadcrumb captured from a live reality.bazos.cz detail page: a
    # plain ">" separator (NOT "»") and the category carried in the link hrefs.
    # Guards against the format the hand-authored "»" fixture missed.
    html = """
<!DOCTYPE html><html><body>
<div class="drobky"><a href="https://www.bazos.cz/" title="Inzerce Bazoš">Hlavní stránka</a>  > <a href="https://reality.bazos.cz/">Reality</a> > <a href="https://reality.bazos.cz/pronajmu/">Pronájem</a> > <a href="https://reality.bazos.cz/pronajmu/byt/">Byty</a> > <b>Inzerát č. 219625164</b></div>
<h1 class="nadpisdetail">Pronájem bytu 3+kk 79,7 m, Pardubice</h1>
<table><tr><td>Cena:</td><td>20 000 Kč</td></tr></table>
<div class="popisdetail">Pronájem bytu 3+kk.</div>
</body></html>
"""
    url = "https://reality.bazos.cz/inzerat/219625164/pronajem-bytu-3kk.php"
    listing = parse_detail(
        html, source_url=url, category_main="byt", category_type="prodej"
    )
    assert (listing.category_main, listing.category_type) == ("byt", "pronajem")
    assert listing.price_unit == "za mesic"


def test_parse_detail_falls_back_when_breadcrumb_missing():
    html = re.sub(r'<div class="drobky">.*?</div>', "", DETAIL_HTML)
    url = "https://reality.bazos.cz/inzerat/219122924/x.php"
    listing = parse_detail(
        html, source_url=url, category_main="byt", category_type="prodej"
    )
    assert (listing.category_main, listing.category_type) == ("byt", "prodej")


# Verbatim breadcrumb shape captured live from reality.bazos.cz fine-section
# detail pages (div.drobky, the section in the LAST link's href). The fine
# section drives BOTH category_main and the portal-agnostic subtype.
def _bazos_section_html(section: str) -> str:
    return (
        '<!DOCTYPE html><html><body>'
        f'<div class="drobky"><a href="https://www.bazos.cz/">Hlavní stránka</a> > '
        f'<a href="https://reality.bazos.cz/">Reality</a> > '
        f'<a href="https://reality.bazos.cz/prodam/">Prodej</a> > '
        f'<a href="https://reality.bazos.cz/prodam/{section}/">X</a> > '
        '<b>Inzerát č. 1</b></div>'
        '<h1 class="nadpisdetail">Detail</h1>'
        '<table><tr><td>Cena:</td><td>1 000 000 Kč</td></tr></table>'
        '<div class="popisdetail">Popis.</div></body></html>'
    )


def test_parse_detail_subtype_from_fine_section_breadcrumb():
    cases = {
        "chata": ("dum", "chata"),
        "kancelar": ("komercni", "kancelar"),
        "sklad": ("komercni", "sklad"),
        "prostory": ("komercni", "obchodni_prostor"),
        "restaurace": ("komercni", "restaurace"),
        "zahrada": ("pozemek", "zahrada"),
        "garaz": ("ostatni", "garaz"),
        "dum": ("dum", None),          # generic house section — no subtype
        "pozemek": ("pozemek", None),  # generic land section — no subtype
        "ostatni": ("ostatni", None),  # generic "other" section — no subtype
        "byt": ("byt", None),
    }
    for section, (cm, sub) in cases.items():
        listing = parse_detail(
            _bazos_section_html(section),
            source_url=f"https://reality.bazos.cz/inzerat/1/{section}.php",
            category_main=None, category_type=None,
        )
        assert listing.category_main == cm, section
        assert listing.subtype == sub, section


def test_parse_detail_coords_from_map_link_outside_lokalita_cell():
    """Live bazos renders the "show on map" link OUTSIDE the Lokalita table cell
    (the fixture above happens to have it inside). Coords must still be extracted
    from the page-wide Google-Maps / Mapy.cz link — the bug that left every real
    bazos listing with NULL geom and starved cross-source dedup."""
    html = """
    <!DOCTYPE html><html><body>
    <h1 class="nadpisdetail">Prodám byt 3+kk Brno</h1>
    <table class="listadvalues">
      <tr><td>Cena:</td><td>3 500 000 Kč</td></tr>
      <tr><td>Lokalita:</td><td>Brno 602 00</td></tr>
    </table>
    <div class="popisdetail">Byt 3+kk o výměře 70 m².</div>
    <div class="mapadetail">
      <a href="https://www.google.com/maps/place/49.180806,16.675186/@49.18,16.67,12z">Zobrazit na mapě</a>
    </div>
    </body></html>
    """
    listing = parse_detail(
        html, source_url="https://reality.bazos.cz/inzerat/999/x.php",
        category_main="byt", category_type="prodej",
    )
    assert listing.lat == 49.180806
    assert listing.lon == 16.675186
    assert listing.locality == "Brno"


def test_parse_coords_rejects_out_of_cz_bounds():
    from scraper.bazos_parser import _parse_coords
    # A stray non-Czech decimal pair must never become geom.
    assert _parse_coords("https://x/maps/place/1.234567,2.345678/") == (None, None)
    # A real Czech pair passes through.
    assert _parse_coords("https://x/maps/place/50.087,14.421/") == (50.087, 14.421)


def test_parse_detail_content_hash_stable():
    url = "https://reality.bazos.cz/inzerat/219122924/x.php"
    a = parse_detail(DETAIL_HTML, source_url=url, category_main="byt", category_type="prodej")
    b = parse_detail(DETAIL_HTML, source_url=url, category_main="byt", category_type="prodej")
    assert digest(a.hash_doc()) == digest(b.hash_doc())
    assert len(digest(a.hash_doc())) == 64


def test_parsed_listing_bridges_into_the_one_writer():
    # listing_write.from_scraped(listing) carries the parsed fields; the writer, not
    # the contract, owns sreality_id (Gate 2 mints or leaves it NULL).
    from scraper import listing_write

    url = "https://reality.bazos.cz/inzerat/219122924/x.php"
    listing = parse_detail(DETAIL_HTML, source_url=url, category_main="byt", category_type="prodej")
    w = listing_write.from_scraped(listing)
    row = w.row
    assert (w.source, w.source_id_native, w.sreality_id) == ("bazos", "219122924", None)
    assert row["category_main"] == "byt"
    assert row["category_type"] == "prodej"
    assert row["price_czk"] == 5_499_000
    assert row["area_m2"] == 65.0


def test_parse_detail_dohodou_price_is_none():
    url = "https://reality.bazos.cz/inzerat/219122925/y.php"
    listing = parse_detail(
        DOHODOU_DETAIL_HTML, source_url=url,
        category_main="byt", category_type="pronajem",
    )
    assert listing.price_czk is None
    assert listing.price_unit == "za mesic"
    assert listing.disposition == "3+1"
    assert listing.area_m2 == 82.0


# --- coordinate resolution ---------------------------------------------------
# The geocoder and its cross-check are gone (W4-b): the ad's own CZ-guarded maps
# link is the only coordinate bazos publishes, so these pin exactly two outcomes.

def test_resolve_uses_the_cz_guarded_link():
    lat, lon, prov = _resolve_coords(link_lat=49.560, link_lon=16.580)
    assert (lat, lon) == (49.560, 16.580)
    assert prov == {"source": "link", "link_present": True}


def test_resolve_without_a_link_yields_no_coordinate():
    """No street geocode stands in for a missing pin any more — a bazos ad with
    no maps anchor simply has no position, and the claim intake licenses none."""
    lat, lon, prov = _resolve_coords(link_lat=None, link_lon=None)
    assert (lat, lon) == (None, None)
    assert prov["source"] is None
    assert prov["link_present"] is False


def test_parse_detail_records_coord_provenance():
    listing = parse_detail(
        DETAIL_HTML,
        source_url="https://reality.bazos.cz/inzerat/219122924/x.php",
        category_main="byt", category_type="prodej",
    )
    coords = listing.raw["coords"]
    assert coords["source"] == "link"
    assert coords["link_present"] is True
    assert (listing.lat, listing.lon) == (49.863882, 16.333580)


def test_lokalita_trailer_yields_the_quarter():
    from scraper.bazos_parser import _trailer_quarter as t

    assert t("Pěkný byt.\nLokalita: Bezručova, Slezské Předměstí", "Hradec Králové") == "Slezské Předměstí"
    assert t("Lokalita: Brno, Židenice", "Brno") == "Židenice"
    # Prose "lokalita" without a colon-anchored value never fires.
    assert t("Byt v klidné lokalitě u parku.", "Brno") is None
    assert t(None, "Brno") is None


def test_lokalita_trailer_on_newline_collapsed_text():
    # Review-confirmed critical on the first cut: parse_detail feeds _text()'d
    # descriptions — every newline collapsed to a space — so the trailer value
    # must terminate at a following label ("Kontakt:", "Cena:", ...).
    from scraper.bazos_parser import _trailer_quarter as t

    assert t("Lokalita: Bezručova, Slezské Předměstí Kontakt: Jan", "HK") == "Slezské Předměstí"
    assert t("Dispozice: 2+kk Lokalita: Bezručova Cena: dohodou", "Plzeň") is None


def test_lokalita_trailer_quarter_is_validated():
    # Review-confirmed major on the first cut: the quarter reached
    # listings.district unvalidated — and district is a HASHED field, so
    # garbage there churns a snapshot per affected row.
    from scraper.bazos_parser import _trailer_quarter as t

    # Lowercase prose is not a place name.
    assert t("Lokalita: Hradec Králové, klidná část u parku", "Hradec Králové") is None
    assert t("Lokalita: Praha 9, výborná dostupnost MHD", "Praha 9") is None
    # The row's own town never duplicates into the district facet.
    assert t("Lokalita: Nádražní, Ostrava", "Ostrava") is None


def test_spaced_thousands_in_the_ad_text_is_one_number():
    """W19: bazos held a sixth private copy of the same naive grammar, over FREE TEXT —
    the one substrate where a parcel is always written "1 500 m2". It reads the whole
    number now, through the one `scraper.area` grammar every portal shares (rule 21)."""
    from scraper.area import parse_area_text

    assert parse_area_text("Prodám pozemek 1 500 m2 v obci") == 1500.0
    assert parse_area_text("Prodám pozemek 1 500 m2 v obci") == 1500.0
    # The disposition in an ad's own prose is still never swallowed.
    assert parse_area_text("Byt 3+1 o velikosti 73 m²") == 73.0


def test_parse_detail_the_cellar_figure_is_not_the_flat():
    """Mechová 3+1 (bazos 14012930, a trial property stored at 2 m²): the only m² in the
    advert is the cellar's. parse_detail hands the resolver the disposition it read, so
    the per-room band declines the figure and the area is absence, not a veto-sized lie;
    a real figure in the same prose still reads."""
    html = (
        "<html><body>"
        '<h1 class="nadpisdetail">Prodej bytu 3+1 s balkonem</h1>'
        '<div class="popisdetail">Byt 3+1 v Mechové ulici. K bytu náleží sklepní kóje '
        "s okýnkem o velikosti 2 m² a podíl na sklepní místnosti.</div>"
        "</body></html>"
    )
    url = "https://reality.bazos.cz/inzerat/1/x.php"
    listing = parse_detail(html, source_url=url, category_main="byt", category_type="prodej")
    assert listing.disposition == "3+1"
    assert (listing.area_m2, listing.area_basis) == (None, None)

    stated = parse_detail(html.replace("Byt 3+1 v", "Byt 3+1 o výměře 70 m² v"),
                          source_url=url, category_main="byt", category_type="prodej")
    assert (stated.area_m2, stated.area_basis) == (70.0, "unknown")


def _bazos_page(title: str, body: str) -> str:
    return ("<html><body>"
            f'<h1 class="nadpisdetail">{title}</h1>'
            f'<div class="popisdetail">{body}</div>'
            "</body></html>")


def test_parse_detail_a_dotted_parcel_is_not_the_house():
    """bazos 186747 and 204860 (A4 review, 2026-09-26): the first m² in a house's prose is
    its parcel written with dotted thousands. The one grammar reads it as the number it is
    (1,139 / 1,256), and the dum ceiling on the unlabelled figure keeps that parcel out of
    the headline — unknown, as it was when the grammar read 1.1 — while the same dotted
    figure on a hall and on a parcel ad is that ad's own area."""
    url = "https://reality.bazos.cz/inzerat/1/x.php"
    house = parse_detail(
        _bazos_page("Prodej rodinného domu 4+1",
                    "Dům, postavený od základů v roce 2005 na rozlehlém pozemku o celkové "
                    "výměře 1.139m², nabízí moderní a především kvalitní prostor."),
        source_url=url, category_main="dum", category_type="prodej")
    assert (house.area_m2, house.area_basis) == (None, None)

    chata = parse_detail(
        _bazos_page("Prodej chaty Děčín - Bynov",
                    "Chata v klidné části Děčína - Bynov. Zahrádka o celkové výměře 1.256 m2 "
                    "je v osobním vlastnictví a je kompletně oplocená."),
        source_url=url, category_main="dum", category_type="prodej")
    assert (chata.area_m2, chata.area_basis) == (None, None)

    hall = parse_detail(
        _bazos_page("Pronájem haly",
                    "Nabízíme světlou, vjezdovou, víceúčelovou halu 2.462 m2 + zázemí a kanceláře."),
        source_url=url, category_main="komercni", category_type="pronajem")
    assert (hall.area_m2, hall.area_basis) == (2462.0, "unknown")

    parcel = parse_detail(
        _bazos_page("Prodej pozemku", "Celková plocha pozemku činí 1.910 m2."),
        source_url=url, category_main="pozemek", category_type="prodej")
    assert (parcel.area_m2, parcel.area_basis) == (1910.0, "plot")


def test_parse_detail_a_room_sized_first_figure_is_not_the_flat():
    """bazos 18453993 (A4, 2026-09-26): a 3+1 whose first m² in the prose is 8 m² — a room
    or a balcony, not the unit. The per-room band (8 m² a room, 24 m² on a 3+1) reads the
    prose arm, which is bazos's whole area, so the figure is absence; a 2 m² cellar the
    same, and the first figure on the band is the flat."""
    url = "https://reality.bazos.cz/inzerat/1/x.php"
    for figure in ("8 m²", "2 m²", "23 m²"):
        listing = parse_detail(
            _bazos_page("Prodej bytu 3+1", f"Byt 3+1 po rekonstrukci, balkon {figure}, sklep."),
            source_url=url, category_main="byt", category_type="prodej")
        assert listing.disposition == "3+1", figure
        assert (listing.area_m2, listing.area_basis) == (None, None), figure

    flat = parse_detail(
        _bazos_page("Prodej bytu 3+1", "Byt 3+1 o výměře 24 m², sklep 2 m²."),
        source_url=url, category_main="byt", category_type="prodej")
    assert (flat.area_m2, flat.area_basis) == (24.0, "unknown")


def _bazos_page_in(section: str, title: str, body: str) -> str:
    """A detail page whose breadcrumb names the deal type, the way live bazos renders it."""
    return ("<html><body>"
            f'<div class="drobky"><a href="https://reality.bazos.cz/{section}/byt/">x</a></div>'
            f'<h1 class="nadpisdetail">{title}</h1>'
            f'<div class="popisdetail">{body}</div>'
            "</body></html>")


def test_parse_detail_a_rental_room_keeps_its_size_and_a_sale_is_still_held_per_room():
    """Production, 2026-09-27: 247 bazos rentals sat under the per-room band (25 active), and
    the hand-read found room rentals — "pronájem pokoje 20m2 ve sdíleném bytě 3+1" (bazos
    18625955, 18850769) — beside defects it cannot tell apart. The band is a SALE's alone,
    and the deal type is the page's own breadcrumb."""
    url = "https://reality.bazos.cz/inzerat/1/x.php"
    room = parse_detail(
        _bazos_page_in("pronajmu", "Pronájem pokoje 20m2 ve sdíleném bytě 3+1",
                       "Pronajmu pokoj 20m2 ve sdíleném bytě 3+1, kuchyň a koupelna společné."),
        source_url=url, category_main="byt", category_type=None)
    assert (room.category_type, room.disposition) == ("pronajem", "3+1")
    assert (room.area_m2, room.area_basis) == (20.0, "unknown")

    small = parse_detail(
        _bazos_page_in("pronajmu", "Pronájem pokoje ve sdíleném bytě 3+1",
                       "Volný pokoj o velikosti 12,5 m² ve sdíleném bytě 3+1."),
        source_url=url, category_main="byt", category_type=None)
    assert (small.category_type, small.disposition) == ("pronajem", "3+1")
    assert (small.area_m2, small.area_basis) == (12.5, "unknown")

    balcony = parse_detail(
        _bazos_page_in("prodam", "Prodej bytu 3+1", "Byt 3+1 po rekonstrukci, balkon 8 m², sklep."),
        source_url=url, category_main="byt", category_type=None)
    assert (balcony.category_type, balcony.disposition) == ("prodej", "3+1")
    assert (balcony.area_m2, balcony.area_basis) == (None, None)

    flat = parse_detail(
        _bazos_page_in("prodam", "Prodej bytu 3+1", "Byt 3+1 o výměře 24 m², sklep 2 m²."),
        source_url=url, category_main="byt", category_type=None)
    assert (flat.category_type, flat.area_m2, flat.area_basis) == ("prodej", 24.0, "unknown")


_FLAT_URL = "https://reality.bazos.cz/inzerat/1/x.php"


def _flat(body: str, title: str = "Prodej bytu 2+kk", category_main: str = "byt") -> ScrapedListing:
    return parse_detail(_bazos_page(title, body), source_url=_FLAT_URL,
                        category_main=category_main, category_type="prodej")


def test_a_labelled_usable_area_wins_over_the_celkova_figure_before_it() -> None:
    """bazos 518256 / 18820141 (portal_columns 3.3, 2026-10-26): "celkové ploše 48m² (užitná
    plocha 40m², ...)" was stored 48, the first figure; 119 of 1,030 rt flats stating both
    labels took the celková one. The usable figure leads, the celková is the total."""
    from scraper.bazos_parser import labelled_areas

    body = ("Byt 2+kk o celkové ploše 48m² (užitná plocha 40m², balkón 6 m², sklep 2m²) "
            "v klidné části obce.")
    assert labelled_areas(body) == (40.0, 48.0)
    listing = _flat(body)
    assert (listing.area_m2, listing.area_basis) == (40.0, "usable")


def test_the_usable_figure_wins_wherever_it_sits_and_over_the_title_figure() -> None:
    after = _flat("Užitná plocha 40 m2, celková plocha 48 m2.", title="Prodej bytu 2+kk 48 m²")
    assert (after.area_m2, after.area_basis) == (40.0, "usable")
    before = _flat("Celková plocha bytu je 48 m2. Užitná plocha 40 m2.")
    assert (before.area_m2, before.area_basis) == (40.0, "usable")


def test_a_celkova_figure_alone_keeps_the_first_figure() -> None:
    """Only a usable label outranks the first figure: a celková figure alone would beat the
    title's unlabelled one, which is often the usable area itself."""
    alone = _flat("Prodám byt, celková plocha 48 m2.")
    assert (alone.area_m2, alone.area_basis) == (48.0, "unknown")
    titled = _flat("Prodám byt, celková plocha 48 m2 včetně sklepa.", title="Prodej bytu 2+kk 40 m²")
    assert (titled.area_m2, titled.area_basis) == (40.0, "unknown")


def test_a_usable_label_alone_is_the_usable_area() -> None:
    listing = _flat("Pěkný byt v centru, sklep 3 m2, užitná plocha 52,5 m2.")
    assert (listing.area_m2, listing.area_basis) == (52.5, "usable")
    for label in ("podlahová plocha", "obytná plocha", "Celková užitná plocha", "UZITNA PLOCHA"):
        listing = _flat(f"Byt 2+kk, {label} 52 m2, celkem s balkonem 58 m2.")
        assert (listing.area_m2, listing.area_basis) == (52.0, "usable"), label


def test_the_labelled_figure_reads_every_unit_spelling() -> None:
    from scraper.bazos_parser import labelled_areas

    for unit in ("m²", "m2", "m 2", "M2", "㎡", "metrů čtverečních", "metru ctverecnich"):
        assert labelled_areas(f"celková plocha 70 {unit}, užitná plocha 61 {unit}") == (61.0, 70.0), unit
    listing = _flat("Byt o celkové ploše 70 metrů čtverečních, užitná plocha 61 metrů čtverečních.")
    assert (listing.area_m2, listing.area_basis) == (61.0, "usable")


def test_a_bare_metr_is_a_distance_not_an_area() -> None:
    from scraper.bazos_parser import labelled_areas

    assert labelled_areas("Obytný soubor 300 metrů od metra") == (None, None)
    assert labelled_areas("užitná plocha 61 metrů, metráž dle katastru") == (None, None)
    listing = _flat("Byt 2+kk 45 m2. Obytný soubor 300 metrů od metra.")
    assert (listing.area_m2, listing.area_basis) == (45.0, "unknown")


def test_an_adjective_without_its_area_noun_is_no_label() -> None:
    """Review of #2053: "obytná kuchyně", "obytný pokoj", "podlahové topení" opened a label on
    the bare stem and took a room's figure as the unit's usable area. The first figure stays."""
    for title, body, first in (
        ("Prodej bytu 3+kk", "Byt 3+kk o výměře 75 m2, obytná kuchyně 30 m2.", 75.0),
        ("Prodej bytu 2+kk", "Byt 2+kk 50 m2, obytný pokoj 18 m2.", 50.0),
        ("Prodej bytu 2+1", "Byt 2+1, 58 m2, podlahové topení v koupelně 6 m2.", 58.0),
        ("Prodej bytu 3+kk", "Byt 3+kk 80 m2, obytné podkroví 25 m2.", 80.0),
    ):
        listing = _flat(body, title=title)
        assert (listing.area_m2, listing.area_basis) == (first, "unknown"), body
    house = parse_detail(
        _bazos_page("Prodej rodinného domu", "Rodinný dům 140 m2, obytná plocha přízemí 70 m2."),
        source_url=_FLAT_URL, category_main="dum", category_type="prodej")
    assert (house.area_m2, house.area_basis) == (140.0, "unknown")


def test_celkova_without_an_area_noun_is_no_total() -> None:
    from scraper.bazos_parser import labelled_areas

    body = "Po celkové rekonstrukci, byt 2+1 o výměře 55 m2. Užitná plocha 52 m2."
    assert labelled_areas(body) == (52.0, None)
    assert labelled_areas("Celkový stav dobrý, 60 m2, celkově 3 pokoje 12 m2") == (None, None)


def test_a_label_after_its_figure_does_not_reach_the_next_one() -> None:
    """Review of #2053: "72 m2 užitné plochy, kuchyň 12 m2" read the kitchen's 12 as usable.
    A label a figure ends right before is skipped, and a comma ends a label's reach."""
    listing = _flat("Byt 3+1 o 72 m2 užitné plochy, kuchyň 12 m2, ložnice 15 m2.",
                    title="Prodej bytu 3+1")
    assert (listing.area_m2, listing.area_basis) == (72.0, "unknown")
    from scraper.bazos_parser import labelled_areas

    assert labelled_areas("Užitná plocha bytu, kuchyň 12 m2") == (None, None)
    assert labelled_areas("Užitná plocha 52,5 m2") == (52.5, None)


def test_a_labelled_usable_figure_is_held_to_the_prose_band() -> None:
    """A free-text label is weaker than a structured field: a house's labelled figure of
    1,000 m² or more is the parcel, and a sale's under 8 m² a room is a part of the unit."""
    house = parse_detail(
        _bazos_page("Prodej rodinného domu 4+1", "Dům 4+1, užitná plocha 2.500 m2."),
        source_url=_FLAT_URL, category_main="dum", category_type="prodej")
    assert (house.area_m2, house.area_basis) == (None, None)
    small = _flat("Byt 3+1 o výměře 70 m2, užitná plocha 12 m2.", title="Prodej bytu 3+1")
    assert (small.area_m2, small.area_basis) == (70.0, "unknown")


def test_the_accessory_veto_reads_whole_words() -> None:
    from scraper.bazos_parser import labelled_areas

    assert labelled_areas("Viz půdorys užitná plocha 61 m2") == (61.0, None)
    assert labelled_areas("Dvorní trakt užitná plocha 61 m2") == (61.0, None)
    assert labelled_areas("Vstup z půdy užitná plocha 9 m2") == (None, None)


def test_a_label_of_an_accessory_or_the_parcel_is_not_the_units() -> None:
    """A label is the unit's only when its clause names no accessory and no parcel: the
    parcel stays out of a house's headline (bazos 186747), a cellar's "užitná plocha" is
    not the flat's, and "celková podlahová" (accessories in) reads as the total."""
    from scraper.bazos_parser import labelled_areas

    assert labelled_areas("Dům na pozemku o celkové výměře 1.139m², užitná plocha domu 150 m2") \
        == (150.0, None)
    assert labelled_areas("užitná plocha sklepa 5 m2, celková plocha teras 30 m2") == (None, None)
    assert labelled_areas("Celková podlahová plocha 48 m², užitná 40 m2") == (40.0, 48.0)
    assert labelled_areas("Byt 3+1 72 m2 užitné plochy") == (None, None)
    assert labelled_areas("Užitná plocha. Byt je po rekonstrukci, 72 m2") == (None, None)


def test_land_and_unlabelled_house_texts_are_unchanged() -> None:
    """The land arm reads the first figure as the parcel, as before; a house with no usable
    label keeps its first-figure rule, the dotted parcel still declined."""
    parcel = parse_detail(
        _bazos_page("Prodej pozemku",
                    "Celková plocha pozemku činí 1.910 m2, z toho užitná plocha zahrady 800 m2."),
        source_url=_FLAT_URL, category_main="pozemek", category_type="prodej")
    assert (parcel.area_m2, parcel.area_basis) == (1910.0, "plot")

    house = parse_detail(
        _bazos_page("Prodej rodinného domu 4+1",
                    "Dům na rozlehlém pozemku o celkové výměře 1.139m², nabízí kvalitní prostor."),
        source_url=_FLAT_URL, category_main="dum", category_type="prodej")
    assert (house.area_m2, house.area_basis) == (None, None)

    labelled_house = parse_detail(
        _bazos_page("Prodej rodinného domu 4+1",
                    "Pozemek o celkové výměře 1.139m², užitná plocha domu 150 m2."),
        source_url=_FLAT_URL, category_main="dum", category_type="prodej")
    assert (labelled_house.area_m2, labelled_house.area_basis) == (150.0, "usable")


def test_the_replay_cases_of_the_review() -> None:
    """Read on a replay of 2,000 stored bazos texts (review of #2053): the agency export puts
    its figures back to back, an obytná figure can leave the kitchen out, and an "including"
    or "a further" figure is not the unit's own."""
    from scraper.bazos_parser import labelled_areas

    export = "Plocha pozemku: 271 m2 Zastavěná plocha: 69 m2 Užitná plocha: 163 m2 Obytná plocha: 111 m2"
    assert labelled_areas(export) == (163.0, None)
    assert labelled_areas("Pozemek: 2 532 m² Užitná plocha cca 250 m² Více informací") == (250.0, None)
    assert labelled_areas("velikost bytu 3+1, obytná plocha 57 m2, užitná plocha 72 m2") == (72.0, None)
    assert labelled_areas("Obytná plocha 45 m2, zahrada 385 m2") == (45.0, None)
    for text in ("nabízí celkovou užitnou plochu 46 m² včetně terasy",
                 "užitná plocha vč. příslušenství 123,6 m2",
                 "užitná plocha domu vzroste o dalších cca 70 m2",
                 "z toho 95,3 m² činí podlahová plocha a 104,7 m² představuje terasa"):
        assert labelled_areas(text)[0] is None, text
