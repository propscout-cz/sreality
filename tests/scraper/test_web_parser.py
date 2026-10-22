"""scraper.web_parser: a page we have never seen before, read deterministically.

Four small pages, each the shape a pasted site really takes: a JSON-LD-rich agency page, an
OpenGraph-only page, a bare Czech page whose facts are only in its words, and a page whose
only contact is a +420 number and a mailto link.
"""

from __future__ import annotations

import json

import pytest

from scraper.portal_identity import WEB
from scraper.web_parser import parse_detail
from toolkit.filter_registry import COLUMN_CANONICAL_VALUES

JSONLD_PAGE = """<!doctype html><html lang="cs"><head>
<title>Byt 3+1 na prodej | RK Novák</title>
<meta property="og:title" content="Prodej bytu 3+1, 78 m², Brno – OG">
<meta property="og:image" content="https://cdn.rk-novak.example/og.jpg">
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
  {"@type": "Organization", "name": "RK Novák", "logo": "https://rk-novak.example/logo.png"},
  {"@type": "RealEstateListing", "name": "Prodej bytu 3+1, 78 m², Brno-Žabovřesky",
   "description": "Nabízíme <b>světlý</b> byt 3+1 ve 4. patře cihlového domu v Brně.\\nByt má balkon a sklep.",
   "datePosted": "2026-10-01T08:00:00Z",
   "image": ["https://cdn.rk-novak.example/1.jpg", {"@type": "ImageObject", "contentUrl": "/img/2.jpg"}],
   "floorSize": {"@type": "QuantitativeValue", "value": 78, "unitCode": "MTK"},
   "contentLocation": {"@type": "Place", "geo": {"@type": "GeoCoordinates", "latitude": 49.2112, "longitude": 16.5794}},
   "offers": {"@type": "Offer", "price": "6490000", "priceCurrency": "CZK",
              "seller": {"@type": "Person", "name": "Jana Nováková", "email": "Jana.Novakova@RK-Novak.example",
                         "telephone": "+420 602 123 456", "worksFor": {"@type": "Organization", "name": "RK Novák s.r.o."}}}}
]}</script>
</head><body>
<nav><a href="/">Domů</a><a href="mailto:info@rk-novak.example">info</a></nav>
<main><h1>Prodej bytu 3+1</h1>
<div class="gallery"><img src="/img/2.jpg" width="800"><img data-src="/img/3.jpg"><img src="/img/icon-share.png">
<img src="/img/tiny.jpg" width="40" height="40"></div>
<p>Nabízíme světlý byt 3+1 ve 4. patře cihlového domu v Brně, 10 minut od centra.</p></main>
<footer>© RK Novák, tel. 541 000 000</footer></body></html>"""

OG_PAGE = """<html><head>
<meta property="og:title" content="Pronájem bytu 2+kk Praha 7 - Holešovice">
<meta property="og:description" content="Pronajmeme zařízený byt 2+kk o výměře 54 m² v Holešovicích, nájem 21 500 Kč/měsíc + poplatky.">
<meta property="og:image" content="https://img.pronajmy.example/a/1.webp">
<meta property="og:site_name" content="Pronájmy.example">
</head><body><div id="app"></div></body></html>"""

CZECH_PAGE = """<html><head><title>Prodej bytu 2+kk 54 m², Praha 6</title></head><body>
<header><a href="/">Reality XY</a><p>Nejlepší realitka v Praze, volejte každý den od rána do večera.</p></header>
<div class="content">
<h1>Prodej bytu 2+kk 54 m², Praha 6</h1>
<p class="price">Cena: 5 990 000 Kč (110 926 Kč/m²)</p>
<p>Prodáváme byt 2+kk v Praze 6 - Dejvicích ve 2. patře panelového domu, dům je 6podlažní.</p>
<p>Byt je po rekonstrukci, k bytu náleží sklep. Výborná dostupnost MHD.</p>
</div>
<aside><p>Podobné nabídky: Prodej bytu 3+1, 7 990 000 Kč, Praha 5 — a mnoho dalších nabídek každý den.</p></aside>
<footer><p>Copyright Reality XY 2026, všechna práva vyhrazena, kontaktujte nás kdykoli.</p></footer>
</body></html>"""

