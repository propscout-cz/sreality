"""Layer 1 of the decision model (PROGRAM.md §6): the rule floor, E2-E5 and E34.

Absolute authority, no training, evaluated before anything learned and never overridden. A
guard returns the NAME of the rule it broke, not a boolean, so every refusal is reportable:
the blocking stats count vetoes by name and the cluster pass records the invariant that
refused a union.

Guards read the same fields the production chokepoint reads (E4), and they are the only
place area tolerance is defined — `area_rel_diff` is `|a-b| / max(a,b)`, i.e. the shortfall
against the LARGER side, so the relation is symmetric, bounded in [0,1) and never flatters a
pair by dividing by the smaller number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence

from autodedup.body_align import rounding_equal_values
from autodedup.dataset import Listing
from autodedup.settings import Settings
from autodedup.text_facts import (
    STATED_AREA_STORED_RATIO,
    address_block_key,
    cellar_areas,
    outdoor_accessory_areas,
    printed_basement_storeys,
    printed_dispositions,
    printed_floors_by_form,
    unit_area_figures,
    unit_designators,
    worded_storeys,
)
from toolkit.room_taxonomy import category_main_compatible

if TYPE_CHECKING:  # pragma: no cover
    from autodedup.d43 import ClusterRelation
    from autodedup.fingerprint import Fingerprint

LAND_CATEGORY: str = "pozemek"
FLAT_CATEGORY: str = "byt"

UNIT_DESIGNATOR_VETO: str = "unit_designator_conflict"


class GuardSide(Protocol):
    """What a guard needs of a side: the columns `Fingerprint`, `GuardRow`, `WitnessSide` and
    `Listing` all carry, and the W32w readings, read only where both sides carry them."""

    source: str | None
    category_main: str | None
    category_type: str | None
    area_m2: float | None
    disposition: str | None
    floor: int | None
    witnessed: bool
    printed_unit_area: float | None
    printed_accessory_areas: tuple[float, ...]
    printed_storeys: tuple[tuple[str, tuple[int, ...]], ...]
    worded_storeys: tuple[str, ...]
    printed_disposition: str | None


# W1: an area composed of a printed figure and printed extras meets within 1 m2 or 2 %.
COMPOSITION_ABS_M2: float = 1.0
COMPOSITION_REL: float = 0.02


def area_rel_diff(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a <= 0.0 or b <= 0.0:
        return None
    return abs(a - b) / max(a, b)


def area_relation(
    a: float | None, b: float | None, settings: Settings | None = None
) -> str:
    """E5's three-way area verdict: `reject` >8%, `band` 3-8%, `support` <=3%, else `unknown`."""
    cfg = settings or Settings()
    diff = area_rel_diff(a, b)
    if diff is None:
        return "unknown"
    if diff > cfg.area_reject_pct:
        return "reject"
    if diff > cfg.area_band_pct:
        return "band"
    return "support"


def pair_veto(a: GuardSide, b: GuardSide, settings: Settings | None = None) -> str | None:
    """The name of the first hard guard the pair breaks (E2-E5), or None when it passes.

    Missing data is never a mismatch (E12): every clause needs BOTH sides known. W1 and W2 only
    lift the area and floor clauses, for two flats both carrying readings (W33m)."""
    cfg = settings or Settings()
    if (a.category_type is not None and b.category_type is not None
            and a.category_type != b.category_type):
        return "category_type"
    if not category_main_compatible(a.category_main, b.category_main):
        return "category_main"
    if (area_relation(a.area_m2, b.area_m2, cfg) == "reject"
            and not areas_witnessed(a, b, cfg.area_reject_pct, cfg)):
        return "area"
    is_land = LAND_CATEGORY in (a.category_main, b.category_main)
    dispo_a, dispo_b = a.disposition, b.disposition
    if cfg.witness_disposition and _both_witnessed(a, b):
        dispo_a, dispo_b = veto_disposition(a), veto_disposition(b)
    if (not is_land and dispo_a is not None and dispo_b is not None
            and dispo_a != dispo_b):
        return "disposition"
    if (a.category_main == FLAT_CATEGORY and b.category_main == FLAT_CATEGORY
            and a.floor is not None and b.floor is not None
            and abs(a.floor - b.floor) >= 2
            and not storeys_witnessed(a, b, cfg)):
        return "floor"
    return None


# --- W32w: what each body states beside its columns ----------------------------------------


