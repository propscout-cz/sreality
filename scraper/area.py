"""Single source of truth for the headline `area_m2` and its `area_basis` stamp.

`area_m2` is POLYMORPHIC by design: the interior area for a dwelling
(byt / dum / komercni), the PARCEL for land. Each parser maps its own portal's
labels onto the typed measures (usable / floor / total / plot) and passes whatever
unlabelled figure it has left — a title figure as `fallback`, or, on the portal whose
area is prose alone (bazos), the first figure in the advert's text as `prose`; the
precedence that picks the headline lives HERE so it can never diverge per portal
(rule 21):

    dwelling:  usable -> floor -> total -> fallback -> prose ('unknown')
    pozemek:   plot   -> total -> usable -> floor -> fallback -> prose (always 'plot')

`plot` is the portal's own parcel measure ("plocha pozemku" / `surfaceLand` /
`estate_area`). EVERY parser hands it over, including the six whose land pages
happen to state the parcel under a label that already reached `total` — the
precedence is one rule fed the same way everywhere (rule 21), never a per-portal
arrangement of which argument a parcel arrives in. It leads on land because it is
the labelled parcel, while a land page's "celková plocha" is the same number
under a weaker label. It is NEVER a dwelling's headline: a house's plot sits beside
its floor area in `estate_area` and the dwelling arm does not read it at all.

`area_basis` is a PROVENANCE STAMP — an observation of which physical area the
column already holds. It never changes the value, which is why it stays out of
every content hash: stamping it must not churn a single snapshot (rule 2).

A 0 m2 measure is a form placeholder, never a measurement, so it is skipped the
same way the per-portal `or` chains this replaces skipped it. The same arm
carries the measure's lower validity bound: on byt / dum / komercni a headline
under MIN_AREA_M2 is a parse artifact (a title-number garble, a per-m2 note read
as the area), never a unit, so the resolver declines it and tries the next
measure. MAX_AREA_M2 is the same idea from above, for EVERY category: `area_m2`
is `numeric(7,1)`, so a measure at or beyond 10^6 cannot be stored at all — the
write boundary NULLs it (`scraper.db.sane_listing_numerics`). Declining it HERE
instead means the resolver falls through to the next measure and, crucially,
never stamps a basis for a value the row will not hold; a parcel of 16,809,800 m2
is real on the portal and belongs in `estate_area` (numeric(9,1)), not in the
headline.

The dwelling band (`dwelling_area_band`): a byt stays under MAX_FLAT_AREA_M2, and a
PROSE figure — the `prose` arm, the first m² anywhere in an advert's free text, which is
bazos's whole area (the text lane) — must also be at least N x MIN_AREA_PER_ROOM_M2 for a
stated N+kk / N+1 ON A SALE (`category_type='prodej'`). The per-room floor reads the prose
arm ONLY, never a structured portal's cell or title figure, and never a rental: in both a
small figure beside a big disposition is a ROOM RENTAL's real size ("pronájem pokoje" in a
5+1, ~280 structured rows and 10 of the 25 active bazos rentals under it on production
2026-09-27), while in a sale's prose it is the cellar or a balcony. A measure outside its
band is declined like any other, so the resolver tries the next one and a prose figure that
named the cellar reads as absence. The UNLABELLED figures (`fallback`, `prose`) are held under
MAX_FLAT_AREA_M2 on a house too: a figure of a thousand m² or more that no label calls the
house's own is its parcel, and a wrong area vetoes a true pair where an unknown one does
not.

BOTH bounds live HERE, not at the write boundary, because a refused measure has
to reach the content hash — a value dropped after hashing would leave `listings`
disagreeing with its own newest snapshot forever (rule 2/8).

`parse_area_text` is the other half of the same idea, one level down: the ONE
grammar that turns a portal's area text into a number. It lives beside the
precedence because both are the same rule — what an area IS — and five parsers
each owning a private copy is exactly what rule 21 forbids. It was five copies
until W19, four of them the naive form that matched the first bare digit run before
an `m²` and so
read "5 870 m²" as 870. The fixed grammar is idnes's — proven in production since its
own truncation incident — WIDENED here to the separators the other portals emit: the
narrow no-break space and the thin space idnes never had to handle.

`stated_plot` is the third rule, for the PLOT column: a flat never carries one, and a
commercial unit's "plot" that merely repeats its floor figure is not a plot (general
ruling 3, 2026-09-30; the operator's flat answers, 2026-10-01) — unless the advert's own
description says the figure IS its parcel (2026-10-25: equality alone is not evidence),
read with `PLOT_TEXT_EVIDENCE_SRC`, which migration 671 carries verbatim. It runs at the
contract boundary — `ScrapedListing.__post_init__` for every portal that crosses it, and
sreality's `parse_listing` itself — so the declined value reaches the content hash the
way every other refused measure does.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# The separators a Czech portal renders between a number's digit groups: ordinary
# space, NBSP, narrow NBSP, thin space, and the zero-width joiners idnes emits
# inside a rendered figure. One class, because there is one grammar (rule 21).
AREA_THOUSANDS_SEPS = "\u0020\u00a0\u202f\u2009\u200b\u200c\u200d\u2060"

# An area token immediately before "m2" / "m²" / "m 2". The FIRST alternative is the
# whole point: it accepts the Czech spaced-thousands form ("5 870 m²") that every
# portal renders, and without it `search` starts INSIDE the number and truncates
# 5870 -> 870 (idnes 8k+ rows in 2026-08; ceskereality 17,207 + realitymix 13,164 in
# W19's measurement). The lookbehind keeps the grouped form from swallowing the digit
# in front of it — a disposition ("3+1 174 m²" stays 174, never 1174), another
# number, or a decimal tail — while still allowing a match to START at a real
# number's first digit.
# The NUMBER half, kept apart so any later reader of a figure builds on this shape
# instead of a second grammar (rule 21). Three forms, in order:
# spaced thousands ("5 870 m²", with an optional decimal tail), DOTTED thousands — a
# dot followed by exactly three digits, the way brokers key "1.910 m2" / "12.100 m2" /
# "1.994,71 m²" (Czech decimals take a comma, so a 3-digit group after a dot is never a
# fraction; "1.5 m2" keeps its dot as the decimal it is) — and a plain number.
# Spelled in ASCII digits and escaped separators because Postgres reads the same text:
# migration 671 carries it verbatim inside PLOT_TEXT_EVIDENCE_SRC, and `\d` would be
# Unicode digits to Python's `re` but ASCII to Postgres.


def _escaped(chars: str) -> str:
    """`chars` as `\\uXXXX` escapes, which Python's `re` and Postgres's regex both read."""
    return "".join(f"\\u{ord(char):04x}" for char in chars)


AREA_THOUSANDS_SEPS_SRC = _escaped(AREA_THOUSANDS_SEPS)
AREA_NUMBER_SRC = (
    rf"[0-9]{{1,3}}(?:[{AREA_THOUSANDS_SEPS_SRC}][0-9]{{3}})+(?:[.,][0-9]+)?"
    rf"|[0-9]{{1,3}}(?:\.[0-9]{{3}})+(?:,[0-9]+)?"
    rf"|[0-9]+(?:[.,][0-9]+)?"
)
AREA_DOTTED_THOUSANDS_SRC = r"^[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]+)?$"
_DOTTED_THOUSANDS_RE = re.compile(AREA_DOTTED_THOUSANDS_SRC)
AREA_TEXT_RE = re.compile(
    rf"(?<![\d+.,])"
    rf"({AREA_NUMBER_SRC})"
    rf"\s*m(?:2|²|\s*2)\b",
    re.IGNORECASE,
)


def _area_token_text(token: str) -> str:
    """One matched number token as a plain decimal: separators stripped, a
    dotted-thousands token's dots with them, the decimal comma read as a point."""
    for sep in AREA_THOUSANDS_SEPS:
        token = token.replace(sep, "")
    if _DOTTED_THOUSANDS_RE.match(token):
        token = token.replace(".", "")
    return token.replace(",", ".")


