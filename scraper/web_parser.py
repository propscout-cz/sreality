"""The tenth portal's parser: a page we have never seen before -> ScrapedListing. Pure.

DETERMINISTIC, like bazos: no LLM at ingest (Field-capture §3, R6/R7/R10). The existing
text lane is `web`'s only reader of prose — its one call returns the location block from
contracts/portals/web.yaml's `llm_text` entries and, once the operator opens an R7 gate for
a `(web, field)` cell, that field. What a page states in a machine-readable way, or in the
shared Czech grammars every portal already uses, arrives here:

  * **JSON-LD** (`RealEstateListing`, `Product` + `Offer`, `Offer`, a residence type):
    name, description, images, the offer's price (CZK only — another currency is no
    price), `floorSize`, the seller / agent / offeredBy party, and lat/lon ONLY from a
    `GeoCoordinates` the page itself publishes (never computed, never geocoded);
  * **OpenGraph / meta** (`og:title`, `og:description`, `og:image`, `description`) and the
    `<h1>` (read once the site chrome is gone: a logo `<h1>` in the site header is no title)
    / `<title>`;
  * the **gallery**: the JSON-LD images and `og:image`; the page's `<img>` (lazy-load
    attributes and `srcset` included) and image links only when the JSON-LD lists no image,
    and then only from the main block's own container — absolute, de-duplicated,
    image-looking, never another ad's card;
  * the **stable main text** as `description`: the JSON-LD description, else
    `og:description`, else the main block — the container with the most text in its OWN
    `<p>` children (inside `<article>` / `<main>` when the page has them), chosen after the
    site's landmark header/footer, nav, aside, script, a form without prose and every
    "similar / related / recommended" block are dropped — else, last, the meta description
    (often one tagline for the whole site). Never page chrome or another ad's card, which
    would mint a snapshot on every fetch (rule 2);
  * the shared grammars over title + description: `scraper.price_text` (the per-m² guard),
    `scraper.area`, `scraper.vocabulary.disposition`, `scraper.floor.floor_from_text`, and
    the title words for `category_type` (prodej / pronájem) and `category_main`
    (byt / dům / pozemek / komerční / garáž), from the first of the JSON-LD name, og:title,
    `<h1>`, `<title>` that says — NULL when none does. A price the text states is the first
    CZK amount not named a deposit, fee, advance or commission;
  * contacts: JSON-LD, `mailto:` and `tel:` links, a `+420` / nine-digit number in the ad
    text -> `raw['broker'] = {id: the email (lower) else the phone (+420…), name, email,
    phone, agency}`, and no broker block at all without an id.

The deterministic readings are written at the TOP LEVEL of `raw` (`price_czk`, `area_m2`,
`disposition`, `category_main`, `category_type`, `title`, `description`, `image_urls`,
`broker`, `jsonld`, `og`, …): the field census reads `coalesce(raw->'params', raw)`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from scraper import vocabulary
from scraper.area import derive_headline_area, parse_area_text
from scraper.floor import floor_from_text
from scraper.media import is_image_url
from scraper.portal_identity import WEB, canonical_url
from scraper.price_text import is_per_area_price
from scraper.scraped_listing import ScrapedListing

MAX_IMAGES = 40
MAX_DESCRIPTION = 10_000
_MIN_DESCRIPTION = 40
_MIN_IMG_PX = 150
# The site's own chrome goes first (contacts are then read from what is left, an agent box in
# an <aside> included); the sidebar widgets go before the prose block is chosen. A header or
# footer is chrome only as the SITE's landmark: one inside <article>/<main> is the ad's own.
# A form is chrome only without prose (ASP.NET WebForms wraps the whole page in one).
_SITE_CHROME = ("script", "style", "noscript", "template", "nav", "iframe", "svg", "button",
                "select")
_CONTENT = "article, main, [role=main]"
_CONTENT_TAGS = frozenset({"article", "main"})
_SIDEBAR = ("aside",)
# Another ad's cards ("Podobné nabídky", a recommendation carousel), by the block's class or
# id, or by its own heading. A bare `carousel` is not here: an ad's own gallery is often one.
_RELATED_RE = re.compile(
    r"similar|related|recommend|podobn|doporuc|dalsi[-_ ]?(?:nabid|inzer|nemov)|mohlo[-_ ]by[-_ ]vas")
_RELATED_HEADING_RE = re.compile(
    r"^(?:podobne|dalsi (?:nabid|inzer|nemov)|doporuc|mohlo by vas|similar|related)")
# A Place alone is a location, never the listing (its name would be read as the title);
# its coordinates still count (`_geo`).
_LISTING_TYPES = ("realestatelisting", "product", "offer", "apartment", "house",
                  "singlefamilyresidence", "residence", "accommodation")
_ORG_TYPES = frozenset({"organization", "realestateagent", "localbusiness", "corporation"})
_PARTY_KEYS = ("seller", "agent", "offeredBy", "broker", "provider")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")
_NOT_GALLERY = re.compile(r"logo|icon|sprite|avatar|banner|pixel|tracking|placeholder|blank\.",
                          re.IGNORECASE)

_G = r"[\s\u00a0\u202f\u2009]"
_AMOUNT = rf"(?<![\d.,])(\d{{1,3}}(?:(?:{_G}|\.)\d{{3}})+|\d{{3,}})"
_PRICE_RE = re.compile(rf"{_AMOUNT}(?:,-+)?{_G}*(?:Kč|Kc|CZK)(?![A-Za-z])|{_AMOUNT},-", re.IGNORECASE)
# The nearest cue before an amount (folded, within _CUE_WINDOW chars): a price word keeps it,
# a deposit / fee / advance / commission word skips it ("Kauce 30 000 Kč. Nájemné 18 500 Kč").
_CUE_WINDOW = 30
_PRICE_CUE_RE = re.compile(
    r"(cen[aeuy]|najemne|najem|najmu|nabidkov)"
    r"|(kauc|jistot|poplat|zaloh|proviz|energi|sluzb|depozit|rezervac|vratn)")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_SEP = r"[\s\u00a0\u202f\u2009.\-]"
# +420 with any grouping; a bare number only in the two Czech groupings (602 123 456,
# 602 12 34 56), never followed by a currency or a unit (that is a price or an area).
_PHONE_INTL_RE = re.compile(rf"(?:\+|00){_G}?420{_SEP}?((?:{_SEP}?\d){{9}})(?!\d)")
_PHONE_BARE_RE = re.compile(
    rf"(?<![\d+])([2-7]\d{{2}}(?:{_SEP}?\d{{3}}){{2}}|[2-7]\d{{2}}(?:{_G}\d{{2}}){{3}})(?!\d)"
    rf"(?!{_G}*(?:Kč|Kc|CZK|m2|m²|,-))")

_TYPE_RE = re.compile(r"\b(?:(prodej|prodeji|prodam|prodava\w*)|(pronajem|pronajmu|pronajmeme|podnajem|najem))\b")
# Earliest match in the title wins, so "Prodej bytu v rodinném domě" is a flat and
# "Pronájem kanceláře v bytovém domě" an office.
_MAIN_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("komercni", re.compile(
        r"\b(?:kancelar\w*|(?:obchodni|nebytov\w*|komercni)\w*\s+prostor\w*|komercni\w*|sklad\w*"
        r"|vyrobn\w*|hal[ay]|restaurac\w*|hotel\w*|penzion\w*|ordinac\w*|provozovn\w*"
        r"|(?:najemni|cinzovni|bytov)\w*\s+dom\w*|(?:najemni|cinzovni|bytovy)\s+dum)\b")),
    ("byt", re.compile(r"\b(?:byt|bytu|byty|bytem|bytove?\s+jednotk\w*|apartman\w*|garsonier\w*|mezonet\w*)\b")),
    ("dum", re.compile(
        r"\b(?:dum|domu|domy|domek|domku|vila|vily|vilu|chata|chaty|chatu|chalupa|chalupy|chalupu"
        r"|usedlost\w*|statek|statku|rekreacni\s+objekt\w*)\b")),
    ("pozemek", re.compile(r"\b(?:pozem\w*|parcel\w*|zahrad[ay]|zahradu|louk[ay]|orn\w+\s+pud\w*)\b")),
    ("ostatni", re.compile(r"\b(?:garaz\w*|(?:parkovaci|garazove)\s+stani|stani)\b")),
)


def parse_detail(html: str, *, source_url: str, final_url: str | None = None) -> ScrapedListing:
    """One page of any site. `source_url` is the ad's identity (canonicalised here);
    `final_url` (where the fetch's redirects ended) only resolves relative links."""
    tree = HTMLParser(html)
    canon = canonical_url(source_url) or source_url
    base = _base_url(tree, final_url or source_url)
    items = list(_jsonld_items(tree))
    main = _main_item(items)
    og = _open_graph(tree)
    page_title = _node_text(tree.css_first("title"))
    meta_description = _meta(tree, "description")

    _strip(tree, _SITE_CHROME)
    _strip_landmarks(tree)
    _strip_related(tree)
    h1 = _node_text(tree.css_first("main h1, article h1, [role=main] h1") or tree.css_first("h1"))
    titles = (_clean(_str(main.get("name"))) if main else None, og.get("title"), h1, page_title)
    title = next((t for t in titles if t), None)
    links = _contact_links(tree)
    _strip(tree, _SIDEBAR)
    _strip_prose_free_forms(tree)
    block = _main_block(tree)
    description = _description(main, og, meta_description, block)
    haystack = f"{title or ''}\n{description or ''}"

    category_type = next((v for t in titles if (v := _category_type(t))), None)
    category_main = next((v for t in titles if (v := _category_main(t))), None)
    offer = _offer_price(main)
    price_czk = offer if offer is not None else (_text_price(title) or _text_price(description))
    price_unit = {"prodej": "za nemovitost", "pronajem": "za mesic"}.get(category_type or "")
    disposition = vocabulary.disposition(WEB, title, description)
    floor_size = _floor_size(main)
    area_m2, area_basis = derive_headline_area(
        category_main=category_main, floor=floor_size, fallback=parse_area_text(title),
        prose=parse_area_text(description), disposition=disposition, category_type=category_type,
    )
    floor, total_floors = floor_from_text(haystack, category_main=category_main)
    lat, lon = _geo(main, items)
    image_urls = _images(main, og, block, base)
    broker = _broker(main, links, (description, _block_text(block)))

    raw: dict[str, Any] = {
        "title": title,
        "description": description,
        "price_czk": price_czk,
        "area_m2": area_m2,
        "disposition": disposition,
        "category_main": category_main,
        "category_type": category_type,
        "floor": floor,
        "total_floors": total_floors,
        "image_urls": image_urls,
        "final_url": final_url,
    }
    if broker is not None:
        raw["broker"] = broker
    if main or lat is not None:
        raw["jsonld"] = _jsonld_facts(main, offer, floor_size, lat, lon)
    if og:
        raw["og"] = og

    return ScrapedListing(
        source=WEB,
        source_id_native=canon,
        source_url=canon,
        category_main=category_main,
        category_type=category_type,
        price_czk=price_czk,
        price_unit=price_unit if price_czk is not None else None,
        area_m2=area_m2,
        area_basis=area_basis,
        disposition=disposition,
        floor=floor,
        total_floors=total_floors,
        lat=lat,
        lon=lon,
        description=description,
        published_at=_published(main),
        raw=raw,
    )


# --- text helpers ----------------------------------------------------------------------------


def _fold(text: str | None) -> str:
    plain = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in plain if not unicodedata.combining(c)).lower()


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    if "<" in text and ">" in text:
        text = HTMLParser(text).text(separator=" ")
    text = re.sub(r"[ \t\r\u00a0\u202f\u2009]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return re.sub(r" {2,}", " ", text).strip() or None


def _str(value: Any) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str)), None)
    return value if isinstance(value, str) else None


