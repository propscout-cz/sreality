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
    (often one tagline for the whole site). The main block beats a metadata description that
    is an excerpt of it (cut off with "…" / "[…]", or a prefix of its text). Never page chrome
    or another ad's card, which would mint a snapshot on every fetch (rule 2);
  * the page's **labelled facts** inside the same content: a table row's `th`/`td`, a
    `dt`/`dd`, a label-class element beside a value-class one, an `li`/`p` reading "Label:
    value", and a price box (a short price label beside an amount) — never inside a hidden
    block or a dialog — kept in `raw['facts']`;
  * the ONE text grammar, `scraper.text_grammar.parse_text`, over the title, that text and
    those facts (the file portal reads a document the same way): the labelled fields through
    the shared label table and `scraper.vocabulary`, the price (the JSON-LD offer, else a
    labelled one, else the first CZK amount in the title then the text that no deposit / fee /
    commission word names, never a per-m² figure), `scraper.area`, the disposition, the storey,
    and the prodej / pronájem + byt / dům / pozemek / komerční / garáž words of a labelled type,
    else the first of the JSON-LD name, og:title, `<h1>`, `<title>` that says — NULL when none
    does;
  * contacts: JSON-LD, `mailto:` and `tel:` links, a labelled phone / e-mail, a `+420` /
    nine-digit number in the ad text -> `raw['broker'] = {id: the email (lower) else the phone
    (+420…), name, email, phone, agency}`, and no broker block at all without an id.

The deterministic readings are written at the TOP LEVEL of `raw` (`price_czk`, `area_m2`,
`disposition`, `category_main`, `category_type`, `title`, `description`, `image_urls`,
`broker`, `jsonld`, `og`, `facts`, …): the field census reads `coalesce(raw->'params', raw)`.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from scraper.media import is_image_url
from scraper.portal_identity import WEB, canonical_url
from scraper.scraped_listing import ScrapedListing
from scraper.text_grammar import (
    MAX_LABEL,
    MAX_VALUE,
    fold_text,
    label_field,
    norm_email,
    norm_phone,
    parse_text,
    split_fact,
    text_phone,
    text_price,
)

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

# Where a page's labelled facts may hide outside the ad: a dialog (a "send to a friend" form),
# anything the page itself hides from a reader.
_HIDDEN = '[aria-hidden="true"], [role="dialog"], dialog'
_BLOCK_TAGS = "ul, ol, table, dl, li, p, div"
# A metadata description cut short: "…", "...", "[…]".
_ELLIPSIS_RE = re.compile(r"\s*(?:\[\s*(?:…|\.{3})\s*\]|…|\.{3})\s*$")


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
    block_text = _block_text(block)
    description = _description(main, og, meta_description, block_text)
    image_urls = _images(main, og, block, base)
    facts = _page_facts(tree)
    offer = _offer_price(main)
    floor_size = _floor_size(main)
    text = parse_text(title, description, facts, portal=WEB, other_titles=titles,
                      stated_price=offer, stated_floor_area=floor_size)
    lat, lon = _geo(main, items)
    broker = _broker(main, links, text.phone or text_phone(block_text), text.email)

    raw: dict[str, Any] = {
        "title": title,
        "description": description,
        "price_czk": text.price_czk,
        "area_m2": text.area_m2,
        "disposition": text.disposition,
        "category_main": text.category_main,
        "category_type": text.category_type,
        "floor": text.floor,
        "total_floors": text.total_floors,
        "image_urls": image_urls,
        "final_url": final_url,
    }
    if facts:
        raw["facts"] = [[label, value] for label, value in facts]
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
        category_main=text.category_main,
        category_type=text.category_type,
        price_czk=text.price_czk,
        price_unit=text.price_unit,
        area_m2=text.area_m2,
        area_basis=text.area_basis,
        disposition=text.disposition,
        floor=text.floor,
        total_floors=text.total_floors,
        has_balcony=text.has_balcony,
        has_parking=text.has_parking,
        has_lift=text.has_lift,
        building_type=text.building_type,
        condition=text.condition,
        energy_rating=text.energy_rating,
        estate_area=text.estate_area,
        usable_area=text.usable_area,
        garden_area=text.garden_area,
        furnished=text.furnished,
        terrace=text.terrace,
        cellar=text.cellar,
        garage=text.garage,
        ownership=text.ownership,
        lat=lat,
        lon=lon,
        description=description,
        published_at=_published(main),
        raw=raw,
    )


# --- text helpers ----------------------------------------------------------------------------


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
              if _RELATED_RE.search(fold_text(f"{n.attributes.get('class') or ''} {n.attributes.get('id') or ''}"))]
    for heading in tree.css("h2, h3, h4"):
        parent = heading.parent
        if (parent is not None and parent.tag not in (*_CONTENT_TAGS, "body", "html")
                and _RELATED_HEADING_RE.match(fold_text(_node_text(heading)))):
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
                 block_text: str | None) -> str | None:
    """JSON-LD, og:description, the main block; the meta description last (a small site's
    is often one tagline on every page). The main block beats a metadata excerpt of itself:
    one cut off with an ellipsis, or a prefix of the block's text."""
    for text in (_clean(_str(main.get("description"))) if main else None, og.get("description")):
        if text and len(text) >= _MIN_DESCRIPTION:
            if block_text and _excerpt_of(text, block_text):
                return block_text[:MAX_DESCRIPTION]
            return text[:MAX_DESCRIPTION]
    if block_text:
        return block_text[:MAX_DESCRIPTION]
    return meta[:MAX_DESCRIPTION] if meta and len(meta) >= _MIN_DESCRIPTION else None