def area_token_to_float(token: str) -> float:
    """One matched number token -> its value (see `_area_token_text`)."""
    return float(_area_token_text(token))


def parse_area_text(text: str | None) -> float | None:
    """The first area figure in `text`, in m² — or None when it carries none.

    Requires the unit, so a per-m² price ("Cena za m2: 7 759 CZK") reads as no area
    at all rather than as its own number: the digits sit AFTER the unit there.
    """
    if not text:
        return None
    match = AREA_TEXT_RE.search(text)
    if not match:
        return None
    return area_token_to_float(match.group(1))


@dataclass(frozen=True, slots=True)
class PortalAreas:
    """Every area column one detail page yields, plus the headline the rule picked.

    ONE shape for every portal, so a portal's key precedence — WHICH of its spec cells is
    the usable measure, which is the parcel, in what order — can be spelled once and read
    twice: by `parse_detail` on a live fetch, and by the W19 heal re-deriving the same
    columns from `listings.raw_json`, which holds that parser's own latest reading of the
    live page. Two copies of a key order is the same defect as two copies of the number
    grammar, one level up (rule 21).

    CONSTRUCTION IS ALSO THE VALIDITY BOUND FOR THE THREE SIDE COLUMNS, and it has to be,
    because it is the last point BEFORE the content hash. `derive_headline_area` already
    declines an out-of-range `area_m2` for exactly that reason (see the module docstring);
    `usable_area` / `estate_area` / `garden_area` had no such gate, so a value at or beyond
    `numeric(9,1)`'s ceiling — or a 0 m² form placeholder — was hashed as itself and then
    NULLed at the write boundary by `scraper.db.sane_listing_numerics`, leaving `listings`
    permanently disagreeing with its own newest snapshot (rules 2/8). Bounding here makes
    the row and its snapshot agree by construction; `sane_listing_numerics` stays the last
    guard for the parsers that do not build a `PortalAreas` (sreality, bezrealitky).
    """

    area_m2: float | None = None
    area_basis: str | None = None
    usable_area: float | None = None
    estate_area: float | None = None
    garden_area: float | None = None

    def __post_init__(self) -> None:
        for column in ("usable_area", "estate_area", "garden_area"):
            value = getattr(self, column)
            if value is not None and not 0.0 < value < MAX_SIDE_AREA_M2:
                object.__setattr__(self, column, None)