CONTACT_PAGE = """<html><head><title>Rodinný dům se zahradou</title>
<meta name="description" content="Rodinný dům 5+1 se zahradou 800 m² v obci Líbeznice, ihned k nastěhování.">
</head><body><article>
<p>Prodám rodinný dům 5+1 se zahradou o výměře 800 m². Dům je ve výborném stavu, nová střecha.</p>
<p>Prohlídky po domluvě na telefonu +420 777 12 34 56 nebo e-mailem.</p>
<a href="mailto:Pavel.Dvorak@Seznam.cz?subject=dum">Napište mi</a>
</article></body></html>"""

URL = "https://www.rk-novak.example/nabidka/byt-brno/?utm_source=fb#foto"


def _parse(html: str, url: str = URL, final_url: str | None = None):  # noqa: ANN202
    return parse_detail(html, source_url=url, final_url=final_url)


def test_a_jsonld_page_gives_its_structured_facts() -> None:
    got = _parse(JSONLD_PAGE)
    assert (got.source, got.source_url) == (WEB, "https://www.rk-novak.example/nabidka/byt-brno")
    assert got.source_id_native == got.source_url
    assert got.raw["title"] == "Prodej bytu 3+1, 78 m², Brno-Žabovřesky"
    assert (got.category_type, got.category_main) == ("prodej", "byt")
    assert (got.price_czk, got.price_unit) == (6_490_000, "za nemovitost")
    assert (got.area_m2, got.area_basis) == (78.0, "floor")
    assert got.disposition == "3+1" and got.floor == 4
    assert (got.lat, got.lon) == (49.2112, 16.5794)
    assert got.description.startswith("Nabízíme světlý byt 3+1 ve 4. patře")
    assert "<b>" not in got.description
    assert got.published_at is not None and got.published_at.year == 2026
    assert got.raw["broker"] == {
        "id": "jana.novakova@rk-novak.example", "name": "Jana Nováková",
        "email": "jana.novakova@rk-novak.example", "phone": "+420602123456",
        "agency": "RK Novák s.r.o.",
    }
    assert got.raw["jsonld"]["lat"] == 49.2112 and got.raw["jsonld"]["price_czk"] == 6_490_000
    # The JSON-LD lists the gallery, so the page's own <img> (3.jpg) is not read.
    assert got.raw["image_urls"] == [
        "https://cdn.rk-novak.example/1.jpg", "https://www.rk-novak.example/img/2.jpg",
        "https://cdn.rk-novak.example/og.jpg",
    ]


def test_an_og_only_page_reads_its_meta_and_the_rent_grammar() -> None:
    got = _parse(OG_PAGE, "https://pronajmy.example/byt/77")
    assert got.raw["title"] == "Pronájem bytu 2+kk Praha 7 - Holešovice"
    assert (got.category_type, got.category_main, got.disposition) == ("pronajem", "byt", "2+kk")
    assert (got.price_czk, got.price_unit) == (21_500, "za mesic")
    assert got.area_m2 == 54.0
    assert got.raw["image_urls"] == ["https://img.pronajmy.example/a/1.webp"]
    assert got.raw["og"]["site_name"] == "Pronájmy.example"
    assert "jsonld" not in got.raw and "broker" not in got.raw
    assert (got.lat, got.lon) == (None, None)


def test_a_bare_czech_page_is_read_by_the_shared_grammars_and_never_its_chrome() -> None:
    got = _parse(CZECH_PAGE, "https://reality-xy.example/inzerat/123")
    assert (got.category_type, got.category_main) == ("prodej", "byt")
    assert (got.price_czk, got.disposition, got.area_m2) == (5_990_000, "2+kk", 54.0)
    assert (got.floor, got.total_floors) == (2, 6)
    assert "Prodáváme byt 2+kk" in got.description
    for chrome in ("Nejlepší realitka", "Podobné nabídky", "Copyright"):
        assert chrome not in got.description
    assert got.raw["price_czk"] == 5_990_000 and got.raw["area_m2"] == 54.0


def test_a_per_m2_figure_alone_is_no_price() -> None:
    page = "<html><head><title>Pronájem kanceláře 120 m², 250 Kč/m²/měsíc</title></head><body></body></html>"
    got = _parse(page, "https://kancelare.example/1")
    assert (got.category_type, got.category_main) == ("pronajem", "komercni")
    assert got.price_czk is None and got.price_unit is None