@dataclass(frozen=True, slots=True)
class Witnesses:
    """One body's W32w readings, each empty unless the dial that reads it is on."""

    unit_area: float | None = None
    accessory_areas: tuple[float, ...] = ()
    storeys: tuple[tuple[str, tuple[int, ...]], ...] = ()
    worded_storeys: tuple[str, ...] = ()
    disposition: str | None = None


@dataclass(frozen=True, slots=True)
class WitnessSide:
    """A `Listing` read as a `GuardSide` under the W32w dials, for the D43 limbs."""

    source: str | None
    category_main: str | None
    category_type: str | None
    area_m2: float | None
    disposition: str | None
    floor: int | None
    witnessed: bool
    printed_unit_area: float | None
    printed_accessory_areas: tuple[float, ...]
    printed_storeys: tuple[tuple[str, tuple[int, ...]], ...]
    worded_storeys: tuple[str, ...]
    printed_disposition: str | None


def body_witnesses(description: str | None, cfg: Settings,
                   category_main: str | None) -> Witnesses:
    """The readings of one body under the W32w dials that read them. Area and storeys are read
    of a flat only: a house, commercial or land body prints a plot, a built-up area, a hall and
    its storeys beside the unit, and its columns are read as W31 reads them (W33m)."""
    if not description or not cfg.witnessing:
        return Witnesses()
    flat = category_main == FLAT_CATEGORY
    unit, extras = (_area_witnesses(description) if cfg.witness_area and flat
                    else (None, ()))
    storeys, worded = ((_storey_witnesses(description, cfg.d43_prose_floor_words))
                       if cfg.witness_floor and flat else ((), ()))
    offered = printed_dispositions(description) if cfg.witness_disposition else frozenset()
    return Witnesses(unit, extras, storeys, worded,
                     next(iter(offered)) if len(offered) == 1 else None)


def witness_side(listing: Listing, cfg: Settings, area_m2: float | None) -> WitnessSide:
    """`listing` with `area_m2` (D43 reads E293's effective area) and its body's readings."""
    seen = body_witnesses(listing.description, cfg, listing.category_main)
    return WitnessSide(listing.source, listing.category_main, listing.category_type, area_m2,
                       listing.disposition, listing.floor, cfg.witnessing, seen.unit_area,
                       seen.accessory_areas, seen.storeys, seen.worded_storeys,
                       seen.disposition)


def _both_witnessed(a: GuardSide, b: GuardSide) -> bool:
    return getattr(a, "witnessed", False) and getattr(b, "witnessed", False)


def _two_flats(a: GuardSide, b: GuardSide) -> bool:
    return a.category_main == FLAT_CATEGORY and b.category_main == FLAT_CATEGORY


def _accessory_sizes(description: str) -> tuple[float, ...]:
    """One size per extra the body states: the cellar (E305), the balcony or loggia and the
    terrace (E294). A kind stated at two sizes abstains; a garden is a plot, not an extra."""
    kinds: dict[str, set[float]] = {"cellar": {value for value, _d in cellar_areas(description)}}
    for kind, value, _decimals in outdoor_accessory_areas(description):
        if kind != "garden":
            kinds.setdefault(kind, set()).add(value)
    return tuple(sorted(next(iter(values)) for values in kinds.values() if len(values) == 1))


def _area_witnesses(description: str) -> tuple[float | None, tuple[float, ...]]:
    """W1: the ONE unit figure the body states (figures that are rounding of one another are
    one; a figure also read as an extra is the extra's) and its extras."""
    extras = _accessory_sizes(description)
    figures = [(value, decimals) for value, decimals in unit_area_figures(description)
               if not any(abs(value - extra) < 0.05 for extra in extras)]
    if not figures or not all(rounding_equal_values(x, dx, y, dy)
                              for x, dx in figures for y, dy in figures):
        return None, extras
    return max(figures, key=lambda figure: (figure[1], figure[0]))[0], extras


def _storey_witnesses(description: str, words: bool
                      ) -> tuple[tuple[tuple[str, tuple[int, ...]], ...], tuple[str, ...]]:
    """W2: the storeys the body prints by noun (`np`; `patro` on the NP scale; `pp`, below ground,
    negative) and the placements it words."""
    forms = dict(printed_floors_by_form(description, words))
    forms["pp"] = printed_basement_storeys(description)
    printed = tuple((form, tuple(sorted(values)))
                    for form, values in sorted(forms.items()) if values)
    return printed, tuple(sorted(worded_storeys(description, words)))


