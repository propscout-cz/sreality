"""A stated price as a number plus its basis, and the ONE conversion to the stored total.

`listings.price_czk` is a CZK TOTAL: the asking price, or the monthly rent (rule 23). Some
adverts state a rate instead: per m², per m² per month, or an amount in EUR. Before #2355 the
detail parsers stored nothing for those while the index card still showed a number, so
`price_changed(None, x)` re-queued the ad on every walk. Now the card reader and the detail
parser both read `PriceReading(amount, currency, basis)` and `resolve` turns it into the total
with the area the platform's per-m² measure divides by (`area_m2`: the plot for pozemek, the
floor area otherwise; migrations 423/425). The card side resolves against the area already
stored for the listing, so a card that matches the stored total reads unchanged and a real
change still refetches.

One reader and one conversion for every portal that states a rate (rule 21: no portal branch
here); the portals that do not yet still refuse a rate (`scraper.price_text.is_per_area_price`).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from scraper.area import AREA_NUMBER_SRC, area_token_to_float

LOG = logging.getLogger(__name__)

TOTAL = "total"
PER_M2 = "per_m2"
PER_M2_MONTH = "per_m2_month"

# THE EUR rate, fixed on purpose (operator ruling 2026-11-05). A daily rate would re-price
# every EUR ad daily. Reviewed quarterly and changed only when the market rate has moved
# more than 0.5 CZK from it; a change is a code change that moves EUR_CZK_RATE_SET with it.
# Every converted price records the rate it used (`ResolvedPrice.record`, kept in raw_json
# and so in every snapshot), and the content hash reads the stated EUR amount
# (`ResolvedPrice.native`), so a rate change re-prices the current row on its next detail
# fetch without a snapshot or a price-change alert.
EUR_CZK_RATE = Decimal("24.5")
EUR_CZK_RATE_SET = "2026-11-05"
_CZK_PER_UNIT: dict[str, Decimal] = {"CZK": Decimal(1), "EUR": EUR_CZK_RATE}
CONVERTIBLE_CURRENCIES: frozenset[str] = frozenset(_CZK_PER_UNIT)
_PRICE_CZK_MAX = 2_147_483_647  # listings.price_czk is a Postgres integer
_AREA_SCALE = Decimal("0.1")


@dataclass(frozen=True)
class PriceReading:
    """What a price cell states: the amount, its currency and what it is per."""

    amount: Decimal
    currency: str = "CZK"
    basis: str = TOTAL


@dataclass(frozen=True)
class ResolvedPrice:
    total_czk: int | None
    # raw_json["price_reading"]: what was stated and how it was read, whenever it was not a
    # plain CZK total. None for a plain total, so those rows' raw_json does not change.
    record: dict[str, Any] | None = None
    # ScrapedListing.price_native: the stated foreign amount, which the content hash reads
    # in place of price_czk.
    native: str | None = None


# --- the reader -----------------------------------------------------------------------------

# The amount is the area grammar's number (scraper.area: one number grammar, rule 21): spaced
# thousands, a decimal comma ("9 990 000,39", "15,5"). Not glued to a word, so the "2" of a
# leading "Cena za m2:" is never the amount.
_AMOUNT_RE = re.compile(rf"(?<![\w.,+])({AREA_NUMBER_SRC})")
_GAP = r"[\s\u200b-\u200d\u2060]"
# Matched against folded text (no diacritics, lower case, so "m²" reads "m2").
_CZK_RE = re.compile(rf"^{_GAP}*(?:,-+{_GAP}*)?(?:kc|czk)(?![a-z])")
_EUR_RE = re.compile(rf"^{_GAP}*(?:eur(?![a-z])|€)")
_FOREIGN_RE = re.compile(rf"^{_GAP}*(?:usd|gbp|chf|pln|huf|[$£])")
_EUR_BEFORE_RE = re.compile(rf"(?:eur|€){_GAP}*$")
_PER_AREA_RE = re.compile(r"(?<![a-z])m\s*2(?!\d)|(?<![a-z])metr")
_MONTH_RE = re.compile(r"mes")
_YEAR_RE = re.compile(r"(?<![a-z])(?:rok|rocn)")
_CURRENCY_TOKEN_RE = re.compile(r"(?<![a-z])(?:kc|czk|eur)(?![a-z])|€")


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).lower()


def _unit_text(rest: str) -> str:
    """The unit right after the currency: a bracket's content, or the text up to the next bracket."""
    rest = re.sub(rf"^{_GAP}*(?:/{_GAP}*)?", "", rest)
    if rest[:1] in ("(", "["):
        end = rest.find(")" if rest[0] == "(" else "]")
        return rest[1:] if end < 0 else rest[1:end]
    end = rest.find("(")
    return rest if end < 0 else rest[:end]


def read_price_text(text: str | None) -> PriceReading | None:
    """The first amount in a price cell with its currency and basis; None when it states none we store.

    The unit is read where the portals put it: right after the currency, bare or bracketed
    and behind a VAT note ("bez DPH, za m²"), or before the amount ("Cena za m2: 7 759").
    A bracket that names a currency is a NOTE beside a total ("4 990 000 Kč (4 008 Kč za
    m²)"), never the unit. A yearly rate and a currency other than CZK or EUR read as None:
    `price_czk` holds neither.
    """
    if not text:
        return None
    match = _AMOUNT_RE.search(text)
    if match is None:
        return None
    prefix, rest = _fold(text[: match.start()]), _fold(text[match.end():])
    currency = "CZK"
    if (czk := _CZK_RE.match(rest)) is not None:
        rest = rest[czk.end():]
    elif (eur := _EUR_RE.match(rest)) is not None:
        currency, rest = "EUR", rest[eur.end():]
    elif _FOREIGN_RE.match(rest):
        return None
    elif _EUR_BEFORE_RE.search(prefix):
        currency = "EUR"
    unit = _unit_text(rest)
    if _CURRENCY_TOKEN_RE.search(unit):
        unit = ""
    marked = unit + " " + prefix
    basis = TOTAL
    if _PER_AREA_RE.search(marked):
        if _YEAR_RE.search(marked):
            return None
        basis = PER_M2_MONTH if _MONTH_RE.search(marked) else PER_M2
    amount = Decimal(repr(area_token_to_float(match.group(1))))
    return PriceReading(amount, currency, basis)