def test_a_phone_and_a_mailto_make_the_broker() -> None:
    got = _parse(CONTACT_PAGE, "https://dum.example/inzerat?id=9")
    assert got.source_url == "https://dum.example/inzerat?id=9"
    assert (got.category_type, got.category_main) == (None, "dum")
    assert got.raw["broker"] == {
        "id": "pavel.dvorak@seznam.cz", "name": None, "email": "pavel.dvorak@seznam.cz",
        "phone": "+420777123456", "agency": None,
    }
    # The ad's own text beats the meta description (a small site's is often one tagline).
    assert got.description.startswith("Prodám rodinný dům 5+1 se zahradou")


def test_without_an_email_the_phone_is_the_broker_id_and_without_both_there_is_no_block() -> None:
    phone_only = CONTACT_PAGE.replace('<a href="mailto:Pavel.Dvorak@Seznam.cz?subject=dum">Napište mi</a>', "")
    assert _parse(phone_only).raw["broker"]["id"] == "+420777123456"
    nothing = phone_only.replace("+420 777 12 34 56", "")
    assert "broker" not in _parse(nothing).raw


def test_a_page_that_does_not_say_leaves_the_categories_null() -> None:
    got = _parse("<html><head><title>Nabídka č. 42</title></head><body><p>x</p></body></html>",
                 "https://x.example/42")
    assert (got.category_type, got.category_main, got.price_czk, got.area_m2) == (None, None, None, None)
    assert got.description is None and got.raw["image_urls"] == []


def test_every_category_it_emits_is_canonical() -> None:
    titles = ["Prodej rodinného domu", "Pronájem garáže", "Prodej pozemku 900 m²",
              "Prodej činžovního domu", "Pronájem bytu v rodinném domě", "Prodej chaty",
              "Pronájem obchodního prostoru", "Prodej bytové jednotky 2+1"]
    seen = {(_parse(f"<html><head><title>{t}</title></head></html>").category_main) for t in titles}
    assert seen <= set(COLUMN_CANONICAL_VALUES["category_main"])
    assert seen == {"dum", "ostatni", "pozemek", "komercni", "byt"}


def test_a_foreign_currency_offer_is_no_price_and_geo_is_never_computed() -> None:
    page = JSONLD_PAGE.replace('"priceCurrency": "CZK"', '"priceCurrency": "EUR"').replace(
        '"geo": {"@type": "GeoCoordinates", "latitude": 49.2112, "longitude": 16.5794}', '"name": "Brno"')
    got = _parse(page)
    assert got.price_czk is None
    assert (got.lat, got.lon) == (None, None) and "lat" not in got.raw["jsonld"]


def test_the_parse_is_deterministic_and_json_safe() -> None:
    a, b = _parse(JSONLD_PAGE), _parse(JSONLD_PAGE)
    assert a.hash_doc() == b.hash_doc() and a.raw == b.raw
    json.dumps(a.raw)


def test_the_raw_keys_the_field_census_reads_sit_at_the_top_level() -> None:
    raw = _parse(JSONLD_PAGE).raw
    for key in ("price_czk", "area_m2", "disposition", "category_main", "category_type", "title",
                "description", "image_urls", "broker", "jsonld", "og"):
        assert key in raw, key


def test_coordinates_from_a_place_alone_still_ride_the_jsonld_block() -> None:
    page = ("<html><head><title>Prodej domu</title><script type='application/ld+json'>"
            '{"@type": "Place", "geo": {"latitude": "50.08", "longitude": "14.42"}}'
            "</script></head><body></body></html>")
    got = _parse(page, "https://dum.example/1")
    assert (got.lat, got.lon) == (50.08, 14.42) and got.raw["jsonld"] == {"lat": 50.08, "lon": 14.42}
    assert got.raw["title"] == "Prodej domu"  # a Place's name is never the ad's title


def test_a_posting_date_is_a_date_or_an_aware_timestamp() -> None:
    from datetime import date, timezone

    def page(stamp: str) -> str:
        return ("<html><head><script type='application/ld+json'>"
                f'{{"@type": "RealEstateListing", "name": "Prodej bytu", "datePosted": "{stamp}"}}'
                "</script></head></html>")

    assert _parse(page("2026-10-01")).published_at == date(2026, 10, 1)
    assert _parse(page("2026-10-01T08:00:00")).published_at.tzinfo == timezone.utc
    assert _parse(page("not a date")).published_at is None


# --- pages that used to mislead the parse ---------------------------------------------------------