def _shares_extra(left: Sequence[float], right: Sequence[float]) -> bool:
    return any(abs(x - y) < 0.05 for x in left for y in right)


def _composes(figure: float, unit: float, extras: Sequence[float]) -> bool:
    """Ruling 13: `figure` is the printed unit figure with one or two of the same body's extras
    added or taken away (`44 = 40 + 4 sklep`, `54 = 60 - 4 balkon - 2 sklep`)."""
    sums = list(extras) + [extras[i] + extras[j]
                           for i in range(len(extras)) for j in range(i + 1, len(extras))]
    for extra in sums:
        for total in (unit + extra, unit - extra):
            gap = abs(figure - total)
            if gap <= COMPOSITION_ABS_M2 or gap <= COMPOSITION_REL * max(figure, total):
                return True
    return False


def _printed_unit(side: GuardSide) -> float | None:
    """The one unit figure a side's body states, beside a column it is within
    `STATED_AREA_STORED_RATIO` of (a figure three times the column is not this unit's)."""
    column, unit = side.area_m2, side.printed_unit_area
    if column is None or column <= 0.0 or unit is None:
        return None
    if not column / STATED_AREA_STORED_RATIO <= unit <= column * STATED_AREA_STORED_RATIO:
        return None
    return unit


def _area_figures(side: GuardSide) -> tuple[float, ...]:
    """W1's figure set F of a side: its column, and the unit figure its own body states."""
    column = side.area_m2
    if column is None or column <= 0.0:
        return ()
    unit = _printed_unit(side)
    return (column,) if unit is None else (column, unit)


def _within(x: float, y: float, tol: float) -> bool:
    diff = area_rel_diff(x, y)
    return diff is not None and diff <= tol


def _composed(side: GuardSide, other: GuardSide) -> bool:
    """Ruling 13 across the pair: a figure of `other` is `side`'s printed unit figure with one
    or two of `side`'s own extras, where `other`'s body does not state the same extra (two
    bodies each beside one shared cellar are two areas, not one area two ways)."""
    unit = _printed_unit(side)
    extras = side.printed_accessory_areas
    if unit is None or not extras or _shares_extra(extras, other.printed_accessory_areas):
        return False
    return any(_composes(figure, unit, extras) for figure in _area_figures(other))


def areas_witnessed(a: GuardSide, b: GuardSide, tol: float, cfg: Settings) -> bool:
    """W1 (`witness_area`): the gap between two flats' columns is no stated fact.

    Some figure of one side's F (`_area_figures`) is within `tol` of one of the other's, or one
    side's printed unit figure and its own extras compose a figure of the other (`_composed`);
    never where both bodies state unit figures that neither meet nor compose, which is two
    stated areas. Every caller reads it only where the columns are already apart, so a reading
    takes a column fact away and never adds one, and two columns that pass are the baseline's.
    A body is read only where both sides carry readings, so a row `rt_fp` serves reads as W31."""
    if not (cfg.witness_area and _two_flats(a, b) and _both_witnessed(a, b)):
        return False
    if _composed(a, b) or _composed(b, a):
        return True
    unit_a, unit_b = _printed_unit(a), _printed_unit(b)
    if unit_a is not None and unit_b is not None and not _within(unit_a, unit_b, tol):
        return False
    return any(_within(x, y, tol) for x in _area_figures(a) for y in _area_figures(b))


def veto_disposition(side: GuardSide) -> str | None:
    """W5: a stated column, read by the one disposition its own body offers."""
    if side.disposition is not None and side.printed_disposition is not None:
        return side.printed_disposition
    return side.disposition


def _worded_fits(word: str, storey: int) -> bool:
    """Does a worded placement admit a printed storey (NP scale, below ground negative)?"""
    if word == "semi":
        return storey in (-1, 1)
    if word == "ground":
        return storey == 1
    if word == "raised":
        return storey in (1, 2)
    return storey >= 2


def _column_storey(storey: int, cfg: Settings, source: str | None) -> int | None:
    """A printed storey on one source's floor column: below ground as printed (147111's `-1. PP`
    is sreality's -1); above it the NP number on a camp that counts the ground floor (level 1),
    one less on one that does not (level 0), unknown on a source the camps do not place."""
    if storey < 0:
        return storey
    camp = cfg.floor_camps.get(source) if source is not None else None
    return None if camp is None else storey - 1 + int(camp)