AREA_BASES: frozenset[str] = frozenset({"usable", "floor", "total", "plot", "unknown"})

LAND_CATEGORIES: frozenset[str] = frozenset({"pozemek"})

# Where a sub-MIN_AREA_M2 headline cannot be a real measurement. `ostatni` (a
# 3 m2 cellar, a 2 m2 parking bay) and an unclassified row are deliberately NOT
# bounded, and neither is land: 202 active parcels really are under 5 m2.
BOUNDED_CATEGORIES: frozenset[str] = frozenset({"byt", "dum", "komercni"})
MIN_AREA_M2 = 5.0

# The same bound scaled by what the advert itself states, for a PROSE figure only: no
# flat or house of N rooms is under 8 m2 a room, so below it the first "m2" in bazos's
# prose is a PART of the unit — the cellar ("sklepní kóje o velikosti 2 m²", Mechová
# 3+1), a balcony or one room ("pokoj 13 m²" in a 76 m² 3+1); 32 bazos rows of the
# 40,514 A4 listings (2026-09-26). A structured portal's figure is NOT held to it: on
# production (2026-09-27) the band hit ~280 structured rows (ceskereality 93,
# idnes 120, bezrealitky 39, realitymix 12, sreality 16), and the hand-read found ROOM
# RENTALS listed under the whole flat's disposition — "pronájem bytu 5+1 a více" at
# 11-38 m², "pronájem pokoje 20 m² ve sdíleném bytě 3+1" — the room's real size. Nor is a
# bazos RENTAL: of the 247 bazos pronajem rows under it (25 active, 2026-09-27) the hand-read
# found room rentals ("pronájem pokoje 20m2 ve sdíleném bytě 3+1", 18625955, 18850769) beside
# genuine defects (2+kk 5 m², 1+1 6 m²) the rule cannot tell apart, so it holds on a SALE
# alone (PER_ROOM_CATEGORY_TYPE). A flat bound on houses instead (dum under 20 m²) would
# take 133 real chaty.
MIN_AREA_PER_ROOM_M2 = 8.0
PER_ROOM_CATEGORY_TYPE = "prodej"
ROOMED_CATEGORIES: frozenset[str] = frozenset({"byt", "dum"})
_ROOMS_RE = re.compile(r"^([1-9])\+(?:kk|1)$")