def _related_page(cards: str, jsonld: bool) -> str:
    ld = ("""<script type="application/ld+json">{"@type": "RealEstateListing",
        "name": "Prodej bytu 2+kk, Brno", "image": ["/foto/123_1.jpg", "/foto/123_2.jpg"]}</script>"""
          if jsonld else "")
    return f"""<html><head><title>Prodej bytu 2+kk, Brno</title>{ld}</head><body>
<main><h1>Prodej bytu 2+kk, Brno</h1>
<div class="galerie"><img src="/foto/123_1.jpg" width="800"><img src="/foto/123_2.jpg" width="800"></div>
<div class="popis"><p>Nabízíme byt 2+kk v Brně - Králově Poli, 54 m², ve 3. patře cihlového domu.</p>
<p>Cena 4 990 000 Kč včetně provize. Byt je po rekonstrukci, k bytu patří sklep.</p></div>
<section class="podobne-nabidky"><h2>Podobné nabídky</h2>{cards}</section>
</main></body></html>"""


def _card(n: str, text: str) -> str:
    return f'<div class="card"><img src="/foto/{n}_1.jpg" width="400"><p>{text}</p></div>'


CARDS_A = (_card("987", "Byt 3+1, Olomouc, 6 500 000 Kč, cihla, 2. patro, balkon a sklep, ihned volný.")
           + _card("555", "Byt 1+kk, Ostrava, 2 100 000 Kč, panel, 5. patro, výtah, nízké poplatky."))
CARDS_B = _card("777", "Rodinný dům 5+1, Zlín, 9 900 000 Kč, zahrada 900 m², garáž, sklep a dílna.")


def test_another_ads_cards_inside_main_never_reach_the_ad() -> None:
    """A "Podobné nabídky" section inside <main>: its text never joins the description and
    its photos never join the gallery, so a refetch with other cards mints no snapshot."""
    for jsonld in (True, False):
        a = _parse(_related_page(CARDS_A, jsonld), "https://rk.example/byt/123")
        b = _parse(_related_page(CARDS_B, jsonld), "https://rk.example/byt/123")
        assert a.description == b.description and a.hash_doc() == b.hash_doc()
        assert "Olomouc" not in (a.description or "") and a.price_czk == 4_990_000
        assert a.raw["image_urls"] == b.raw["image_urls"] == [
            "https://rk.example/foto/123_1.jpg", "https://rk.example/foto/123_2.jpg"]


def test_unmarked_cards_never_win_the_description() -> None:
    page = _related_page(CARDS_A, False).replace(' class="podobne-nabidky"', "").replace(
        "<h2>Podobné nabídky</h2>", "")
    got = _parse(page, "https://rk.example/byt/123")
    assert got.description.startswith("Nabízíme byt 2+kk") and "Olomouc" not in got.description


@pytest.mark.parametrize("price", ["Cena 4 500,- Kč/m²", "Cena 4 500,-/m²", "Cena 4 500 Kč/m²"])
def test_a_per_m2_figure_with_the_czech_dash_is_no_price(price: str) -> None:
    page = (f"<html><head><title>Prodej pozemku 900 m², Říčany</title></head><body><article>"
            f"<p>Prodej stavebního pozemku o výměře 900 m² v Říčanech. {price}.</p></article></body></html>")
    assert _parse(page, "https://pozemky.example/1").price_czk is None


def test_a_deposit_before_the_rent_is_not_the_rent() -> None:
    page = ("<html><head><title>Pronájem bytu 2+kk, Olomouc</title></head><body><article>"
            "<p>Pronájem bytu 2+kk v Olomouci. Kauce 30 000 Kč. Nájemné 18 500 Kč měsíčně plus "
            "poplatky 3 500 Kč.</p></article></body></html>")
    got = _parse(page, "https://najmy.example/1")
    assert (got.price_czk, got.price_unit) == (18_500, "za mesic")


def test_a_logo_h1_in_the_site_header_is_no_title() -> None:
    page = ("<html><head><title>Prodej bytu 2+kk, Brno</title></head><body>"
            "<header><h1>Reality Novák</h1></header><div class='obsah'>"
            "<p>Nabízíme světlý byt 2+kk v Brně, po rekonstrukci, s balkonem a sklepem.</p></div>"
            "</body></html>")
    got = _parse(page, "https://rk-novak.example/byt/1")
    assert got.raw["title"] == "Prodej bytu 2+kk, Brno"
    assert (got.category_type, got.category_main, got.disposition) == ("prodej", "byt", "2+kk")