def storeys_witnessed(a: GuardSide, b: GuardSide, cfg: Settings) -> bool:
    """W2 (`witness_floor`, two flats both carrying readings): the floor columns' gap is no
    stated fact — both bodies print one storey, the same in a shared noun or one apart across
    `patro` and `NP` (618930), or one body's one storey, no worded placement against it, is the
    silent side's column on its camp (147111). Read only where the columns already differ, so
    it lifts the veto or the D43 floor fact and never adds one."""
    if not (cfg.witness_floor and _two_flats(a, b) and _both_witnessed(a, b)):
        return False
    left, right = dict(a.printed_storeys), dict(b.printed_storeys)
    if left and right:
        if any(len(values) != 1 for values in (*left.values(), *right.values())):
            return False
        shared = set(left) & set(right)
        if shared:
            return all(left[form] == right[form] for form in shared)
        if len(left) == len(right) == 1 and set(left) | set(right) == {"np", "patro"}:
            (x,), (y,) = next(iter(left.values())), next(iter(right.values()))
            return abs(x - y) <= 1
        return False
    printer, silent = (a, b) if left else (b, a)
    printed = {value for _form, values in printer.printed_storeys for value in values}
    if len(printed) != 1 or silent.floor is None:
        return False
    storey = next(iter(printed))
    if not all(_worded_fits(word, storey)
               for word in (*printer.worded_storeys, *silent.worded_storeys)):
        return False
    return _column_storey(storey, cfg, silent.source) == silent.floor


def unit_designator_conflict(
    a: Listing, b: Listing, settings: Settings | None = None
) -> tuple[str, str] | None:
    """E61: the two designators, when both adverts name a DIFFERENT unit of one address block.

    A developer's adverts inside one building are near-identical by construction — same address,
    same photographs, same template, often the same price — so no resemblance can separate them.
    The one thing that can is the building's own naming: `byt (č.3)`, `označením B36`,
    `jednotka č. 12`. When both bodies name exactly one unit and the names differ at one address,
    they are two units and no certificate and no score may merge them.

    Exactly one designator per side is required. A body listing several (`jednotky č. 3, 5 a 7`)
    is a project's price list, not this advert's identity, and two such lists overlapping proves
    nothing either way. The block must match too: two buildings number their flats independently,
    so `byt č. 3` in one house and `byt č. 5` in another is not a conflict.

    Honest about its evidence: this fires on 3 distinct (block, unit-pair) facts in ONE Zizkov
    building in the g5 cohort (ruian:21778370, units 1/3/5). It is a rule with a sound mechanism
    measured on one development, never a rate."""
    if settings is not None and not settings.unit_designator_veto:
        return None
    left, right = unit_designators(a.description), unit_designators(b.description)
    # Read the texts FIRST: a side that names no unit ends the rule here, which is also the
    # only thing a stub side (`evaluate._SimSide`, everything None) can honestly answer.
    if len(left) != 1 or len(right) != 1 or left == right:
        return None
    if address_block_key(a) != address_block_key(b):
        return None
    return next(iter(left)), next(iter(right))


def floor_relation(a: GuardSide, b: GuardSide) -> str:
    """`reject` / `band` (delta 1 - portals disagree on přízemí) / `support` / `unknown`."""
    if (a.category_main != FLAT_CATEGORY or b.category_main != FLAT_CATEGORY
            or a.floor is None or b.floor is None):
        return "unknown"
    delta = abs(a.floor - b.floor)
    if delta >= 2:
        return "reject"
    return "band" if delta == 1 else "support"


def _far_pairs_excused(
    members: Sequence["Fingerprint"], cfg: Settings, relation: "ClusterRelation | None",
    closure_of: Mapping[int, int] | None = None,
) -> bool:
    """Every pair of members of two closures whose COLUMNS are further apart than the spread
    states one area: its W1 readings meet within the spread (`witness_area`), or both bodies
    print the same floor-area figures (E280), so the spread is the portals'."""
    from autodedup.indistinguishable import _printed_areas_prevail

    prevails = cfg.d43_printed_area_prevails and relation is not None
    if not prevails and not cfg.witness_area:
        return False
    listings: Mapping[int, Listing] | None = None
    sized = [fp for fp in members if fp.area_m2 is not None and fp.area_m2 > 0.0]
    for index, left in enumerate(sized):
        for right in sized[index + 1:]:
            gap = area_rel_diff(left.area_m2, right.area_m2)
            if gap is None or gap <= cfg.cluster_area_spread:
                continue
            if _same_closure(left.listing_id, right.listing_id, closure_of):
                continue
            if areas_witnessed(left, right, cfg.cluster_area_spread, cfg):
                continue
            if not prevails:
                return False
            if listings is None:
                reader = getattr(relation, "listings", None)
                if reader is None:
                    return False
                listings = reader()
            a, b = listings.get(left.listing_id), listings.get(right.listing_id)
            if a is None or b is None or not _printed_areas_prevail(a, b):
                return False
    return True


