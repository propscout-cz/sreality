"""Shared Czech floor grammar: ONE storey scale, ground = 0, on all nine portals.

`listings.floor` means the same storey whatever portal wrote it: **přízemí = 0,
1. patro = 1, 1. NP = 0, suterén = −1**. Ground = 0 is not a preference — it is the
only convention anything in this repo ever DECLARED (this module and the retired LLM
URL parser's prompt); ground = 1 was undeclared portal
passthrough on six portals until W8, and the column was a ~50/50 mix of the two
(the same flat read 3 from its idnes row and 4 from its sreality row).

**A portal's convention is contract DATA, not a per-parser branch.** Each
`scraper.attribute_contract` floor cell declares which scale its source key carries:

  * `ground0` — the key counts from the ground storey (ceskereality's `patro`).
  * `ground1` — the key counts the STOREY ORDINAL, 1 = ground (every `podlaží`-named
    key: sreality `floor_number`, bezrealitky `etage`, mmreality `floor`, remax
    `cislo podlazi`, realitymix `číslo podlaží v domě`, maxima `podlaží`).
  * `word` — the VALUE states its own convention in Czech ("2. patro (3. NP)"), so the
    number alone is meaningless and the grammar below reads the word.

`floor_from_portal` is the one converter and it REFUSES a bare integer whose cell
declares no convention: reading a number off a key whose Czech meaning nobody wrote
down is exactly how ceskereality's `patro` and realitymix's `podlaží` — one storey
apart — ended up in a single bare-int reader.

`normalize_floor` is the value-side grammar (the word wins wherever a value carries
one, on every portal). `floor_from_text` is the free-text miner built on it (bazos and
`web`, the portals with no structured floor field): HIGH-PRECISION only — it fires on
explicit unit-floor cues and defers the ambiguous tail (mezonet/loft, bare 'suterén' in
prose). For a FLAT it also reads a bare 'N. podlaží' and a spelled-out ordinal ('v šestém
podlaží', 'čtvrté patro'), and reads 'přízemí' as 0 only when the text ties it to the flat
('byt v přízemí', 'přízemní byt') and names no other storey: an advert says where its
cellar, pram room or entrance is, and 'sklep v přízemí domu' put 373 sixth-floor bazos
flats on the ground storey. The chief trap is the BUILDING total leaking in
as the unit's floor ("v 6. patře šestipodlažního domu", "z celkových 10 pater"): the
unit floor uses the positional-ordinal NOUN ('6. patře'), the building total the
ADJECTIVAL form ('šestipodlažní') or an explicit 'celkem'/'z celkových' phrase —
lexically separable, so the miner captures the former and reads the latter only as
`total_floors`, then drops any floor that is not below the stated total.

`total_floors` is a PODLAŽÍ COUNT on every portal (ground storey included), so a
building's top storey is `total_floors - 1` under ground = 0 — which is what
`is_plausible_floor` enforces. That count convention is also why the two patra-worded
total cues below are read as `n + 1`: "třípatrový dům" / "z celkových 3 pater" is three
storeys ABOVE the ground one, i.e. four podlaží.

Text is diacritics-folded before matching (přízemí==prizemi, patře==patre), so a
listing that drops accents reads the same; patterns are written folded/ASCII.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from unicodedata import combining, normalize

FloorConvention = Literal["ground0", "ground1", "word"]

# Canonical ground floor = 0; plausibility window for a residential storey.
_FLOOR_MIN, _FLOOR_MAX = -3, 40


def _strip_marks(text: str) -> str:
    return "".join(c for c in normalize("NFD", text) if not combining(c))


def _fold(text: str) -> str:
    return _strip_marks(text).lower()


# N. NP (nadzemní podlaží, 1-indexed from ground): k.NP -> k-1.
_NP_RE = re.compile(r"(\d{1,2})\.\s*(?:np|nadzemni\w*\s+podlazi)\b")
# N. PP (podzemní podlaží, below ground): k.PP -> -k.
_PP_RE = re.compile(r"(\d{1,2})\.\s*(?:pp|podzemni\w*\s+podlazi)\b")
# N. patro/patra/patře/patrem (ground=0 relative): k -> k. Noun forms only — the
# building ADJECTIVE 'patrový' (patr+ovy) is excluded by the trailing \b stopping
# at its 'v', and the genitive-plural building total 'pater' (pat+er) lacks the
# 'patr' stem entirely. The sign is captured: idnes spells a basement '-1. patro'.
_PATRO_NOUN = r"(?:patr[oau]|patre|patrem)\b"
# The sign may not be the hyphen of a storey RANGE: in "1.-2. patro" (a maisonette)
# the '-' separates two ordinals, so a bare `-?` read it as -2 = suterén.
_PATRO_RE = re.compile(rf"(?<![\d.])(-?\d{{1,2}})\.\s*{_PATRO_NOUN}")
# přízemí (ground) -> 0; 'zvýšené/snížené přízemí' is still the ground storey.
_PRIZEMI_RE = re.compile(r"prizem")
# suterén (basement) -> -1. NOT 'sklep' (a cellar the flat HAS, not the storey it
# is ON), so the bare token is only trusted from a structured floor field.
_SUTEREN_RE = re.compile(r"suteren")
# The number a bare-int cell carries, sign included: ceskereality writes "2.".
_BARE_INT_RE = re.compile(r"-?\d+")
# Any storey WORD at all. A value carrying one has already spoken for itself, so when
# the grammar reads it and refuses it (out of band) the key's convention must not get a
# second go at the bare number — "45. patro" is not 44 under any reading.
_WORD_CUE_RE = re.compile(
    rf"prizem|suteren|\d{{1,2}}\.\s*(?:np|pp|nadzemni|podzemni|{_PATRO_NOUN})")


def _bounded(value: int) -> int | None:
    return value if _FLOOR_MIN <= value <= _FLOOR_MAX else None


def is_plausible_floor(floor: int | None, total_floors: int | None) -> bool:
    """A residential storey in a sane band and not above the building's top one.

    The shared guard for any floor write. `total_floors` counts PODLAŽÍ including the
    ground storey, so under ground = 0 the top storey is `total_floors - 1`: a floor at
    or above the count itself is a misread building number, not a flat.
    """
    if floor is None:
        return False
    if not (_FLOOR_MIN <= floor <= _FLOOR_MAX):
        return False
    if total_floors is not None and floor > total_floors - 1:
        return False
    return True


def normalize_floor(text: str | None) -> int | None:
    """One Czech floor expression -> canonical storey (ground = 0), or None.

    Recognizes přízemí, N. patro, N.NP, N.PP, suterén (diacritics-insensitive).
    Returns None for a bare integer (no keyword = no convention to read) or any
    unrecognized form, so a caller never silently reads an NP value as a patro.

    přízemí is read FIRST because idnes files 'snížené přízemí' as '1. PP': the half-sunk
    ground storey is the one the advert names, not the basement its parenthetical numbers
    it as, and letting PP win would move those flats to -1 (= suterén).
    """
    if not text:
        return None
    folded = _fold(text)
    if _PRIZEMI_RE.search(folded):
        return 0
    m = _PATRO_RE.search(folded)
    if m:
        return _bounded(int(m.group(1)))
    m = _NP_RE.search(folded)
    if m:
        return _bounded(int(m.group(1)) - 1)
    m = _PP_RE.search(folded)
    if m:
        return _bounded(-int(m.group(1)))
    if _SUTEREN_RE.search(folded):
        return -1
    return None


def floor_from_portal(convention: FloorConvention | None, value: Any) -> int | None:
    """A portal's stated floor -> the canonical storey, under its DECLARED convention.

    A value carrying a Czech storey word is read through `normalize_floor` whatever the
    key's convention is — the word is the portal speaking about that one advert, and the
    convention only says how to read a number with no word on it. `ground1` shifts a
    positive ordinal down by one; 0 and negatives are left alone (sreality emits BOTH 0
    and 1 for the ground storey, and -1 is suterén under either reading, so a blanket
    decrement would invent basements).

    Raises on an undeclared convention rather than guessing: a bare integer whose Czech
    meaning is written down nowhere is the defect this function exists to end. A number
    outside the storey band is absence on either scale, exactly as the word arm already
    reads it: sreality's 161 and realitymix's 1,002 were typed, not climbed.
    """
    if convention is None:
        raise ValueError(
            "floor_from_portal: the contract cell declares no convention — a bare "
            "storey number cannot be read without one (ground0 | ground1 | word)")
    text = value if isinstance(value, str) else None
    if text is not None:
        spelled = normalize_floor(text)
        if spelled is not None:
            return spelled
        if _WORD_CUE_RE.search(_fold(text)):
            return None
    if convention == "word":
        return None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif text is not None and (m := _BARE_INT_RE.search(text)):
        number = int(m.group(0))
    else:
        return None
    return _bounded(number - 1 if convention == "ground1" and number >= 1 else number)


def total_floors_from_portal(value: int | None) -> int | None:
    """A stated building PODLAŽÍ count, or None outside the storey band.

    The one band every structured parser and the text lane read a count through, the
    twin of the text miner's `1 <= n <= _FLOOR_MAX`: a count of 0 is mmreality's "not
    stated", and 113 / 731,463,379 are a typo and an id (bezrealitky)."""
    if value is None or isinstance(value, bool):
        return None
    return value if 1 <= value <= _FLOOR_MAX else None