def _node_text(node: Node | None) -> str | None:
    return _clean(node.text(separator=" ")) if node is not None else None


def _meta(tree: HTMLParser, name: str) -> str | None:
    node = tree.css_first(f'meta[name="{name}"]') or tree.css_first(f'meta[property="{name}"]')
    return _clean(node.attributes.get("content")) if node is not None else None


# --- JSON-LD ----------------------------------------------------------------------------------


def _jsonld_items(tree: HTMLParser) -> Iterator[dict[str, Any]]:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            # strict=False: hand-built JSON-LD often carries a raw newline inside a string.
            data = json.loads(node.text() or "", strict=False)
        except ValueError:
            continue
        yield from _walk_jsonld(data)


def _walk_jsonld(data: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Every item, an `@graph`'s members and a page's `mainEntity` included."""
    if depth > 3:
        return
    for item in data if isinstance(data, list) else [data]:
        if not isinstance(item, dict):
            continue
        yield item
        for key in ("@graph", "mainEntity"):
            nested = item.get(key)
            if isinstance(nested, (dict, list)):
                yield from _walk_jsonld(nested, depth + 1)


def _types(item: dict[str, Any]) -> list[str]:
    t = item.get("@type")
    return [x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def _main_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The listing itself: the first item of the most specific listing type the page has."""
    for wanted in _LISTING_TYPES:
        for item in items:
            if wanted in _types(item):
                return item
    return {}


def _offers(main: dict[str, Any]) -> list[dict[str, Any]]:
    if "offer" in _types(main):
        return [main]
    offers = main.get("offers")
    out = offers if isinstance(offers, list) else [offers]
    return [o for o in out if isinstance(o, dict)]


def _offer_price(main: dict[str, Any]) -> int | None:
    for offer in _offers(main):
        spec = offer.get("priceSpecification")
        spec = spec[0] if isinstance(spec, list) and spec else spec
        spec = spec if isinstance(spec, dict) else {}
        price = offer.get("price", spec.get("price"))
        currency = str(offer.get("priceCurrency") or spec.get("priceCurrency") or "CZK").upper()
        if currency != "CZK" or str(spec.get("unitCode") or "").upper() == "MTK":
            continue
        try:
            value = int(float(str(price).replace(" ", "").replace(",", ".")))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _floor_size(main: dict[str, Any]) -> float | None:
    offered = main.get("itemOffered")
    size = main.get("floorSize") or (offered.get("floorSize") if isinstance(offered, dict) else None)
    if isinstance(size, dict):
        unit = str(size.get("unitCode") or size.get("unitText") or "MTK").upper()
        if unit not in ("MTK", "M2", "M²", "SQM"):
            return None
        size = size.get("value")
    try:
        value = float(str(size).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _find_geo(obj: Any, depth: int = 0) -> tuple[float, float] | None:
    if depth > 4:
        return None
    if isinstance(obj, list):
        for x in obj:
            found = _find_geo(x, depth + 1)
            if found:
                return found
        return None
    if not isinstance(obj, dict):
        return None
    if "latitude" in obj and "longitude" in obj:
        try:
            lat, lon = float(obj["latitude"]), float(obj["longitude"])
        except (TypeError, ValueError):
            return None
        return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0) else None
    for key in ("geo", "contentLocation", "itemOffered", "location", "address"):
        found = _find_geo(obj.get(key), depth + 1)
        if found:
            return found
    return None


def _geo(main: dict[str, Any], items: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """lat/lon only as the page's own JSON-LD states them: the listing's, else a Place's."""
    found = _find_geo(main) if main else None
    if found is None:
        for item in items:
            if set(_types(item)) & {"place", "residence", "apartment", "house", "accommodation"}:
                found = _find_geo(item)
                if found:
                    break
    return found if found else (None, None)


def _published(main: dict[str, Any]) -> datetime | date | None:
    value = _str(main.get("datePosted") or main.get("datePublished")) if main else None
    if not value:
        return None
    try:
        if len(value) <= 10:
            return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _jsonld_facts(main: dict[str, Any], price: int | None, floor_size: float | None,
                  lat: float | None, lon: float | None) -> dict[str, Any]:
    facts = {"type": (_types(main) or [None])[0], "name": _str(main.get("name")),
             "price_czk": price, "floor_size_m2": floor_size, "lat": lat, "lon": lon}
    return {k: v for k, v in facts.items() if v is not None}


# --- OpenGraph, description, main block -----------------------------------------------------------


def _open_graph(tree: HTMLParser) -> dict[str, str]:
    og: dict[str, str] = {}
    for key in ("title", "description", "image", "site_name", "type"):
        value = _meta(tree, f"og:{key}")
        if value:
            og[key] = value
    return og


def _decompose(nodes: list[Node]) -> None:
    """Drop each node, a node inside another one dropped skipped (its memory goes with it)."""
    chosen = {n.mem_id for n in nodes}
    for node in nodes:
        parent = node.parent
        while parent is not None and parent.mem_id not in chosen:
            parent = parent.parent
        if parent is None:
            node.decompose()


def _strip(tree: HTMLParser, selectors: tuple[str, ...]) -> None:
    for selector in selectors:
        _decompose(list(tree.css(selector)))


def _inside_content(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.tag in _CONTENT_TAGS or (parent.attributes.get("role") or "") == "main":
            return True
        parent = parent.parent
    return False


def _strip_landmarks(tree: HTMLParser) -> None:
    """The site's header and footer; an `<article>`'s own header (its title, its price) stays."""
    _decompose([n for n in tree.css("header, footer") if not _inside_content(n)])


def _strip_related(tree: HTMLParser) -> None:
    """Another ad's cards: a block whose class or id says similar / related / recommended, or
    a section under a "Podobné nabídky" heading (never the content landmark itself)."""
    marked = [n for n in tree.css("[class], [id]")
              if _RELATED_RE.search(_fold(f"{n.attributes.get('class') or ''} {n.attributes.get('id') or ''}"))]
    for heading in tree.css("h2, h3, h4"):
        parent = heading.parent
        if (parent is not None and parent.tag not in (*_CONTENT_TAGS, "body", "html")
                and _RELATED_HEADING_RE.match(_fold(_node_text(heading)))):
            marked.append(parent)
    _decompose(marked)


def _strip_prose_free_forms(tree: HTMLParser) -> None:
    _decompose([f for f in tree.css("form")
                if not any(len((p.text() or "").strip()) >= _MIN_DESCRIPTION for p in f.css("p"))])


def _own_paragraphs(node: Node) -> list[Node]:
    return [child for child in node.iter() if child.tag == "p"]


def _main_block(tree: HTMLParser) -> Node | None:
    """The container with the most text in its OWN `<p>` children, searched inside the page's
    article/main when it has one (a landmark is a scope, never a candidate: it would hold
    every card beside the ad)."""
    paragraphs = [p for scope in tree.css(_CONTENT) for p in scope.css("p")] or list(tree.css("p"))
    best, best_score, seen = None, 0, set()
    for p in paragraphs:
        parent = p.parent
        if parent is None or parent.mem_id in seen:
            continue
        seen.add(parent.mem_id)
        score = sum(len((c.text() or "").strip()) for c in _own_paragraphs(parent))
        if score > best_score:
            best, best_score = parent, score
    return best


def _block_text(block: Node | None) -> str | None:
    if block is None:
        return None
    paragraphs = [t for t in (_clean(p.text(separator=" ")) for p in _own_paragraphs(block)) if t]
    text = "\n\n".join(paragraphs) if paragraphs else _clean(block.text(separator="\n"))
    return text if text and len(text) >= _MIN_DESCRIPTION else None


def _description(main: dict[str, Any], og: dict[str, str], meta: str | None,
                 block: Node | None) -> str | None:
    """JSON-LD, og:description, the main block; the meta description last (a small site's
    is often one tagline on every page)."""
    for text in (_clean(_str(main.get("description"))) if main else None, og.get("description")):
        if text and len(text) >= _MIN_DESCRIPTION:
            return text[:MAX_DESCRIPTION]
    text = _block_text(block)
    if text:
        return text[:MAX_DESCRIPTION]
    return meta[:MAX_DESCRIPTION] if meta and len(meta) >= _MIN_DESCRIPTION else None


# --- the shared grammars ----------------------------------------------------------------------


def _category_type(title: str | None) -> str | None:
    m = _TYPE_RE.search(_fold(title))
    if not m:
        return None
    return "prodej" if m.group(1) else "pronajem"


def _category_main(title: str | None) -> str | None:
    folded = _fold(title)
    hits = [(m.start(), canon) for canon, rule in _MAIN_RES if (m := rule.search(folded))]
    return min(hits)[1] if hits else None


def _text_price(text: str | None) -> int | None:
    """The first amount in CZK the text states that no deposit / fee / commission word names;
    a per-m² figure is no price (price_text), `,-` spelling included."""
    if not text:
        return None
    for m in _PRICE_RE.finditer(text):
        group = 1 if m.group(1) else 2
        before = _fold(text[max(0, m.start(group) - _CUE_WINDOW):m.start(group)])
        cues = list(_PRICE_CUE_RE.finditer(before))
        if cues and cues[-1].group(2):
            continue
        if is_per_area_price(re.sub(r"^,-+", "", text[m.end(group):])):
            return None
        digits = re.sub(r"\D", "", m.group(group))
        return int(digits) if digits and int(digits) > 0 else None
    return None


# --- images -----------------------------------------------------------------------------------


def _base_url(tree: HTMLParser, page_url: str) -> str:
    node = tree.css_first("base[href]")
    href = node.attributes.get("href") if node is not None else None
    return urljoin(page_url, href) if href else page_url


def _srcset_largest(srcset: str) -> str | None:
    best, best_w = None, -1.0
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        width = -0.5
        if len(bits) > 1 and bits[1][:-1].replace(".", "", 1).isdigit():
            width = float(bits[1][:-1])
        if width > best_w:
            best, best_w = bits[0], width
    return best


def _jsonld_images(main: dict[str, Any]) -> list[str]:
    image = main.get("image") if main else None
    out: list[str] = []
    for item in image if isinstance(image, list) else [image]:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            url = _str(item.get("contentUrl") or item.get("url"))
            if url:
                out.append(url)
    return out


def _img_candidates(scope: Node | HTMLParser) -> Iterator[str]:
    for img in scope.css("img"):
        attrs = img.attributes
        too_small = any(
            (v := attrs.get(k)) and v.isdigit() and int(v) < _MIN_IMG_PX for k in ("width", "height"))
        if too_small:
            continue
        for key in ("data-src", "data-lazy-src", "data-original", "data-full", "src"):
            if attrs.get(key):
                yield attrs[key]  # type: ignore[misc]
                break
        else:
            srcset = attrs.get("data-srcset") or attrs.get("srcset")
            if srcset:
                largest = _srcset_largest(srcset)
                if largest:
                    yield largest
    for a in scope.css("a[href]"):
        href = a.attributes.get("href") or ""
        if urlsplit(href).path.lower().endswith(_IMAGE_EXT):
            yield href


def _gallery_scope(block: Node) -> Node:
    """The main block's own container (its gallery is usually a sibling), never the page."""
    parent = block.parent
    if block.tag in ("body", "html") or parent is None or parent.tag in ("body", "html"):
        return block
    return parent


def _images(main: dict[str, Any], og: dict[str, str], block: Node | None, base: str) -> list[str]:
    """The JSON-LD images and og:image; the DOM's only when the JSON-LD lists none."""
    listed = _jsonld_images(main)
    raw = [*listed, *([og["image"]] if og.get("image") else [])]
    if not listed and block is not None:
        raw += list(_img_candidates(_gallery_scope(block)))
    out: list[str] = []
    for src in raw:
        src = src.strip()
        if not src or src.startswith("data:"):
            continue
        url = urljoin(base, src)
        path = urlsplit(url).path.lower()
        if (urlsplit(url).scheme not in ("http", "https") or path.endswith((".svg", ".gif", ".ico"))
                or _NOT_GALLERY.search(path) or not is_image_url(url) or url in out):
            continue
        out.append(url)
        if len(out) >= MAX_IMAGES:
            break
    return out


# --- contacts ---------------------------------------------------------------------------------


def _norm_email(value: str | None) -> str | None:
    if not value:
        return None
    email = value.strip().removeprefix("mailto:").split("?", 1)[0].strip().lower()
    return email if _EMAIL_RE.match(email) else None


def _norm_phone(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().removeprefix("tel:")
    digits = re.sub(r"\D", "", raw)
    if raw.lstrip().startswith(("+", "00")) and not digits.startswith(("420", "00420")):
        digits = digits.removeprefix("00")
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    digits = digits.removeprefix("00").removeprefix("420")
    return f"+420{digits}" if len(digits) == 9 and digits[0] in "234567" else None


def _party(main: dict[str, Any]) -> dict[str, Any]:
    for holder in [main, *_offers(main)] if main else []:
        for key in _PARTY_KEYS:
            party = holder.get(key)
            party = party[0] if isinstance(party, list) and party else party
            if isinstance(party, dict):
                return party
    return {}


def _contact_links(tree: HTMLParser) -> tuple[str | None, str | None]:
    """The first `mailto:` and `tel:` link outside the site chrome (an agent box counts)."""
    email = next((e for a in tree.css('a[href^="mailto:"]')
                  if (e := _norm_email(a.attributes.get("href")))), None)
    phone = next((p for a in tree.css('a[href^="tel:"]')
                  if (p := _norm_phone(a.attributes.get("href")))), None)
    return email, phone


def _broker(main: dict[str, Any], links: tuple[str | None, str | None],
            texts: tuple[str | None, ...]) -> dict[str, Any] | None:
    party = _party(main)
    org = bool(set(_types(party)) & _ORG_TYPES)
    employer = next((p for k in ("worksFor", "memberOf", "parentOrganization")
                     if isinstance(p := party.get(k), dict)), {})
    email = _norm_email(_str(party.get("email"))) or links[0]
    phone = _norm_phone(_str(party.get("telephone"))) or links[1]
    for text in texts if phone is None else ():
        m = (_PHONE_INTL_RE.search(text) or _PHONE_BARE_RE.search(text)) if text else None
        if m:
            phone = _norm_phone(m.group(1))
            break
    broker_id = email or phone
    if broker_id is None:
        return None
    name = None if org else _clean(_str(party.get("name")))
    agency = _clean(_str(party.get("name"))) if org else _clean(_str(employer.get("name")))
    return {"id": broker_id, "name": name, "email": email, "phone": phone, "agency": agency}