# And from above, for a FLAT only: no Czech flat reaches 1,000 m², and every stored one
# that does is a project's site area or a keyed typo (23,000 / 5,989 / 4,095 / 1,800).
# The same ceiling holds for a HOUSE's unlabelled fallback (bazos's first prose m², a
# structured portal's title figure): at 1,000 m² or more that figure is the parcel —
# bazos 186747 "pozemku o celkové výměře 1.139m²", 204860 "Zahrádka o celkové výměře
# 1.256 m2" beside a 24 m² chata. Measured on the 40,514 A4 rows (2026-09-26): 21 bazos
# dum adverts whose first figure is a dotted-thousands parcel and 157 stored bazos dum
# headlines of 1,000 m² or more; every structured dum at or above it is a LABELLED
# measure (usable_area equal to area_m2), which this ceiling does not touch.
MAX_FLAT_AREA_M2 = 1_000.0

# The first value `listings.area_m2` (numeric(7,1)) cannot store. Kept equal to
# `scraper.db._NUMERIC_ABS_MAX["area_m2"]` — which is what NULLs an out-of-range
# value at the write boundary — by `tests/scraper/test_area.py`, so the two can
# never drift; spelled here rather than imported because this module is
# stdlib-only and must not pull in psycopg.
MAX_AREA_M2 = 1_000_000.0

# The same idea for the three SIDE columns, which are `numeric(9,1)`. Kept equal
# to `scraper.db._NUMERIC_ABS_MAX["estate_area"]` (= usable_area = garden_area)
# by `tests/scraper/test_area.py`, and applied in `PortalAreas.__post_init__` —
# see the class docstring for why the bound has to sit BEFORE the hash.
MAX_SIDE_AREA_M2 = 100_000_000.0


def dwelling_area_band(category_main: str | None,
                       disposition: str | None = None) -> tuple[float, float]:
    """The [low, high) band a dwelling headline must sit in to be the unit's own area;
    `disposition` raises the floor per room, which `derive_headline_area` does for its
    `prose` arm alone."""
    low = MIN_AREA_M2 if category_main in BOUNDED_CATEGORIES else 0.0
    rooms = _ROOMS_RE.match(disposition or "") if category_main in ROOMED_CATEGORIES else None
    if rooms:
        low = max(low, MIN_AREA_PER_ROOM_M2 * int(rooms.group(1)))
    high = MAX_FLAT_AREA_M2 if category_main == "byt" else MAX_AREA_M2
    return low, high