# --- Free-text miner ---------------------------------------------------------

# Building-total cues -> total_floors, as a PODLAŽÍ count (ground storey included).
# 'Podlaží celkem: 6' and the digit adjectival '6-podlažní' / '6podlažní' already count
# podlaží; the patra-worded cues ('z celkových 10 pater', '6patrový') count the storeys
# ABOVE the ground one, so they are +1. Word-number adjectivals ('šestipodlažní') carry
# no digit and are intentionally left unparsed — they also never match the unit-floor
# patterns, so the trap is closed without reading them.
_TOTAL_LABEL_RE = re.compile(r"podlazi\s+celkem\s*[:\-]?\s*(\d{1,2})")
_TOTAL_CELKEM_PATER_RE = re.compile(r"z\s+celkov\w+\s+(\d{1,2})\s+pater")
_TOTAL_ADJ_PODLAZI_RE = re.compile(r"(\d{1,2})\s*-?\s*podlazni\w*")
_TOTAL_ADJ_PATRO_RE = re.compile(r"(\d{1,2})\s*-?\s*patrov\w*")
_TOTAL_CUES: tuple[tuple[re.Pattern[str], int], ...] = (
    (_TOTAL_LABEL_RE, 0), (_TOTAL_CELKEM_PATER_RE, 1),
    (_TOTAL_ADJ_PODLAZI_RE, 0), (_TOTAL_ADJ_PATRO_RE, 1),
)

