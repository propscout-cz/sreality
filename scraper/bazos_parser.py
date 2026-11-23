"""Deterministic HTML parsing for reality.bazos.cz (multi-portal slice 3b).

Pure functions, no I/O of their own: `parse_index` turns one search-results
page into the listing ids + the next offset, and `parse_detail` turns one
listing page into a `ScrapedListing` (the shared multi-portal contract in
`scraper.scraped_listing`). Bazos is a free-form classifieds site — no JSON
API, attributes buried in free text — so disposition and area come out by
regex over the title + description.

The ad's own CZ-guarded maps-link pin is the ONLY coordinate bazos publishes
and therefore the only one we take (`_resolve_coords`). It is frequently a
town-centre approximation, and that is what the claim's precision cap says; the
text geocode that used to beat it is gone with the geocoder (W4-b).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from selectolax.parser import HTMLParser, Node

from scraper import vocabulary
from scraper.area import (
    AREA_NUMBER_SRC,
    LAND_CATEGORIES,
    PortalAreas,
    area_token_to_float,
    derive_headline_area,
    parse_area_text,
)
from scraper.floor import floor_from_text
from scraper.portal_identity import native_id
from scraper.price_text import is_per_area_price
from scraper.published import bazos_posted_date
from scraper.scraped_listing import ScrapedListing

# Bazos URL segments -> our canonical labels (mirrors parser.CATEGORY_* style).
SALE_TYPE: dict[str, str] = {
    "prodam": "prodej",
    "pronajmu": "pronajem",
}
CATEGORY_MAIN: dict[str, str] = {
    "byt": "byt",
    "dum": "dum",
    "chata": "dum",          # bazoš "Chaty, Chalupy" section
    "pozemky": "pozemek",
    "pozemek": "pozemek",
    "zahrada": "pozemek",
    "nebytove": "komercni",
    "komercni": "komercni",
    "kancelar": "komercni",
    "prostory": "komercni",  # "Obchodní prostory"
    "sklad": "komercni",
    "restaurace": "komercni",
    "garaz": "ostatni",
    "ostatni": "ostatni",
}

# Portal-agnostic subtype (migration 152), keyed on the bazoš section slug that
# appears in the detail breadcrumb / index URL. Every section that shares a
# category_main with a sibling section is listed, because the presence nomination
# is subtype-scoped — an unlisted sibling would nominate the other's rows on every
# walk. Each collapsed group's GENERIC member stays absent (subtype NULL): "dum"
# (bazoš doesn't split rodinný/vila), "pozemek" (plain land) and "ostatni".
SUBTYPE: dict[str, str] = {
    "chata": "chata",              # bazoš lumps chaty+chalupy; chata is the slug
    "restaurace": "restaurace",
    "kancelar": "kancelar",
    "prostory": "obchodni_prostor",
    "sklad": "sklad",
    "zahrada": "zahrada",          # collapses onto pozemek
    "garaz": "garaz",              # collapses onto ostatni
}

_PSC_RE = re.compile(r"\b(\d{3})\s?(\d{2})\b")
_MAP_LABEL_RE = re.compile(r"(?i)\bzobrazit na map[ěe]\b")
_COORD_RE = re.compile(r"(-?\d{1,3}\.\d{3,}),\s*(-?\d{1,3}\.\d{3,})")
# bazos serves /img/N/ (full ~1200px) and /img/Nt/ (thumbnail ~340px) for the same
# photo; a detail page shows the full cover plus the whole thumbnail strip.
_BAZOS_THUMB_RE = re.compile(r"/img/(\d+)t/")


def _full_size_image_url(src: str) -> str:
    return _BAZOS_THUMB_RE.sub(r"/img/\1/", src, count=1)

# Czech-bbox guard applied to the link pin so a stray decimal pair or a swapped
# lat/lon can never become a bogus geom.
_CZ_LAT_MIN, _CZ_LAT_MAX = 48.0, 51.5
_CZ_LON_MIN, _CZ_LON_MAX = 12.0, 19.0


def _in_cz_bbox(lat: float, lon: float) -> bool:
    return _CZ_LAT_MIN <= lat <= _CZ_LAT_MAX and _CZ_LON_MIN <= lon <= _CZ_LON_MAX
_PRICE_DIGITS_RE = re.compile(r"\d[\d\s ]{3,}")


@dataclass(frozen=True)
class IndexItem:
    source_id_native: str
    detail_path: str
    title: str | None = None
    price_text: str | None = None
    locality_text: str | None = None
    posted_date: str | None = None
    views: str | None = None


@dataclass(frozen=True)
class IndexPage:
    total: int | None
    items: list[IndexItem] = field(default_factory=list)
    next_offset: int | None = None


def _text(node: Node | None) -> str | None:
    if node is None:
        return None
    txt = re.sub(r"\s+", " ", node.text(separator=" ", strip=False)).strip()
    return txt or None


def _page_text(tree: HTMLParser) -> str:
    body = tree.body
    if body is not None:
        return body.text(separator=" ", strip=False)
    root = tree.root
    return root.text(separator=" ", strip=False) if root is not None else ""


def _parse_total(text: str) -> int | None:
    # "Zobrazeno 1-20 inzerátů z 6 990"
    m = re.search(r"inzer\w+\s+z\s+(\d[\d\s]*\d|\d)", text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


def _parse_price(text: str | None, category_type: str | None) -> tuple[int | None, str | None]:
    unit = "za mesic" if category_type == "pronajem" else "za nemovitost"
    if not text:
        return None, unit
    if "dohodou" in text.lower():
        return None, unit
    m = _PRICE_DIGITS_RE.search(text)
    if not m:
        return None, unit
    # A per-m² figure must NEVER be stored as the absolute price — it reads as a
    # total in every downstream consumer (Kč/m² stats, comparables, Browse sort).
    if is_per_area_price(text[m.end():]):
        return None, unit
    digits = re.sub(r"\D", "", m.group(0))
    return (int(digits) if digits else None), unit


def _parse_coords(href: str | None) -> tuple[float | None, float | None]:
    if not href:
        return None, None
    m = _COORD_RE.search(href)
    if not m:
        return None, None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not _in_cz_bbox(lat, lon):
        return None, None
    return lat, lon


# The explicit "Lokalita: <place>[, <quarter>]" trailer some ads append to
# the description (W0 item 0i). The colon distinguishes it from prose ("v
# klidné lokalitě"). IMPORTANT: parse_detail feeds NEWLINE-COLLAPSED text
# (_text() folds all whitespace to single spaces), so a line-end anchor is
# dead here — the value is instead truncated at the first following label
# token ("Kontakt:", "Cena:", "Tel:", ...) and gated word-by-word
# (review-confirmed critical on this item's first cut: the loose capture
# swallowed the rest of the description).
_LOKALITA_TRAILER_RE = re.compile(
    r"(?i)\blokalita:[^\S\r\n]*(?:ul\.[^\S\r\n]*)?"
    r"([^,\r\n]{2,60}?)"
    r"(?:,[^\S\r\n]*([^,\r\n]{2,60}?))?"
    r"(?=,|[\r\n]|$|\s\S{1,15}:)"
)

# Words that mark the END of a trailer value when ads run several labelled
# lines together ("Lokalita: Bezručova Kontakt: 777..." after collapse).
_TRAILER_STOPWORDS: frozenset[str] = frozenset({
    "kontakt", "cena", "tel", "telefon", "email", "e-mail", "volejte",
    "pište", "piste", "info", "vice", "více", "web", "www", "mobil",
})


def _trailer_value_words(raw: str) -> list[str]:
    """The leading words of a trailer segment up to the first label token —
    a word carrying ':'/'@' or a known trailer stopword ends the value."""
    out: list[str] = []
    for w in raw.split():
        if ":" in w or "@" in w or w.lower().strip(".,:") in _TRAILER_STOPWORDS:
            break
        out.append(w)
    return out


def _trailer_quarter(description: str | None, locality: str | None) -> str | None:
    """The quarter from a description's 'Lokalita: <place>, <quarter>' trailer, or None:
    label-truncated, at most 3 words, capitalised, and never the row's own town. The
    street half is gone (2026-09-30): the text lane reads the street now."""
    m = _LOKALITA_TRAILER_RE.search(description or "")
    quarter_words = _trailer_value_words(((m and m.group(2)) or "").strip(" ."))
    quarter = " ".join(quarter_words).strip(" .") or None
    if quarter is not None and (
        len(quarter_words) > 3
        or not quarter[0].isupper()
        or (locality and quarter.lower() == locality.lower())
    ):
        quarter = None
    return quarter


def _resolve_coords(
    *,
    link_lat: float | None,
    link_lon: float | None,
) -> tuple[float | None, float | None, dict[str, Any]]:
    """The ad's own maps-link pin, CZ-guarded, or nothing.

    The provenance dict is stored in raw_json, and `source` is what the claim
    intake's licence ladder reads: only `"link"` licenses a bazos coordinate.
    """
    present = link_lat is not None and link_lon is not None
    prov: dict[str, Any] = {
        "source": "link" if present else None,
        "link_present": present,
    }
    return (link_lat, link_lon, prov) if present else (None, None, prov)


def parse_index(html: str) -> IndexPage:
    tree = HTMLParser(html)
    total = _parse_total(_page_text(tree))

    items: list[IndexItem] = []
    for block in tree.css("div.inzeraty.inzeratyflex"):
        link = block.css_first('a[href*="/inzerat/"]')
        href = link.attributes.get("href") if link else None
        source_id = native_id("bazos", href)
        if not source_id or not href:
            continue
        items.append(
            IndexItem(
                source_id_native=source_id,
                detail_path=href,
                title=_text(block.css_first("h2.nadpis")) or _text(link),
                price_text=_text(block.css_first("div.inzeratycena")),
                locality_text=_text(block.css_first("div.inzeratylok")),
                posted_date=_text(block.css_first("span.velikost10")),
                views=_text(block.css_first("div.inzeratyview")),
            )
        )

    return IndexPage(total=total, items=items, next_offset=_next_offset(tree))


def _next_offset(tree: HTMLParser) -> int | None:
    pager = tree.css_first("div.strankovani")
    if pager is None:
        return None
    for link in pager.css("a"):
        if (link.text(strip=True) or "").startswith("Další"):
            # The offset is a path segment; a locality-filtered URL appends a
            # query (?hlokalita=…&humkreis=…), so allow a trailing ?/# after it
            # — anchoring on $ alone misses every page past the first when a
            # filter is active.
            m = re.search(r"/(\d+)/?(?:[?#]|$)", link.attributes.get("href") or "")
            if m:
                return int(m.group(1))
    return None


def _locality(cell_text: str | None) -> tuple[str | None, str | None]:
    """Return (locality, psc) from the Lokalita cell text.

    bazos renders the cell two ways: "Town PSČ" (older fixtures) and "PSČ Town"
    (live — the PSČ is the maps-link anchor, the town a separate town-listings
    anchor). Prefer the text before the PSČ; fall back to the text after it when
    nothing precedes it. A trailing "Zobrazit na mapě" map label is stripped first
    so it never leaks into the town name.
    """
    if not cell_text:
        return None, None
    text = _MAP_LABEL_RE.sub(" ", cell_text)
    psc_match = _PSC_RE.search(text)
    if not psc_match:
        return text.strip(" ,\n\t") or None, None
    psc = f"{psc_match.group(1)} {psc_match.group(2)}"
    before = text[: psc_match.start()].strip(" ,\n\t")
    after = text[psc_match.end():].strip(" ,\n\t")
    return (before or after or None), psc


def _detail_table(tree: HTMLParser) -> dict[str, Node]:
    """Map the left details-table row labels to their value cells.

    The value is the LAST cell, not cells[1]: live bazos renders the Lokalita row
    as three cells — label, a map-icon cell, then the cell holding the PSČ/town
    (and the maps link). Reading cells[1] there grabbed the icon cell (no text),
    which left `locality` NULL for every listing.
    For the ordinary two-cell rows (Cena, Vidělo) cells[-1] is still the value.
    """
    rows: dict[str, Node] = {}
    for tr in tree.css("table tr"):
        cells = tr.css("td")
        if len(cells) < 2:
            continue
        label = (cells[0].text(strip=True) or "").rstrip(":").lower()
        if label:
            rows[label] = cells[-1]
    return rows


def _category_from_breadcrumb(tree: HTMLParser) -> tuple[str | None, str | None]:
    """(category_main, category_type) from the detail breadcrumb, or (None, None).

    Reads the breadcrumb LINKS' canonical URL segments (".../pronajmu/byt/" ->
    ('byt', 'pronajem')), NOT the display text: bazos separates the trail with a
    plain ">" and localised labels, so the href segments are the stable signal.
    Lets the drain tell a sale ad from a rental without the queue carrying the
    category."""
    node = tree.css_first("div.drobky")
    if node is None:
        return (None, None)
    cmain: str | None = None
    ctype: str | None = None
    for a in node.css("a"):
        for seg in (a.attributes.get("href") or "").split("/"):
            if ctype is None and seg in SALE_TYPE:
                ctype = SALE_TYPE[seg]
            if cmain is None and seg in CATEGORY_MAIN:
                cmain = CATEGORY_MAIN[seg]
    return (cmain, ctype)


def _subtype_from_breadcrumb(tree: HTMLParser) -> str | None:
    """Portal-agnostic subtype from the detail breadcrumb's section segment, or
    None. Same div.drobky href-segment signal as `_category_from_breadcrumb` —
    e.g. ".../prodam/kancelar/" -> 'kancelar'."""
    node = tree.css_first("div.drobky")
    if node is None:
        return None
    for a in node.css("a"):
        for seg in (a.attributes.get("href") or "").split("/"):
            if seg in SUBTYPE:
                return SUBTYPE[seg]
    return None


def ad_haystack(title: str | None, description: str | None) -> str:
    """The text every free-text field is mined from: the ad's title plus its body.

    Composed HERE so the W19 heal can rebuild exactly the same string from
    `raw_json->>'title'` + `listings.description` without a second copy of the rule.
    """
    return f"{title or ''}\n{description or ''}"


# A labelled area in the prose (Q12/Q14, portal_columns 3.3, 2026-10-26): 1,030 of 18,375 rt
# bazos flats state a celková figure beside a užitná / podlahová / obytná one, and taking
# the FIRST figure stored the celková in 119 of them (101 over 8 % off the usable one).
# "celková užitná / obytná" is still the usable measure; "celková podlahová" is the
# building-law measure with the accessories in (realitymix's 'floor', section 3.2), so it
# reads as total. A label is the adjective WITH its area noun ("užitná plocha", "celková
# výměra"): the bare stem also opens "obytná kuchyně 30 m2", "podlahové topení" and "celková
# rekonstrukce" (432 + 9 stored flats put a room's figure right after one, review of #2053),
# so a bare adjective is read only directly before its figure ("užitná 40 m2"). A label is
# the measure's only when nothing in its clause names an accessory or the parcel ("pozemek o
# celkové výměře 1.139 m²", "užitná plocha sklepa") and nothing between it and its figure
# names a part of the unit ("obytná plocha přízemí 70 m2", "vzroste o dalších 70 m2") or
# folds the accessories in ("vč. příslušenství", "46 m² včetně terasy"); a label a figure
# ends right before ("72 m2 užitné plochy kuchyň 12 m2", "95,3 m² činí podlahová plocha a
# 104,7 m² ...") is that figure's own,
# so it is read only when its figure follows at once ("Zastavěná plocha: 69 m2 Užitná
# plocha: 163 m2", the agency export; "Pozemek: 2 532 m² Užitná plocha cca 250 m²"), and a
# comma ends every label's reach.
_AREA_NOUN_SRC = r"(?:ploch|plo[šs]|v[ýy]m[ěe]r)\w*"
_AREA_LABEL_RE = re.compile(
    rf"(?<!\w)(?:(celkov)\w*\s+)?(u[žz]itn|podlahov|obytn)\w*(\s+{_AREA_NOUN_SRC})?"
    rf"|(?<!\w)(celkov)\w*\s+{_AREA_NOUN_SRC}",
    re.IGNORECASE)
_AREA_UNIT_SRC = r"(?:m(?:2|²|\s2)(?!\d)|㎡|metr\w*\s+[čc]tvere[čc]n\w*)"
_LABELLED_FIGURE_RE = re.compile(
    rf"(?<![\d+.,])({AREA_NUMBER_SRC})\s*{_AREA_UNIT_SRC}", re.IGNORECASE)
_FIGURE_RIGHT_BEFORE_RE = re.compile(
    rf"(?<![\d+.,])(?:{AREA_NUMBER_SRC})\s*{_AREA_UNIT_SRC}"
    r"(?:[ \t]+(?:[čc]in[íi]|je|tvo[řr][íi]|p[řr]edstavuje))?[ \t]*\Z", re.IGNORECASE)
_LABEL_REACH = 40
_CLAUSE_BACK = 30
_REACH_END_RE = re.compile(r"[.!?](?=\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ])|[;\n]|,(?!\d)")
_CLAUSE_START_RE = re.compile(r"[,;:.!?()\n]")
_NOT_THE_UNIT_RE = re.compile(
    r"balk|lod[žz]i|teras|sklep|komor|gar[áa][žz]|p[ůu]d(?:a|y|u|ou|ě)\b|zahrad|pozem|parcel"
    r"|dv[ůo]r(?:ek|ku|u|a|em)?\b",
    re.IGNORECASE)
_A_PART_OF_THE_UNIT_RE = re.compile(
    r"p[řr][íi]zem|patr|podkrov|podla[žz]|\bNP\b|kuchy|pokoj|lo[žz]nic|m[íi]stnost|dal[šs][íi]"
    r"|\ba\b|v[čc]etn|v[čc]\.",
    re.IGNORECASE)
_DIRECT_GAP_RE = re.compile(
    r"[\s:]*(?:(?:cca|ca|asi|p[řr][íi]bli[žz]n[ěe]|zhruba|je|[čc]in[íi])\.?[\s:]*)*",
    re.IGNORECASE)
_WITH_ACCESSORIES_RE = re.compile(r"\s*\(?\s*(?:v[čc]etn|v[čc]\.)", re.IGNORECASE)


def _label_kind(label: re.Match[str]) -> str:
    """'usable', 'living' (obytná) or 'total' for one `_AREA_LABEL_RE` match."""
    adjective = (label[2] or "").lower()
    if not adjective or (label[1] and adjective.startswith("podlahov")):
        return "total"
    return "living" if adjective.startswith("obytn") else "usable"


def labelled_areas(haystack: str | None) -> tuple[float | None, float | None]:
    """(usable, total): the first figure the text labels each measure, or None for one it
    does not label; an obytná figure is the usable one only when no užitná / podlahová figure
    is stated, since it can leave the kitchen and the hall out. A label's figure is the
    first m² within `_LABEL_REACH` characters after it, in its own clause and before the
    next label."""
    found: dict[str, float] = {}
    labels = list(_AREA_LABEL_RE.finditer(haystack or ""))
    for i, label in enumerate(labels):
        kind = _label_kind(label)
        if kind in found:
            continue
        text = label.string
        end = min(label.end() + _LABEL_REACH,
                  labels[i + 1].start() if i + 1 < len(labels) else len(text))
        reach = text[label.end():end]
        stop = _REACH_END_RE.search(reach)
        figure = _LABELLED_FIGURE_RE.search(reach[:stop.start()] if stop else reach)
        if figure is None:
            continue
        direct = _DIRECT_GAP_RE.fullmatch(reach, 0, figure.start()) is not None
        if label[2] and not label[3] and not direct:
            continue
        before = text[max(0, label.start() - _CLAUSE_BACK):label.start()]
        if not direct and _FIGURE_RIGHT_BEFORE_RE.search(before):
            continue
        clause = _CLAUSE_START_RE.split(before)[-1] + reach[:figure.start()]
        if _NOT_THE_UNIT_RE.search(clause) or _A_PART_OF_THE_UNIT_RE.search(reach[:figure.start()]):
            continue
        if _WITH_ACCESSORIES_RE.match(reach, figure.end()):
            continue
        found[kind] = area_token_to_float(figure[1])
    return found.get("usable", found.get("living")), found.get("total")


def areas_from_text(haystack: str | None, *, category_main: str | None,
                    category_type: str | None, disposition: str | None) -> PortalAreas:
    """bazos's area, such as it is — spelled here once and nowhere else.

    bazos has no structured area field at all: no interior measure and no
    "plocha pozemku", so `plot` is genuinely absent rather than unread. A figure the
    text LABELS usable (`labelled_areas`) leads, held to the same band as the prose
    figure (free text is a weaker label than a structured field: a house's "užitná
    plocha 2.500 m2" is still its parcel); a celková figure is passed nowhere, because
    the first figure it would outrank is often the title's usable one. Otherwise the
    resolver's `prose` arm is the whole story, and always on land (it stamps that
    free-text number 'plot', which is what it is on a parcel ad). It is the one arm the
    per-room floor reads, on a sale only, because the first m² in a sale's prose may be
    its cellar while a rental's may be the room it lets. Free prose is also the one
    substrate where a parcel is always written "1 500 m2", which is why the naive
    grammar cost ~12,400 of these rows their thousands digit.
    """
    band = {"category_main": category_main, "disposition": disposition,
            "category_type": category_type}
    usable = None if category_main in LAND_CATEGORIES else labelled_areas(haystack)[0]
    if usable is not None and derive_headline_area(prose=usable, **band)[0] is None:
        usable = None
    area_m2, area_basis = derive_headline_area(
        plot=None, usable=usable, prose=parse_area_text(haystack), **band)
    return PortalAreas(area_m2=area_m2, area_basis=area_basis)


def parse_detail(
    html: str,
    *,
    source_url: str,
    category_main: str | None,
    category_type: str | None,
) -> ScrapedListing:
    tree = HTMLParser(html)
    source_id = native_id("bazos", source_url) or ""

    # The page's own breadcrumb is authoritative for the category (sale vs rent);
    # the passed-in values are only a fallback when it's missing/unrecognised.
    bc_main, bc_type = _category_from_breadcrumb(tree)
    category_main = bc_main or category_main
    category_type = bc_type or category_type
    subtype = _subtype_from_breadcrumb(tree)

    title = _text(tree.css_first("h1.nadpisdetail")) or ""
    description = _text(tree.css_first("div.popisdetail"))
    haystack = ad_haystack(title, description)
    # Deterministic floor (ground=0, like idnes + the LLM rubric) from the same
    # haystack area/disposition come from; the LLM enrichment fills only what this
    # high-precision pass leaves NULL (ambiguous/mezonet cases). The category picks the
    # grammar: a flat's přízemí must be the flat's own, a house keeps the legacy miner.
    floor, total_floors = floor_from_text(haystack, category_main=category_main)

    table = _detail_table(tree)
    price_cell = table.get("cena")
    price_czk, price_unit = _parse_price(_text(price_cell), category_type)

    lok_cell = table.get("lokalita")
    # The embedded maps link. Prefer the lokalita cell, but live bazos renders
    # the "show on map" link elsewhere on the detail page, so fall back to any
    # Google-Maps / Mapy.cz link anywhere on the page.
    maps_link = (lok_cell.css_first('a[href*="map"]') if lok_cell else None) or \
        tree.css_first('a[href*="google.com/maps"], a[href*="mapy.cz"]')
    link_lat, link_lon = _parse_coords(
        maps_link.attributes.get("href") if maps_link else None
    )
    locality, psc = _locality(_text(lok_cell))

    lat, lon, coord_provenance = _resolve_coords(link_lat=link_lat, link_lon=link_lon)

    # The listing's own photos carry its ad id in the URL (/img/N/sub/<id>.jpg); the
    # "podobné inzeráty" footer shows OTHER ads' cover thumbnails — scope to this id.
    image_urls: list[str] = []
    seen: set[str] = set()
    for img in tree.css("img"):
        src = img.attributes.get("src")
        if not src or "bazos.cz/img/" not in src:
            continue
        if source_id and source_id not in src:
            continue
        full = _full_size_image_url(src)
        if full not in seen:
            seen.add(full)
            image_urls.append(full)

    posted_text = _text(tree.css_first("span.velikost10"))

    disposition = vocabulary.disposition("bazos", haystack)
    areas = areas_from_text(haystack, category_main=category_main,
                            category_type=category_type, disposition=disposition)

    raw = {
        "id": source_id,
        "title": title,
        "price_text": _text(price_cell),
        "locality_text": _text(lok_cell),
        "psc": psc,
        "views": _text(table.get("vidělo")) or _text(table.get("videlo")),
        "posted_date": posted_text,
        "image_urls": image_urls,
        "coords": coord_provenance,
    }

    return ScrapedListing(
        source="bazos",
        source_id_native=source_id,
        source_url=source_url,
        category_main=category_main,
        category_type=category_type,
        subtype=subtype,
        price_czk=price_czk,
        price_unit=price_unit,
        area_m2=areas.area_m2,
        area_basis=areas.area_basis,
        disposition=disposition,
        floor=floor,
        total_floors=total_floors,
        locality=locality,
        district=_trailer_quarter(description, locality),
        # The Lokalita cell's PSČ was parsed into raw_json.psc since day one but
        # never written to the column — 29,538 of 29,543 active rows had it in
        # the payload with listings.zip NULL (W0 item 0c). A municipality-grain
        # anchor far better than bazos's collapsed town pins.
        zip=psc,
        lat=lat,
        lon=lon,
        description=description,
        # Bazos re-stamps this date on every bump / TOP renewal — a LAST-BUMP
        # date, not first publication — but it is the only publish signal the
        # portal exposes (and it is out of the content hash, so a bump never
        # churns a snapshot).
        published_at=bazos_posted_date(posted_text),
        raw=raw,
    )