def derive_headline_area(
    *,
    category_main: str | None,
    usable: float | None = None,
    floor: float | None = None,
    total: float | None = None,
    plot: float | None = None,
    fallback: float | None = None,
    prose: float | None = None,
    disposition: str | None = None,
    category_type: str | None = None,
) -> tuple[float | None, str | None]:
    """Return (area_m2, area_basis) for one listing. See module docstring.

    `fallback` is a structured portal's unlabelled figure (its title); `prose` is the first
    figure in an advert's free text, and `disposition` scales the band of `prose` alone, on
    a sale (`category_type` PER_ROOM_CATEGORY_TYPE) alone.
    """
    if category_main in LAND_CATEGORIES:
        # A parcel has no interior: whichever measure the page carried IS the plot.
        # `plot` leads because it is the parcel under its own label; `total` follows
        # because a land page's "celková plocha" is that same parcel, and a stray
        # "užitná plocha" on one is a mislabel of it again.
        for value in (plot, total, usable, floor, fallback, prose):
            if value and value < MAX_AREA_M2:
                return value, "plot"
        return None, None
    min_m2, max_m2 = dwelling_area_band(category_main)
    prose_min, _ = dwelling_area_band(
        category_main, disposition if category_type == PER_ROOM_CATEGORY_TYPE else None)
    unlabelled_max = (min(max_m2, MAX_FLAT_AREA_M2)
                      if category_main in ROOMED_CATEGORIES else max_m2)
    for value, basis, low, high in (
        (usable, "usable", min_m2, max_m2),
        (floor, "floor", min_m2, max_m2),
        (total, "total", min_m2, max_m2),
        (fallback, "unknown", min_m2, unlabelled_max),
        (prose, "unknown", prose_min, unlabelled_max),
    ):
        if value and low <= value < high:
            return value, basis
    return None, None


# A flat never carries a parcel of its own (operator, 2026-10-01). On production every
# flat "plot" the hand-read opened was the floor figure again (ceskereality: 987 of its
# 3,185 active flat plots), the placeholder 1, or the whole building's parcel — never
# the unit's land; the reference portal, sreality, publishes none on a flat at all.
PLOT_FREE_CATEGORIES: frozenset[str] = frozenset({"byt"})

# A commercial unit's plot that equals its usable measure, or a headline the page
# LABELLED an interior measure, is the floor area typed into the plot box (general
# ruling 3, 2026-09-30: 1,383 ceskereality, 651 idnes, 427 realitymix, 53 mmreality
# active rows; on all 14 live pages opened the portal itself prints one figure in the
# plot box and the floor-area box). NOT compared: a title / prose fallback (`unknown` —
# "Prodej areálu 3 400 m²" IS the site, and the real parcel would be dropped), a
# zastavěná plocha figure for equality (a building may really cover its whole parcel), and
# a house's plot (the operator kept the dum rule as it is: a garage's parcel really is its
# footprint).
PLOT_ECHO_CATEGORIES: frozenset[str] = frozenset({"komercni"})
PLOT_ECHO_BASES: frozenset[str] = frozenset({"usable", "floor", "total"})

# The scale the side columns store (numeric(9,1)): two figures that read back equal ARE
# equal, whatever their float tails; half up, as Postgres rounds on the write.
_SIDE_AREA_QUANTUM = Decimal("0.1")
_HECTARE_M2 = Decimal(10_000)

# But EQUALITY ALONE IS NOT EVIDENCE (operator, 2026-10-25): an areál, farm, camp or petrol
# station whose TRUE parcel the advertiser typed into both boxes looks exactly like an echo.
# The advert's own text tells them apart, so the echo branch reads it: a parcel statement
# ("pozemek" / "parcela" in any case form, "plocha pozemku", ...) or a built-up statement
# ("zastavěná plocha" / "zastavěné plochy", ...), then at most 40 characters, then a figure in
# m² or ha. The 40 never cross a sentence end, a bullet, another statement or an earlier
# figure, so the figure read is the statement's own, whichever engine reads it — this text
# is ALSO migration 671's (Python's `re` and Postgres's regex read it identically; no `\b`,
# `\s`, `\d` or case flag, whose meanings differ between the two). Groups: the statement's
# stem, the number (AREA_NUMBER_SRC), the unit.
_UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
_LOWER = "a-záčďéěíňóřšťúůýž"
_GAP = "[" + _escaped(" \t\n\r" + chr(0x00A0) + chr(0x202F) + chr(0x2009)) + "]"
_LINE_BREAK = "[" + _escaped("\n\r") + "]"
_UNIT_SRC = rf"[mM](?:²|{_GAP}?2)|[hH][aA]|[hH]ektar[{_LOWER}]*"
_FIGURE_SRC = rf"(?:{AREA_NUMBER_SRC}){_GAP}*(?:{_UNIT_SRC})(?![0-9{_UPPER}{_LOWER}])"
_STATEMENT_SRC = r"[Pp]ozem|POZEM|[Pp]arcel|PARCEL|[Zz]astav[ěe]n|ZASTAV[ĚE]N"
PLOT_TEXT_EVIDENCE_SRC = (
    rf"(?<![{_UPPER}{_LOWER}])({_STATEMENT_SRC})[{_UPPER}{_LOWER}]*"
    rf"(?:(?!{_FIGURE_SRC}|{_STATEMENT_SRC}|[.]{_GAP}*[{_UPPER}]|{_LINE_BREAK}{_GAP}*[{_UPPER}])"
    rf"[^•|;!?]){{0,40}}"
    rf"({AREA_NUMBER_SRC}){_GAP}*({_UNIT_SRC})(?![0-9{_UPPER}{_LOWER}])"
)
_PLOT_TEXT_EVIDENCE_RE = re.compile(PLOT_TEXT_EVIDENCE_SRC)