# A spec-style 'Podlaží: <expr>' label: the value is read through normalize_floor
# so its embedded NP/patro form ('Podlaží: 4. NP') is converted correctly. A bare
# 'Podlaží: 7' with no NP/patro keyword yields None (the integer's convention is
# unknowable from the label alone) and is left to the text lane. 'Podlaží celkem:' has a
# word between 'podlazi' and the colon, so it never matches this floor label.
# The capture stops at a comma/semicolon: it routinely runs past the value into the next
# clause, and a 'přízemí' three words later would otherwise beat the label's own '3. NP'.
_FLOOR_LABEL_RE = re.compile(r"\bpodlazi\s*[:\-]\s*([^\n,;]{1,30})")
# A numeric unit-floor expression, optionally bound by a leading preposition.
_FLOOR_EXPR = rf"\d{{1,2}}\.\s*(?:np|nadzemni\w*\s+podlazi|pp|podzemni\w*\s+podlazi|{_PATRO_NOUN})"
_FLOOR_PREP_RE = re.compile(rf"\b(?:v|ve|na)\s+({_FLOOR_EXPR})")
_FLOOR_BARE_RE = re.compile(rf"\b({_FLOOR_EXPR})")
_PRIZEMI_TEXT_RE = re.compile(r"\bprizem")

# --- the flat-only readers ----------------------------------------------------
# A flat's storey is its own attribute, so a flat text gets the wider grammar below. Every
# other category keeps the legacy miner above unchanged: a house's 'v přízemí kuchyň, ve
# druhém patře ložnice' names rooms, not the advert's storey, and re-reading 18k such rows
# would only churn snapshots.
_FLAT_CATEGORIES: frozenset[str | None] = frozenset({None, "byt"})

# A bare 'N. podlaží' is a storey ORDINAL (ground = 1), the scale of every podlaží key.
_PODLAZI_RE = re.compile(r"(\d{1,2})\.\s*podlazi(?:m)?\b")
_FLAT_EXPR = rf"\d{{1,2}}\.\s*(?:podlazi(?:m)?\b|np|nadzemni\w*\s+podlazi|pp|podzemni\w*\s+podlazi|{_PATRO_NOUN})"
_FLAT_PREP_RE = re.compile(rf"\b(?:v|ve|na)\s+({_FLAT_EXPR})")
_FLAT_BARE_RE = re.compile(rf"\b({_FLAT_EXPR})")