def _same_closure(left: int, right: int, closure_of: Mapping[int, int] | None) -> bool:
    if not closure_of:
        return False
    key = closure_of.get(left)
    return key is not None and key == closure_of.get(right)


def _apart(values: Sequence[tuple[int, Any]], closure_of: Mapping[int, int] | None,
           far: Callable[[Any, Any], bool]) -> bool:
    """Whether two stated values of DIFFERENT closures are `far` apart (E910). Without a ruling
    every advert is its own closure, and every `far` here is decided by the extreme pair (a
    relative gap, or plain inequality), so that case is the old O(n) min/max rule exactly; only
    a set holding two adverts of one closure reads pair by pair."""
    if not values:
        return False
    keys = [closure_of[i] for i, _v in values if i in closure_of] if closure_of else []
    if len(keys) == len(set(keys)):
        ordered = [v for _i, v in values]
        return far(min(ordered), max(ordered))
    return any(far(left, right)
               for index, (left_id, left) in enumerate(values)
               for right_id, right in values[index + 1:]
               if not _same_closure(left_id, right_id, closure_of))


def cluster_invariants_ok(
    members: Sequence["Fingerprint"],
    settings: Settings | None = None,
    must_not_link: frozenset[tuple[int, int]] | set[tuple[int, int]] = frozenset(),
    relation: "ClusterRelation | None" = None,
    closure_of: Mapping[int, int] | None = None,
) -> str | None:
    """E34 on the MERGED member set: the violated invariant's name, or None when it holds.

    E132: `relation` is D43 read at cluster grain — a group is built transitively, so a
    pairwise gate alone lets A-B and B-C pass while A and C differ on the floor. It is the last
    limb because it is the expensive one and it reads the two BODIES, which a fingerprint
    cannot.

    E910: `closure_of` maps a member to its must-link closure (the operator's `same` rulings,
    Decision 8), and an advert it does not name is a closure of its own. The hard limbs (size,
    deal type, category) and the operator's must-not-links read the whole set; the spreads
    (area, disposition, floor) are read across closures only, because the operator has ruled
    two adverts of one closure one property — with no ruling that is every pair, today's rule
    exactly. The relation must be bound to the same closures by the caller.

    E938: the category limb reads every PAIR of the group's categories. Rule #15 is a set of
    pairs, not an equivalence: a komerční may join a byt and a dům, the byt and the dům never.
    """
    cfg = settings or Settings()
    if len(members) > cfg.max_cluster_size:
        return "size"

    types = {fp.category_type for fp in members if fp.category_type is not None}
    if len(types) > 1:
        return "category_type"

    cats = sorted({fp.category_main for fp in members if fp.category_main is not None})
    for index, left in enumerate(cats):
        for right in cats[index + 1:]:
            if not category_main_compatible(left, right):
                return "compat_class"

    sized = [(fp.listing_id, fp.area_m2) for fp in members
             if fp.area_m2 is not None and fp.area_m2 > 0.0]
    if (_apart(sized, closure_of,
               lambda lo, hi: abs(hi - lo) / max(lo, hi) > cfg.cluster_area_spread)
            and not _far_pairs_excused(members, cfg, relation, closure_of)):
        return "area_spread"

    is_land = any(fp.category_main == LAND_CATEGORY for fp in members)
    if cfg.cluster_disposition and not is_land:
        stated = [(fp.listing_id, fp.disposition) for fp in members
                  if fp.disposition is not None]
        if _apart(stated, closure_of, lambda a, b: a != b):
            return "disposition"

    if cfg.cluster_floor_spread:
        floors = [(fp.listing_id, fp.floor) for fp in members
                  if fp.category_main == FLAT_CATEGORY and fp.floor is not None]
        if _apart(floors, closure_of, lambda a, b: a != b):
            return "floor_spread"

    ids = [fp.listing_id for fp in members]
    for index, left in enumerate(ids):
        for right in ids[index + 1:]:
            pair = (left, right) if left < right else (right, left)
            if pair in must_not_link:
                return "must_not_link"

    if relation is not None and relation.violating_pair(ids) is not None:
        return "d43_distinguishable"
    return None