def test_the_categories_come_from_the_first_title_that_says() -> None:
    page = ("""<html><head><meta property="og:title" content="Prodej bytu 3+1 v Brně">"""
            """<script type="application/ld+json">{"@type": "RealEstateListing", "name": "Byt 3+1, 78 m²"}"""
            "</script></head><body></body></html>")
    got = _parse(page, "https://rk.example/1")
    assert got.raw["title"] == "Byt 3+1, 78 m²"
    assert (got.category_type, got.category_main) == ("prodej", "byt")


def test_a_site_wide_meta_description_never_beats_the_ads_text() -> None:
    page = ("<html><head><title>Prodej bytu 2+kk, Brno</title><meta name='description' content="
            "'Realitní kancelář Novák - prodej a pronájem nemovitostí v Praze a okolí, 20 let praxe.'>"
            "</head><body><div class='obsah'><p>Prodáváme byt 2+kk v Brně, 54 m², za 4 990 000 Kč, "
            "ve 2. patře.</p></div></body></html>")
    got = _parse(page, "https://rk-novak.example/byt/2")
    assert got.description.startswith("Prodáváme byt 2+kk v Brně")
    assert (got.price_czk, got.area_m2, got.disposition) == (4_990_000, 54.0, "2+kk")


def test_a_webforms_page_wrapped_in_one_form_is_read() -> None:
    page = ("<html><head><title>Prodej bytu 3+1, Plzeň</title></head><body><form id='aspnetForm'>"
            "<div class='detail'><div class='foto'><img src='/img/a.jpg' width='800'></div>"
            "<div class='text'><p>Prodej bytu 3+1 o výměře 72 m² ve 4. patře v Plzni, cena 3 900 000 Kč.</p>"
            "</div></div><input type='submit'></form></body></html>")
    got = _parse(page, "https://rk-plzen.example/detail.aspx?id=5")
    assert got.description.startswith("Prodej bytu 3+1 o výměře 72 m²")
    assert (got.price_czk, got.area_m2, got.floor) == (3_900_000, 72.0, 4)
    assert got.raw["image_urls"] == ["https://rk-plzen.example/img/a.jpg"]


def test_an_articles_own_header_is_the_ads() -> None:
    page = ("<html><head><title>Byt</title></head><body><article><header><h1>Prodej bytu 2+kk</h1>"
            "<p>Cena 5 200 000 Kč, Praha 4 - Nusle, k nastěhování ihned po dohodě.</p></header>"
            "<p>Byt 2+kk o výměře 48 m² v cihlovém domě.</p></article></body></html>")
    got = _parse(page, "https://rk.example/2")
    assert got.raw["title"] == "Prodej bytu 2+kk" and got.price_czk == 5_200_000


def test_jsonld_with_a_raw_newline_in_a_string_is_still_read() -> None:
    page = ('<html><head><script type="application/ld+json">{"@type": "RealEstateListing", '
            '"name": "Prodej bytu 2+kk", "description": "Byt 2+kk v Brně.\nPo rekonstrukci, '
            's balkonem a sklepem.", "offers": {"@type": "Offer", "price": 4990000, "priceCurrency": "CZK"}}'
            "</script></head><body></body></html>")
    assert "\n" in page  # a literal newline inside the JSON string
    got = _parse(page, "https://rk.example/3")
    assert (got.raw["title"], got.price_czk, got.category_type) == ("Prodej bytu 2+kk", 4_990_000, "prodej")
    assert got.description.startswith("Byt 2+kk v Brně.")


def test_a_listing_under_a_webpages_main_entity_is_found() -> None:
    page = ('<html><head><script type="application/ld+json">{"@type": "WebPage", "name": "RK", '
            '"mainEntity": {"@type": "RealEstateListing", "name": "Prodej domu 5+1", '
            '"geo": {"@type": "GeoCoordinates", "latitude": 49.5, "longitude": 17.1}, '
            '"offers": {"@type": "Offer", "price": "8900000", "priceCurrency": "CZK"}}}'
            "</script></head><body></body></html>")
    got = _parse(page, "https://rk.example/4")
    assert (got.raw["title"], got.price_czk, got.lat, got.lon) == ("Prodej domu 5+1", 8_900_000, 49.5, 17.1)