# Spelled-out ordinals up to the tenth, every case ending folded ('šestém' -> 'sestem').
_ORDINAL_STEMS: dict[str, int] = {
    "prvn": 1, "druh": 2, "tret": 3, "ctvrt": 4, "pat": 5,
    "sest": 6, "sedm": 7, "osm": 8, "devat": 9, "desat": 10,
}
_ORDINAL = rf"({'|'.join(_ORDINAL_STEMS)})(?:i|y|e|a|im|em|ym|iho|eho|ou)"
_STOREY_NOUN = rf"({_PATRO_NOUN}|podlazi(?:m)?\b)"
# 'v šestém (nadzemním) podlaží' takes one word between, as the measurement read it; the
# nominative 'čtvrté patro' takes none, so a stray ordinal three words off never binds.
_ORDINAL_PREP_RE = re.compile(rf"\b(?:v|ve|na)\s+{_ORDINAL}\s+(?:(\w+)\s+)?{_STOREY_NOUN}")
_ORDINAL_BARE_RE = re.compile(rf"\b{_ORDINAL}\s+{_STOREY_NOUN}")

# A storey cue the readers above do not convert ('v 6 podlaží', 've 3. poschodí', 'patro:
# 4'). It reads no number; it only forbids the přízemí fallback, which would be a guess
# against what the advert says.
_UNREAD_STOREY_RE = re.compile(
    rf"\b(?:v|ve|na)\s+\d{{1,2}}\s+(?:{_PATRO_NOUN}|podlazi|poschodi)"
    rf"|\d{{1,2}}\.\s*poschodi|\b{_ORDINAL}\s+(?:\w+\s+)?poschodi|\bpatro\s*:?\s*\d")

# přízemí tied to the flat: 'přízemní byt', 'Patro: přízemí', or 'v (zvýšeném) přízemí'
# whose own sentence names the unit ('byt', '2+kk', 'jednotka', 'nachází se') and no room
# or shared space of the house ('sklep', 'kočárkárna', 'vchod') right before it.
# 'umístěn' is no unit word: 'garáž i stání jsou umístěny v přízemí'.
_PRIZEMNI_UNIT_RE = re.compile(
    r"\bprizemn\w*[,\s]+(?:\S+\s+){0,2}?(?:byt|bytov\w*\s+jednotk|jednotk|garsonier|garsonk)")
_PATRO_LABEL_PRIZEMI_RE = re.compile(r"\bpatro\s*[:\-]\s*(?:zvysen\w*\s+|snizen\w*\s+)?prizem")
_V_PRIZEMI_RE = re.compile(r"\b(?:v|ve)\s+(?:zvysen\w*\s+|snizen\w*\s+)?prizem", re.I)
_UNIT_WORD_RE = re.compile(
    r"(?:byt(?:u|em|y|ech)?|jednotk\w*|garsonier\w*|garsonk\w*|nachazi|nachazejic\w*"
    r"|situovan\w*|\d\+(?:kk|\d))")
# 'Byt není v přízemí' is a selling point, not a storey.
_NEGATION_RE = re.compile(r"(?:neni|nejsou|nebude)")
_AMENITY_WORD_RE = re.compile(
    r"(?:sklep|kolarn|kocarkarn|garaz|susarn|pradeln|spolecn|vchod|parkovan|kotel|dilen|dilna"
    r"|komor|sklad|obchod|kancelar|provozovn|restaurac|ordinac|prodejn|lekarn|schrank)")
# A sentence ends at a stop before a capital, so 'o už. ploše' or 'ul. Družby' runs on; the
# unit may sit a dozen words back ('byt 2+1 o už. ploše 62 m² ve zvýšeném přízemí'), the
# room that owns the přízemí sits right before it ('sklep se nachází v přízemí').
_SENTENCE_BREAK_RE = re.compile(r"[!?\n]|[.;]\s+(?=[A-Z])")
_UNIT_WINDOW, _AMENITY_WINDOW = 12, 4


def _parse_total_floors(folded: str) -> int | None:
    for rx, above_ground in _TOTAL_CUES:
        m = rx.search(folded)
        if m:
            value = int(m.group(1)) + above_ground
            if 1 <= value <= _FLOOR_MAX:
                return value
    return None