# --- the mislabel bands -----------------------------------------------------------------

@dataclass(frozen=True)
class Ppm2Band:
    """One category's per-m² distribution: the 0.1st, 1st and 99th percentile.

    A reading is IMPOSSIBLE beyond five times the extreme percentile (under p001 / 5, over
    p99 * 5) and NORMAL inside [p01, p99]. The portal's label is overridden only when its
    reading is impossible and the other reading is normal; a monthly rent band is per month.
    """

    p001: float
    p01: float
    p99: float

    @property
    def impossible_low(self) -> float:
        return self.p001 / 5

    @property
    def impossible_high(self) -> float:
        return self.p99 * 5


# Read 2026-11-05 from public.measure_price_per_m2 over every active listing, all sources,
# per (category_main, category_type) with at least 1,000 rows; a category without a band
# trusts the label. Re-derive them the same way; never hand-tune one to make a case pass.
PPM2_BANDS: dict[tuple[str, str], Ppm2Band] = {
    ("byt", "prodej"): Ppm2Band(9_900.00, 22_715.07, 277_222),
    ("byt", "pronajem"): Ppm2Band(27.11, 117.64, 881),
    ("dum", "prodej"): Ppm2Band(831.59, 3_856.97, 218_125),
    ("dum", "pronajem"): Ppm2Band(9.65, 33.62, 600),
    ("komercni", "prodej"): Ppm2Band(259.69, 1_177.51, 220_097),
    ("komercni", "pronajem"): Ppm2Band(2.12, 21.10, 1_558),
    ("ostatni", "prodej"): Ppm2Band(49.39, 838.69, 185_813),
    ("ostatni", "pronajem"): Ppm2Band(1.25, 21.50, 784),
    ("pozemek", "podil"): Ppm2Band(4.07, 7.30, 2_659),
    ("pozemek", "prodej"): Ppm2Band(0.15, 3.41, 19_302),
    ("pozemek", "pronajem"): Ppm2Band(0.10, 0.18, 253),
}


def _basis_used(stated: str, czk: Decimal, area: Decimal | None, category_main: str | None,
                category_type: str | None, label: str) -> str | None:
    """The basis to convert with: the label, unless its reading is impossible (#2355)."""
    band = PPM2_BANDS.get((category_main or "", category_type or ""))
    if band is None or area is None:
        return stated
    low, high = Decimal(repr(band.p01)), Decimal(repr(band.p99))
    used: str | None = stated
    if stated == TOTAL:
        if czk < Decimal(repr(band.impossible_low)) * area and low <= czk <= high:
            used = PER_M2
    elif czk > Decimal(repr(band.impossible_high)):
        used = TOTAL if low * area <= czk <= high * area else None
    if used != stated:
        LOG.info(
            "PRICE basis_override %s stated=%s used=%s amount_czk=%s area_m2=%s category=%s/%s",
            label, stated, used or "none", czk, area, category_main, category_type,
        )
    return used


def resolve(
    reading: PriceReading | None,
    *,
    area_m2: float | None,
    category_main: str | None,
    category_type: str | None,
    label: str = "",
) -> ResolvedPrice:
    """The stored total for a reading: converted to CZK, times the measure's area when per m²."""
    rate = _CZK_PER_UNIT.get(reading.currency) if reading is not None else None
    if reading is None or rate is None:
        return ResolvedPrice(None)
    czk = reading.amount * rate
    # The area as `listings.area_m2` (numeric(7,1)) holds it, so the detail parse and the
    # card, which reads the stored column, multiply by the same number.
    area = (Decimal(repr(area_m2)).quantize(_AREA_SCALE, rounding=ROUND_HALF_UP)
            if area_m2 is not None and area_m2 > 0 else None)
    used = _basis_used(reading.basis, czk, area, category_main, category_type, label)
    total: Decimal | None = None
    if used == TOTAL:
        total = czk
    elif used is not None and area is not None and (
            used == PER_M2 or category_type == "pronajem"):
        total = czk * area
    # Whole crowns, the fraction dropped as the parsers always did, so a stored decimal
    # total ("1 234,50 Kč") does not move by a crown and fire a price change.
    total_czk = int(total.quantize(Decimal(1), rounding=ROUND_DOWN)) if total else None
    record: dict[str, Any] | None = None
    if reading.currency != "CZK" or reading.basis != TOTAL or used != TOTAL:
        record = {"amount": float(reading.amount), "currency": reading.currency,
                  "basis": reading.basis}
        if reading.currency != "CZK":
            record |= {"czk_per_eur": float(rate), "rate_set": EUR_CZK_RATE_SET}
        if used != reading.basis:
            record["read_as"] = used or "none"
    native = None
    if reading.currency != "CZK":
        native = f"{reading.amount.normalize():f} {reading.currency}" + (
            "" if reading.basis == TOTAL else f" {reading.basis}")
    if total_czk is not None and not 0 < total_czk <= _PRICE_CZK_MAX:
        total_czk = None
    return ResolvedPrice(total_czk, record, native)