def _excerpt_of(excerpt: str, full: str) -> bool:
    cut = _ELLIPSIS_RE.sub("", excerpt)
    short, long = " ".join(cut.split()), " ".join(full.split())
    return len(long) > len(short) and (cut != excerpt or long.startswith(short))


# --- the page's labelled facts ----------------------------------------------------------------


def _page_facts(tree: HTMLParser) -> list[tuple[str, str]]:
    """Every `(label, value)` pair the ad's content states, in page order and once each: a
    table row's `th`/`td` (or a `td` label ending in a colon), a `dt`/`dd`, an element whose
    class names a label beside one whose class names a value, an `li`/`p` reading "Label:
    value", and a price box (a short price label beside an amount). Read inside the content
    landmarks when the page has them, never inside a hidden block or a dialog."""
    # "67 m<sup>2</sup>" reads "67 m2", not "67 m 2" (the description is already taken).
    tree.unwrap_tags(["sup", "sub"])
    tree.merge_text_nodes()
    hidden = {n.mem_id for n in tree.css(_HIDDEN)}
    scopes = list(tree.css(_CONTENT)) or ([tree.body] if tree.body is not None else [])
    facts: list[tuple[str, str]] = []
    for scope in scopes:
        for node in scope.traverse(include_text=True):
            for fact in (_price_box(node) if node.tag == "-text" else _element_facts(node)):
                if fact not in facts and not _inside(node, hidden):
                    facts.append(fact)
    return facts


def _element_facts(node: Node) -> Iterator[tuple[str, str]]:
    tag = node.tag
    if tag == "tr":
        cells = [c for c in node.iter() if c.tag in ("th", "td")]
        for label, value in zip(cells, cells[1:]):
            text = _node_text(label) or ""
            if value.tag == "td" and (label.tag == "th" or text.endswith(":")):
                yield from _fact(text, _node_text(value))
    elif tag == "dt":
        sibling = _next_element(node)
        if sibling is not None and sibling.tag == "dd":
            yield from _fact(_node_text(node), _node_text(sibling))
    elif tag in ("li", "p"):
        if not any(n.mem_id != node.mem_id for n in node.css(_BLOCK_TAGS)):
            fact = split_fact(_node_text(node))
            if fact:
                yield fact
    if "label" in (node.attributes.get("class") or "").lower():
        sibling = _next_element(node)
        if sibling is not None and "value" in (sibling.attributes.get("class") or "").lower():
            yield from _fact(_node_text(node), _node_text(sibling))


def _price_box(text_node: Node) -> Iterator[tuple[str, str]]:
    """A price label alone in its element (climbing wrappers that hold nothing else), beside
    an element that states an amount: `<div>Cena nemovitosti</div><div>8.950.000 Kč</div>`."""
    raw = text_node.text()
    if not raw or len(raw) > 4 * MAX_LABEL or raw.isspace():
        return
    label = _clean(raw)
    if not label or len(label) > MAX_LABEL or label_field(label) != "price":
        return
    element = text_node.parent
    while element is not None and element.tag not in ("body", "html") and _node_text(element) == label:
        sibling = _next_element(element)
        if sibling is not None:
            value = _node_text(sibling)
            if text_price(value) is not None:
                yield from _fact(label, value)
            return
        element = element.parent


def _fact(label: str | None, value: str | None) -> Iterator[tuple[str, str]]:
    label = (label or "").rstrip(":").strip()
    if label and value and len(label) <= MAX_LABEL and len(value) <= MAX_VALUE:
        yield label, value


def _next_element(node: Node) -> Node | None:
    sibling = node.next
    while sibling is not None and sibling.tag.startswith(("-", "_")):
        sibling = sibling.next
    return sibling


def _inside(node: Node, marked: set[int]) -> bool:
    current: Node | None = node
    while current is not None:
        if current.mem_id in marked:
            return True
        current = current.parent
    return False


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
                  if (e := norm_email(a.attributes.get("href")))), None)
    phone = next((p for a in tree.css('a[href^="tel:"]')
                  if (p := norm_phone(a.attributes.get("href")))), None)
    return email, phone


def _broker(main: dict[str, Any], links: tuple[str | None, str | None],
            said_phone: str | None, said_email: str | None) -> dict[str, Any] | None:
    """The JSON-LD party, then the page's `mailto:` / `tel:` links, then what the text grammar
    read (a labelled contact, a number in the ad's text)."""
    party = _party(main)
    org = bool(set(_types(party)) & _ORG_TYPES)
    employer = next((p for k in ("worksFor", "memberOf", "parentOrganization")
                     if isinstance(p := party.get(k), dict)), {})
    email = norm_email(_str(party.get("email"))) or links[0] or said_email
    phone = norm_phone(_str(party.get("telephone"))) or links[1] or said_phone
    broker_id = email or phone
    if broker_id is None:
        return None
    name = None if org else _clean(_str(party.get("name")))
    agency = _clean(_str(party.get("name"))) if org else _clean(_str(employer.get("name")))
    return {"id": broker_id, "name": name, "email": email, "phone": phone, "agency": agency}