def _unit_storey(expr: str) -> int | None:
    m = _PODLAZI_RE.fullmatch(expr.strip())
    if m:
        return _bounded(int(m.group(1)) - 1)
    return normalize_floor(expr)


def _ordinal_storey(m: re.Match[str], *, between: str | None) -> int | None:
    n = _ORDINAL_STEMS[m.group(1)]
    noun = m.group(m.lastindex or 0)
    if between and between.startswith("podzemn"):
        return _bounded(-n)
    return _bounded(n - 1 if noun.startswith("podlazi") else n)


def _spelled_storey(folded: str) -> int | None:
    m = _ORDINAL_PREP_RE.search(folded)
    if m:
        return _ordinal_storey(m, between=m.group(2))
    m = _ORDINAL_BARE_RE.search(folded)
    if m:
        return _ordinal_storey(m, between=None)
    return None


def _flat_on_ground(folded: str, cased: str) -> bool:
    """'přízemí' said of the flat itself, never of a room or a shared space of the house."""
    if _PRIZEMNI_UNIT_RE.search(folded) or _PATRO_LABEL_PRIZEMI_RE.search(folded):
        return True
    for m in _V_PRIZEMI_RE.finditer(cased):
        # The slice keeps the match's own first letter so a capital 'V přízemí' ends the
        # sentence before it; that letter is then dropped.
        sentence = _SENTENCE_BREAK_RE.split(cased[max(0, m.start() - 200):m.start() + 1])[-1][:-1]
        words = [w.strip(",.:;()").lower() for w in sentence.split()]
        if any(_AMENITY_WORD_RE.match(w) for w in words[-_AMENITY_WINDOW:]):
            continue
        if any(_NEGATION_RE.fullmatch(w) for w in words[-2:]):
            continue
        if any(_UNIT_WORD_RE.fullmatch(w) for w in words[-_UNIT_WINDOW:]):
            return True
    return False


def _flat_floor(folded: str, cased: str) -> int | None:
    m = _FLOOR_LABEL_RE.search(folded)           # 1) 'Podlaží: 4. NP' label
    floor = _unit_storey(m.group(1)) if m else None
    if floor is None:                             # 2) 've 3. patře' / 'v 6. podlaží'
        m = _FLAT_PREP_RE.search(folded)
        if m:
            floor = _unit_storey(m.group(1))
    if floor is None:                             # 3) bare '3. patro' / '4. NP'
        m = _FLAT_BARE_RE.search(folded)
        if m:
            floor = _unit_storey(m.group(1))
    if floor is None:                             # 4) 'v šestém podlaží' / 'čtvrté patro'
        floor = _spelled_storey(folded)
    if (floor is None and not _UNREAD_STOREY_RE.search(folded)
            and _flat_on_ground(folded, cased)):  # 5) 'byt v přízemí' -> 0
        floor = 0
    return floor


def _legacy_floor(folded: str) -> int | None:
    floor: int | None = None
    m = _FLOOR_LABEL_RE.search(folded)           # 1) 'Podlaží: 4. NP' label
    if m:
        floor = normalize_floor(m.group(1))
    if floor is None:                             # 2) 've 3. patře' / '6. NP'
        m = _FLOOR_PREP_RE.search(folded)
        if m:
            floor = normalize_floor(m.group(1))
    if floor is None:                             # 3) bare '3. patro' / '4. NP'
        m = _FLOOR_BARE_RE.search(folded)
        if m:
            floor = normalize_floor(m.group(1))
    if floor is None and _PRIZEMI_TEXT_RE.search(folded):  # 4) přízemí -> 0
        floor = 0
    return floor


def floor_from_text(haystack: str | None, *,
                    category_main: str | None = None) -> tuple[int | None, int | None]:
    """Mine (floor, total_floors) from free text, high-precision only.

    A flat (or an unknown category) reads the flat grammar; every other category keeps the
    legacy miner. Returns (None, None) when no unit-floor cue is present — the ambiguous
    tail (mezonet/loft, bare 'suterén') is left to the text lane. Reads the building total
    separately and drops a captured floor that is not below it (most likely a building
    number we grabbed).
    """
    if not haystack:
        return None, None
    cased = _strip_marks(haystack)
    folded = cased.lower()
    total = _parse_total_floors(folded)
    floor = (_flat_floor(folded, cased) if category_main in _FLAT_CATEGORIES
             else _legacy_floor(folded))
    if floor is not None and not is_plausible_floor(floor, total):
        floor = None
    return floor, total