def _at_side_scale(value: float | None) -> Decimal | None:
    """None for an absent, non-finite or unstorable figure, which is never compared (a
    plot like that passes through to the writer's numeric guard, as it did before)."""
    if value is None or not math.isfinite(value) or abs(value) >= MAX_SIDE_AREA_M2:
        return None
    return Decimal(str(value)).quantize(_SIDE_AREA_QUANTUM, rounding=ROUND_HALF_UP)


def _text_figure(number: str, unit: str) -> Decimal | None:
    """A stated figure in m² at column scale, exactly as migration 671 reads it (decimal
    arithmetic, ha x 10,000); None when no stored plot could equal or exceed it."""
    figure = Decimal(_area_token_text(number)) * (_HECTARE_M2 if unit[0] in "hH" else 1)
    if figure >= MAX_SIDE_AREA_M2:
        return None
    return figure.quantize(_SIDE_AREA_QUANTUM, rounding=ROUND_HALF_UP)


def _text_confirms_plot(plot: Decimal, text: str | None) -> bool:
    """Whether the advert's text makes a plot-box figure that equals the floor figure a
    parcel after all: (a) it states a parcel EQUAL to it; else (c) a parcel it states
    that differs means the box echoes the floor; else (b) a built-up area SMALLER than
    it means the figure is a parcel; else (d) no statement, an echo."""
    parcel_stated = built_up_smaller = False
    for match in _PLOT_TEXT_EVIDENCE_RE.finditer(text or ""):
        figure = _text_figure(match[2], match[3])
        if match[1][0] in "Zz":
            built_up_smaller = built_up_smaller or (figure is not None and figure < plot)
            continue
        if figure == plot:
            return True
        parcel_stated = True
    return built_up_smaller and not parcel_stated


def stated_plot(
    category_main: str | None,
    plot: float | None,
    *,
    usable: float | None = None,
    headline: float | None = None,
    headline_basis: str | None = None,
    text: str | None = None,
) -> float | None:
    """The parcel a page STATES, or None when its plot box holds no plot: never on a byt;
    on komerční not when it equals, at column scale, the usable measure or a headline
    whose basis is a labelled interior measure (PLOT_ECHO_BASES) — unless the advert's
    own `text` (its description) says the figure is a parcel (`_text_confirms_plot`)."""
    if plot is None or category_main in PLOT_FREE_CATEGORIES:
        return None
    if category_main not in PLOT_ECHO_CATEGORIES:
        return plot
    stated = _at_side_scale(plot)
    if stated is None:
        return plot
    echoes = _at_side_scale(usable) == stated or (
        headline_basis in PLOT_ECHO_BASES and _at_side_scale(headline) == stated)
    if not echoes or _text_confirms_plot(stated, text):
        return plot
    return None
