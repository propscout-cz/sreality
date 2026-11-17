"""One Czech grammar for an advert stated in words: a title, its prose and its labelled facts.

Plain text in, so a web page (`scraper.web_parser` hands over the page's fact table, price box
and prose) and a document's text lines (`facts_from_lines`) read one way. Deterministic, no
LLM (Field-capture §3, R6/R7):

  * **labelled facts** — `(label, value)` pairs. The label is folded (`vocabulary.fold`) and
    looked up in `_LABELS`; `_IGNORED_LABELS` are known and read elsewhere (the place is the
    text lane's) or nowhere. An unknown label is COUNTED under `label/<portal>/<label>` in the
    vocabulary's unmapped counter, which the run summary drains (`take_unmapped`), never
    matched by likeness. Enum values go through `vocabulary.canonical`, flags through
    `vocabulary.yes_no` (a stated size is presence), areas through `scraper.area`, storeys
    through `scraper.floor`;
  * the **categories**: a labelled `Typ nabídky` / `Typ nemovitosti`, else the prodej /
    pronájem and byt / dům / pozemek / komerční / garáž words of the first title that says;
  * the **price**: one the caller read from structure, else a labelled one, else the first CZK
    amount in the title, then the prose, that no deposit / fee / commission word names;
    a per-m² figure is never the price (`scraper.price_text`);
  * the **disposition**, **area** and **storey**: the labelled reading first, then the shared
    grammars over the title + prose (`vocabulary.disposition`, `derive_headline_area`,
    `floor_from_text`);
  * the broker's **contacts**: a labelled phone / e-mail, else a +420 / nine-digit number in
    the prose.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence, TypeVar

from scraper import vocabulary
from scraper.area import PortalAreas, derive_headline_area, parse_area_text
from scraper.floor import floor_from_text, is_plausible_floor, total_floors_from_portal
from scraper.price_text import is_per_area_price

PRICE_UNITS: dict[str, str] = {"prodej": "za nemovitost", "pronajem": "za mesic"}
MAX_LABEL = 40
MAX_VALUE = 200

_G = r"[\s   ]"
_AMOUNT = rf"(?<![\d.,])(\d{{1,3}}(?:(?:{_G}|\.)\d{{3}})+|\d{{3,}})"
_PRICE_RE = re.compile(rf"{_AMOUNT}(?:,-+)?{_G}*(?:Kč|Kc|CZK)(?![A-Za-z])|{_AMOUNT},-", re.IGNORECASE)
# The nearest cue before an amount (folded, within _CUE_WINDOW chars): a price word keeps it,
# a deposit / fee / advance / commission word skips it ("Kauce 30 000 Kč. Nájemné 18 500 Kč").
_CUE_WINDOW = 30
_PRICE_CUE_RE = re.compile(
    r"(cen[aeuy]|najemne|najem|najmu|nabidkov)"
    r"|(kauc|jistot|poplat|zaloh|proviz|energi|sluzb|depozit|rezervac|vratn)")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_EMAIL_IN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SEP = r"[\s   .\-]"
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

# "Label: value" — a short label that starts with a letter and ends with a letter, a dot or a
# bracket, so a clock ("10:00") or a URL ("https://…") is never a label.
_LABEL_VALUE_RE = re.compile(
    rf"^\s*([^\W\d_](?:[^:\n]{{0,{MAX_LABEL - 2}}}(?:[^\W\d_]|[.)]))?)\s*:\s*(?!//)(\S.*?)\s*$")

# Folded label -> what it states. One entry per spelling a page or an exposé uses; the nine
# portals' own spec-row labels (attribute_contract) are spelled here too.
_LABELS: dict[str, str] = {
    "cena": "price", "cena_nemovitosti": "price", "celkova_cena": "price", "cena_celkem": "price",
    "kupni_cena": "price", "prodejni_cena": "price", "nabidkova_cena": "price",
    "najemne": "price", "mesicni_najemne": "price", "cena_najmu": "price", "najem": "price",
    "uzitna_plocha": "usable_area", "plocha_uzitna": "usable_area",
    "podlahova_plocha": "floor_area", "plocha": "floor_area", "plocha_bytu": "floor_area",
    "celkova_plocha": "total_area",
    "plocha_pozemku": "estate_area", "pozemek": "estate_area", "plocha_parcely": "estate_area",
    "zahrada": "garden_area", "plocha_zahrady": "garden_area",
    "podlazi": "floor", "patro": "floor", "cislo_podlazi": "floor",
    "pocet_podlazi": "total_floors", "podlazi_celkem": "total_floors",
    "vlastnictvi": "ownership", "typ_vlastnictvi": "ownership",
    "budova": "building_type", "konstrukce": "building_type", "konstrukce_budovy": "building_type",
    "typ_budovy": "building_type", "stavba": "building_type",
    "stav": "condition", "stav_objektu": "condition", "stav_nemovitosti": "condition",
    "stav_bytu": "condition", "stav_budovy": "condition",
    "vybaveni": "furnished", "vybaveno": "furnished", "zarizeno": "furnished",
    "energeticka_narocnost": "energy_rating", "energeticka_narocnost_budovy": "energy_rating",
    "trida_energeticke_narocnosti": "energy_rating", "penb": "energy_rating",
    "dispozice": "disposition",
    "typ_nabidky": "category_type", "typ_nemovitosti": "category_main",
    "sklep": "cellar", "plocha_sklepu": "cellar",
    "balkon": "has_balcony", "lodzie": "has_balcony", "plocha_balkonu": "has_balcony",
    "plocha_lodzie": "has_balcony",
    "terasa": "terrace", "plocha_terasy": "terrace",
    "vytah": "has_lift",
    "parkovani": "parking", "parkovaci_stani": "parking", "pocet_parkovacich_mist": "parking",
    "garaz": "garage",
    "telefon": "phone", "tel.": "phone", "mobil": "phone",
    "e-mail": "email", "email": "email",
}
_IGNORED_LABELS = frozenset({
    # The place: the text lane locates the ad from its title + prose (contracts/portals/*.yaml).
    "lokalita", "adresa", "ulice", "mesto", "obec", "cast_obce", "mestska_cast", "okres", "kraj",
    # Beside the price, never the price.
    "poznamka_k_cene", "cena_za_m2", "cena_za_m²", "poplatky", "mesicni_poplatky", "kauce",
    "provize",
    # A built-up area is no headline measure (as the portals ignore it).
    "zastavena_plocha",
})

_ENUMS = ("ownership", "building_type", "condition", "furnished")
_FLAGS = ("cellar", "has_balcony", "terrace", "has_lift", "garage")

T = TypeVar("T")


@dataclass(frozen=True)
class TextFacts:
    price_czk: int | None = None
    price_unit: str | None = None
    category_main: str | None = None
    category_type: str | None = None
    disposition: str | None = None
    area_m2: float | None = None
    area_basis: str | None = None
    usable_area: float | None = None
    estate_area: float | None = None
    garden_area: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    ownership: str | None = None
    building_type: str | None = None
    condition: str | None = None
    furnished: str | None = None
    energy_rating: str | None = None
    cellar: bool | None = None
    has_balcony: bool | None = None
    terrace: bool | None = None
    has_lift: bool | None = None
    has_parking: bool | None = None
    garage: bool | None = None
    phone: str | None = None
    email: str | None = None


def parse_text(title: str | None, prose: str | None, facts: Sequence[tuple[str, str]] = (), *,
               portal: str, other_titles: Sequence[str | None] = (),
               stated_price: int | None = None,
               stated_floor_area: float | None = None) -> TextFacts:
    """Everything the words state. `other_titles` (further titles of the same ad, in order) are
    read for the category words after `title`; `stated_price` / `stated_floor_area` are figures
    the caller read from structure (web: the JSON-LD offer and floorSize), which lead."""
    said = _labelled(facts, portal)
    titles = (title, *other_titles)
    category_type = _first(category_type_of(v) for v in (*said["category_type"], *titles))
    category_main = _first(category_main_of(v) for v in (*said["category_main"], *titles))
    price = stated_price if stated_price is not None else _first(
        text_price(v) for v in (*said["price"], title, prose))
    disposition = vocabulary.disposition(portal, *said["disposition"], title, prose)
    floor, total = _storeys(said, category_main, f"{title or ''}\n{prose or ''}")
    plot = _area(said["estate_area"])
    usable = _area(said["usable_area"])
    headline, basis = derive_headline_area(
        category_main=category_main, usable=usable,
        floor=stated_floor_area if stated_floor_area is not None else _area(said["floor_area"]),
        total=_area(said["total_area"]), plot=plot, fallback=parse_area_text(title),
        prose=parse_area_text(prose), disposition=disposition, category_type=category_type,
    )
    areas = PortalAreas(headline, basis, usable_area=usable, estate_area=plot,
                        garden_area=_area(said["garden_area"]))
    enums = {f: _first(vocabulary.canonical(f, portal, v) for v in said[f]) for f in _ENUMS}
    flags = {f: vocabulary.any_true(*map(_flag, said[f])) for f in _FLAGS}
    return TextFacts(
        price_czk=price,
        price_unit=PRICE_UNITS.get(category_type or "") if price is not None else None,
        category_main=category_main,
        category_type=category_type,
        disposition=disposition,
        area_m2=areas.area_m2,
        area_basis=areas.area_basis,
        usable_area=areas.usable_area,
        estate_area=areas.estate_area,
        garden_area=areas.garden_area,
        floor=floor,
        total_floors=total,
        energy_rating=_first(vocabulary.energy_rating(v) for v in said["energy_rating"]),
        has_parking=vocabulary.any_true(*map(_parking, said["parking"]), flags["garage"]),
        phone=_first(text_phone(v) for v in (*said["phone"], prose)),
        email=_first(_email_in(v) for v in said["email"]),
        **enums,
        **flags,
    )


def split_fact(line: str | None) -> tuple[str, str] | None:
    """`("Label", "value")` when a line reads "Label: value" with a short label, else None."""
    m = _LABEL_VALUE_RE.match(line or "")
    if not m or len(m.group(2)) > MAX_VALUE:
        return None
    return m.group(1), m.group(2)


def facts_from_lines(text: str | None) -> list[tuple[str, str]]:
    """Every "Label: value" line of a text, in order."""
    return [fact for line in (text or "").splitlines() if (fact := split_fact(line))]


def label_field(label: str | None) -> str | None:
    """What a label states (`price`, `usable_area`, `ownership`, …), or None."""
    return _LABELS.get(_label_key(label))


# --- the labelled facts -----------------------------------------------------------------------


def _label_key(label: str | None) -> str:
    return vocabulary.fold(" ".join((label or "").split()).rstrip(":").strip())


def _labelled(facts: Iterable[tuple[str, str]], portal: str) -> defaultdict[str, list[str]]:
    said: defaultdict[str, list[str]] = defaultdict(list)
    for label, value in facts:
        key, value = _label_key(label), " ".join((value or "").split())
        if not key or not value or key in _IGNORED_LABELS:
            continue
        field = _LABELS.get(key)
        if field is None:
            vocabulary.refuse("label", portal, label.strip().rstrip(":"))
            continue
        said[field].append(value)
    return said


def _first(values: Iterable[T | None]) -> T | None:
    return next((v for v in values if v is not None), None)


def _area(values: list[str]) -> float | None:
    return _first(parse_area_text(v) for v in values)


def _flag(value: str) -> bool | None:
    """"Ano" / "Ne", or a stated size ("3 m²" is a cellar, "0" none)."""
    said = vocabulary.yes_no(value)
    if said is None and re.search(r"\d", value):
        said = vocabulary.present(value)
    return said


def _parking(value: str) -> bool | None:
    said = _flag(value)
    return said if said is not None else vocabulary.parking(value)


def _storeys(said: defaultdict[str, list[str]], category_main: str | None,
             haystack: str) -> tuple[int | None, int | None]:
    """The labelled storey (read with its label, so "Podlaží: 4. NP" converts and a bare
    "Podlaží: 3" stays unread, as `scraper.floor` rules), else the title + prose."""
    floor = total = None
    for value in said["floor"]:
        floor, total = floor_from_text(f"podlaží: {value}", category_main=category_main)
        if floor is not None:
            break
    counts = (re.search(r"\d{1,3}", v) for v in said["total_floors"])
    total = _first(total_floors_from_portal(int(m.group(0))) for m in counts if m) or total
    text_floor, text_total = floor_from_text(haystack, category_main=category_main)
    floor = floor if floor is not None else text_floor
    total = total if total is not None else text_total
    return (floor if is_plausible_floor(floor, total) else None), total


# --- the free-text grammars ---------------------------------------------------------------------


def fold_text(text: str | None) -> str:
    """Diacritics stripped, lower-cased, spacing kept (the category words are matched on it)."""
    plain = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in plain if not unicodedata.combining(c)).lower()


def category_type_of(text: str | None) -> str | None:
    m = _TYPE_RE.search(fold_text(text))
    if not m:
        return None
    return "prodej" if m.group(1) else "pronajem"


def category_main_of(text: str | None) -> str | None:
    folded = fold_text(text)
    hits = [(m.start(), canon) for canon, rule in _MAIN_RES if (m := rule.search(folded))]
    return min(hits)[1] if hits else None


def text_price(text: str | None) -> int | None:
    """The first amount in CZK the text states that no deposit / fee / commission word names;
    a per-m² figure is no price (price_text), `,-` spelling included."""
    if not text:
        return None
    for m in _PRICE_RE.finditer(text):
        group = 1 if m.group(1) else 2
        before = fold_text(text[max(0, m.start(group) - _CUE_WINDOW):m.start(group)])
        cues = list(_PRICE_CUE_RE.finditer(before))
        if cues and cues[-1].group(2):
            continue
        if is_per_area_price(re.sub(r"^,-+", "", text[m.end(group):])):
            return None
        digits = re.sub(r"\D", "", m.group(group))
        return int(digits) if digits and int(digits) > 0 else None
    return None


# --- contacts -------------------------------------------------------------------------------------


def norm_email(value: str | None) -> str | None:
    if not value:
        return None
    email = value.strip().removeprefix("mailto:").split("?", 1)[0].strip().lower()
    return email if _EMAIL_RE.match(email) else None


def norm_phone(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().removeprefix("tel:")
    digits = re.sub(r"\D", "", raw)
    if raw.lstrip().startswith(("+", "00")) and not digits.startswith(("420", "00420")):
        digits = digits.removeprefix("00")
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    digits = digits.removeprefix("00").removeprefix("420")
    return f"+420{digits}" if len(digits) == 9 and digits[0] in "234567" else None


def text_phone(*texts: str | None) -> str | None:
    """The first +420 / nine-digit Czech number the texts state, normalised."""
    for text in texts:
        m = (_PHONE_INTL_RE.search(text) or _PHONE_BARE_RE.search(text)) if text else None
        if m:
            return norm_phone(m.group(1))
    return None


def _email_in(text: str) -> str | None:
    m = _EMAIL_IN_RE.search(text)
    return norm_email(m.group(0)) if m else None

