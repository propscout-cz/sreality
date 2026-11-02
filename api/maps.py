"""Mapy.cz proxy — autocomplete suggest + RÚIAN code resolve.

The frontend never holds the Mapy.cz API key; it talks to these proxy
routes instead. Suggest responses are cached in-process for a few
minutes since identical queries don't change on that timescale and the
free tier is rate-limited. Resolve responses are NOT cached because
they depend on registry state which can change between deploys.

WHAT YOU PICK IS WHAT YOU FILTER. A location chip is ONE RÚIAN unit at the
level the operator picked — kraj, okres, obec, část obce or ulice
(`api/location_filter.LEVEL_COLUMN`) — and `resolve` answers one shape,
`{level, id, name, context}`: the picked level, the unit's RÚIAN code and its
register name, and for a unit below the obec the register name of its obec.
A pick the register cannot bind keeps its level with `id` null, and the SPA
then makes NO chip and says why. It resolves against the RÚIAN mirror
(`ruian_admin_units` + `ruian_admin_unit_geometries`, migration 381), the
register `listing_location` answers with, so chip and listing share one
registry version.

HOW EACH LEVEL IS BOUND. The mirror draws polygons down to the obec and NONE
for `cast_obce` or a street (`location_data/ruian_boundaries.LAYERS`), so:

  * obec / okres / kraj -> point-in-polygon, the resolver's own containing-obec
    statement (`resolve_db.CONTAINING_OBEC_SQL`) run verbatim, and its chain; the
    pick's NAME picks the unit of that chain, its type only breaks a tie
    (`_named_unit`);
  * cast_obce -> BY NAME against the mirror's name index, inside the PIP'd obec;
  * ulice -> BY NAME inside the PIP'd obec (`_bind_street`), then the border
    guard. An address pick binds the street its `regionalStructure` names;
  * adresni_misto (the address picker's flag only, W3b) -> that street's doors
    of the picked number, or the obec's for a streetless village, read by the
    resolver's own `address_points_by_number`: ONE door near the pick, or none.

Suggestions are regional only (`type=regional`): a POI has no register unit.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException

from api import http_json
from api.location_filter import ChipLevel
from location_data.resolver.bind import REGISTRY_PIN_CONFLICT_M
from location_data.resolver.composite import match_streets
from location_data.resolver.geo import haversine_m
from location_data.resolver.normalize import (
    case_fold,
    house_number,
    normalize_house_number,
    normalize_match_key,
    split_street_and_number,
)
from location_data.resolver.resolve_db import (
    CONTAINING_OBEC_SQL,
    CachedRegistryView,
    RunCache,
    SqlRegistryView,
)
from location_data.resolver.types import RegistryView, Street
from scraper.geocoding import KEY_LEVEL_STATUS, MapyKeyRefused, mapy_api_keys, with_mapy_key_failover

LOG = logging.getLogger(__name__)

# Mapy.cz suggestion `type` → the level it is TYPED at: the level itself for a část
# obce or a street (an address filters at its street), a tie-break for the point-bound
# levels (`_named_unit`). regional.country (and any other type) narrows nothing.
_TYPE_LEVEL: dict[str, ChipLevel] = {
    "regional.region": "kraj",
    "regional.region.district": "okres",
    "regional.municipality": "obec",
    "regional.municipality_part": "cast_obce",
    "regional.street": "ulice",
    "regional.address": "ulice",
}
# The levels a point binds through its containing-obec chain, finest first.
_POINT_LEVELS: tuple[ChipLevel, ...] = ("obec", "okres", "kraj")
# The border guard's slack beyond a street's own extent: the street point is the
# centroid of its doors and `extent_m` the farthest door, so a pick ON the street
# lies within the extent; 200 m absorbs a doorless stretch past it.
_STREET_SLACK_M = 200.0
_OKRES_PREFIX = re.compile(r"^\s*okres\s+", re.IGNORECASE)

MAPY_SUGGEST_URL = "https://api.mapy.cz/v1/suggest"
_SUGGEST_TTL_SECONDS = 300
_SUGGEST_CACHE_MAX = 256

_suggest_cache: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}


def _api_keys() -> list[str]:
    keys = mapy_api_keys()
    if not keys:
        raise HTTPException(status_code=503, detail="geocoding not configured")
    return keys


def _cache_evict_expired(now: float) -> None:
    for k, (exp, _) in list(_suggest_cache.items()):
        if exp <= now:
            del _suggest_cache[k]


def clear_suggest_cache() -> None:
    _suggest_cache.clear()


def suggest(query: str, *, limit: int = 10, lang: str = "cs") -> dict[str, Any]:
    keys = _api_keys()
    cache_key = (query, limit, lang)
    now = time.monotonic()
    hit = _suggest_cache.get(cache_key)
    if hit is not None and hit[0] > now:
        return hit[1]
    _cache_evict_expired(now)
    raw = _fetch_suggest(query, limit=limit, lang=lang, keys=keys)
    items = raw.get("items") or []
    payload = {"items": items}
    if len(_suggest_cache) >= _SUGGEST_CACHE_MAX:
        _suggest_cache.pop(next(iter(_suggest_cache)))
    _suggest_cache[cache_key] = (now + _SUGGEST_TTL_SECONDS, payload)
    return payload


def _fetch_suggest(
    query: str, *, limit: int, lang: str, keys: list[str]
) -> dict[str, Any]:
    def attempt(key: str, last: bool) -> dict[str, Any]:
        try:
            return http_json.fetch_json(
                "GET", MAPY_SUGGEST_URL, timeout=10,
                params={
                    "query": query, "limit": limit, "lang": lang,
                    "type": "regional", "apikey": key,
                },
            )
        except http_json.HttpJsonError as exc:
            if exc.status in KEY_LEVEL_STATUS and not last:
                raise MapyKeyRefused(exc.status) from exc
            # A rejected key / throttle / outage from Mapy must NOT surface as a raw
            # 500: the typeahead says "Place search is unavailable" only on a 503, and
            # a 500 leaves a silent empty dropdown. Log the real upstream status.
            LOG.warning("Mapy suggest upstream failure: %s", exc)
            raise HTTPException(
                status_code=503, detail="geocoding temporarily unavailable",
            ) from exc

    return with_mapy_key_failover(
        attempt, keys, log=LOG, refused="Mapy suggest key %d/%d rejected (status=%s); failing over to backup",
    )


_CURRENT_VERSION_SQL = "SELECT id FROM registry_versions WHERE is_current LIMIT 1"

# The obec covering ONE point is the resolver's statement, verbatim (one point in
# its arrays), so a chip, a listing and an estimation subject can never be put in
# different towns by two copies of the same question. Its chain is walked here.
_CONTAINING_CHAIN_SQL = f"""
WITH RECURSIVE hit AS ({CONTAINING_OBEC_SQL}), chain AS (
  SELECT u.id, u.parent_id, u.level::text AS level, u.code, u.name
    FROM ruian_admin_units u JOIN hit h ON h.id = u.id
  UNION ALL
  SELECT p.id, p.parent_id, p.level::text, p.code, p.name
    FROM ruian_admin_units p JOIN chain c ON p.id = c.parent_id
)
SELECT id, level, code, name FROM chain
"""

# A část obce by NAME inside one obec: `parent_obec_unit_id` is what makes a
# part unambiguous without a polygon. DISTINCT because the index stores several
# NAME KINDS per entity (official, deaccented, qualifier-stripped, aliases —
# migration 381): without it "Žižkov" comes back once per kind and reads as two
# parts.
_PART_LOOKUP_SQL = """
SELECT DISTINCT u.code, u.name
  FROM ruian_name_index n
  JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
 WHERE n.registry_version_id = %(version)s
   AND n.name_norm = %(name_norm)s
   AND u.level = 'cast_obce'
   AND u.valid_to IS NULL
   AND n.parent_obec_unit_id = %(obec_unit_id)s
 ORDER BY 1, 2
 LIMIT 8
"""


# Not memoised: resolve is a low-frequency endpoint and a fresh registry load must
# light up at once. A query error RAISES (a 500, which the SPA shows as "couldn't
# verify"): read as "no registry" it would tell the operator RÚIAN has no such place.
def _registry_version(conn: Any) -> int | None:
    """The current registry version, or None when the mirror is not loaded."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.ruian_admin_units') IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        cur.execute(_CURRENT_VERSION_SQL)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None


def _containing_chain(
    conn: Any, *, version: int, lat: float, lng: float
) -> dict[str, tuple[int, int, str]]:
    """{level: (unit_id, code, name)} for the obec covering the point and each
    of its ancestors. Empty when the point is outside every obec polygon."""
    with conn.cursor() as cur:
        cur.execute(_CONTAINING_CHAIN_SQL, ([0], [lat], [lng], version))
        rows = cur.fetchall()
    return {
        str(level): (int(unit_id), int(code), name)
        for unit_id, level, code, name in rows
    }


def containing_obec_kod(conn: Any, *, lat: float | None, lng: float | None) -> int | None:
    """The RÚIAN code of the obec covering a point, or None (outside every obec / no current
    registry) -- the one containing-obec statement, for subjects with no stored location
    (an estimation of a typed point). Any query error raises: read
    as "no obec" it would become a confident `location_unknown` frozen into a run."""
    if lat is None or lng is None:
        return None
    with conn.cursor() as cur:
        cur.execute(_CURRENT_VERSION_SQL)
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    obec = _containing_chain(conn, version=int(row[0]), lat=lat, lng=lng).get("obec")
    return obec[1] if obec else None


def _lookup_part(
    conn: Any, *, version: int, name: str, obec_unit_id: int,
) -> list[tuple[int, str]]:
    key = normalize_match_key(name)
    if not key:
        return []
    with conn.cursor() as cur:
        cur.execute(
            _PART_LOOKUP_SQL,
            {"version": version, "name_norm": key, "obec_unit_id": obec_unit_id},
        )
        return [(int(code), nm) for code, nm in cur.fetchall()]


def _name_variants(name: str) -> list[str]:
    """The pick's name, then its last `-` / `,` delimited segment.

    Mapy names a quarter the way a person says it — "Hradec Králové - Třebeš",
    "Ostrava-Poruba", "Praha-Vinoř" — while RÚIAN stores the bare part name
    ("Třebeš", "Poruba", "Vinoř"). One retry on the tail is the whole
    difference between a quarter chip and no chip at all; it is a RETRY, never
    a widening, because a miss on both still returns no code."""
    out = [name]
    tail = re.split(r"[-,]", name)[-1].strip()
    if tail and tail != name.strip():
        out.append(tail)
    return out


def _resolve_cast_obce(
    conn: Any, *, version: int, name: str, obec_unit_id: int, obec_code: int,
) -> tuple[int, str] | None:
    """A quarter pick, placed by NAME inside the PIP'd obec — or NOT placed.

    `cast_obce` ONLY, never `momc`: the answer table stores `cast_obce_kod` and
    has no městská-část column, so a momc code would be a chip that matches
    nothing. That means "Praha 2" and "Brno-střed" (both momc) cannot resolve,
    and neither can a Mapy neighbourhood the registry has never heard of.

    WHEN IT CANNOT BE PLACED, THE ANSWER IS "NO CODE", NOT "THE TOWN". Falling
    back to the containing obec would turn a saved "this quarter" filter into
    "this whole city" — silently, and most visibly in a watchdog that then mails
    the operator about every listing in Prague. No code means no chip.

    A name that means two different parts of one obec is also refused: picking
    one of them at random is a cohort the operator did not choose."""
    for variant in _name_variants(name):
        matches = _lookup_part(
            conn, version=version, name=variant, obec_unit_id=obec_unit_id,
        )
        codes = {code for code, _nm in matches}
        if len(codes) == 1:
            return matches[0]
        if len(codes) > 1:
            LOG.info(
                "maps.resolve: %r names %d parts of obec %s — refusing to guess",
                variant, len(codes), obec_code,
            )
            return None
    return None


# THE CHIP BINDER is stricter than the listing binder: a listing that binds a guess
# is one row in the wrong place, a chip that binds one is a whole cohort the operator
# did not pick. (1) VERBATIM — the register's spelling, case-folded, diacritics KEPT:
# it alone tells Praha's "Hanušova" from "Hanusova" (one deaccented key, 10 km
# apart). (2) A TRAILING NUMBER it missed is refused: a street pick carries no house
# number, and Nymburk holds "Na Hroudách" beside "Na Hroudách 1".."6". (3)
# `match_streets`' EXACT tier, one row ("ul. Husova", "nám. Republiky"); its
# type-word-tolerant tier reads Kladno's "náměstí Svobody" and "Svobody" as one name.
def _bind_street(
    name: str, obec_code: int, registry: RegistryView,
) -> tuple[Street | None, str]:
    """-> (the ONE street a chip may bind, its tier) or (None, why not)."""
    wanted = case_fold(name)
    verbatim: dict[int, Street] = {}
    for street in registry.streets_in_obec(obec_code):
        if case_fold(street.name) == wanted:
            verbatim.setdefault(street.code, street)  # one code is one street, as in match_streets
    if verbatim:
        if len(verbatim) > 1:
            return None, "verbatim homonym"
        return next(iter(verbatim.values())), "verbatim"
    if split_street_and_number(name)[1]:
        return None, "trailing number"
    streets, tier = match_streets(name, [obec_code], registry)
    if tier == "exact" and len(streets) == 1:
        return streets[0], tier
    return None, f"{len(streets)} {tier}-tier rows"


# THE BORDER GUARD. Only the PIP'd obec is searched, roads often ARE obec boundaries,
# and 77 % of street names exist in another obec too ("Hraniční" x57): a bound street
# whose point (`SqlRegistryView.street_point`: its doors' centroid, `extent_m` the
# farthest door) lies more than `extent_m + _STREET_SLACK_M` from the pick is a
# namesake. A street with no doors has no point, and binds.
def _resolve_street(
    conn: Any, *, version: int, name: str | None, obec_code: int,
    lat: float, lng: float,
) -> Street | None:
    """The picked street, or None with one LOG line."""
    if not name or not any(ch.isalpha() for ch in name):
        # Blank or bare digits: no register street is spelled without a letter
        # (0 of 83,476, 2026-10-03), so the obec's street list is not even read.
        return None
    # One request, one memo: the verbatim step and `match_streets`' index share
    # ONE `streets_in_obec` read (Praha: 7,675 rows).
    registry = CachedRegistryView(SqlRegistryView(conn, version), RunCache())
    street, tier = _bind_street(name, obec_code, registry)
    if street is None:
        LOG.info("maps.resolve: street %r in obec %s not bound (%s) — no code",
                 name, obec_code, tier)
        return None
    point = registry.street_point(street)
    if point is not None:
        off_m = haversine_m(lat, lng, point.lat, point.lon)
        if off_m > point.extent_m + _STREET_SLACK_M:
            LOG.info(
                "maps.resolve: street %r (%s) of obec %s lies %.0f m from the pick "
                "(extent %.0f m) — no code", name, street.code, obec_code, off_m,
                point.extent_m,
            )
            return None
    return street


def _entry_name(regional_structure: Sequence[Any], type_: str) -> str | None:
    """The name of the regionalStructure entry of this type, if any."""
    return next(
        (e["name"] for e in regional_structure
         if isinstance(e, dict) and e.get("type") == type_ and isinstance(e.get("name"), str)),
        None,
    )


def _street_of(type_: str, name: str, regional_structure: Sequence[Any]) -> str | None:
    """The name a pick is bound by: its own, except an address, which binds the
    `regional.street` entry of its regionalStructure (a street-less village
    address has none, and so binds nothing)."""
    return name if type_ != "regional.address" else _entry_name(regional_structure, "regional.street")


# The door's label: the ONE label (rule 25) over its register fields, as the row will read.
# `kod_adm` is the mirror's primary key (migration 381): one row or none, so no LIMIT/ORDER.
_DOOR_LABEL_SQL = """
SELECT location_display_label(
         s.name, CASE WHEN ap.typ_so = 'č.p.' THEN ap.cislo_domovni::text END,
         ap.cislo_orientacni::text || coalesce(ap.znak_orientacniho, ''),
         o.name, c.name, 'CZ', 'cz')
  FROM ruian_address_points ap
  LEFT JOIN ruian_streets s ON s.id = ap.street_id
  LEFT JOIN ruian_admin_units o ON o.id = ap.obec_unit_id
  LEFT JOIN ruian_admin_units c ON c.id = ap.cast_obce_unit_id
 WHERE ap.kod_adm = %s AND ap.valid_to IS NULL
"""


# THE ADDRESS PICKER'S DOOR (W3b). The number is the `regional.address` entry's; a street
# entry binds as the chip does and, unbound, binds nothing (never the whole obec). One keyed
# read of the resolver's own lookup — nothing searches the 3 M points — and exactly ONE door
# of that number within `REGISTRY_PIN_CONFLICT_M` of the pick, else no code.
def _resolve_address_point(
    conn: Any, *, version: int, lat: float, lng: float, name: str,
    regional_structure: Sequence[Any],
) -> tuple[int, str | None] | None:
    """The picked door's (kód ADM, label), or None with one LOG line."""
    obec = _containing_chain(conn, version=version, lat=lat, lng=lng).get("obec")
    slots = normalize_house_number(_entry_name(regional_structure, "regional.address") or "")
    number, typ = house_number(slots)
    if obec is None or number is None:
        LOG.info("maps.resolve: address %r has no obec or number — no code", name)
        return None
    street = None
    street_name = _street_of("regional.address", name, regional_structure)
    if street_name is not None:
        street = _resolve_street(conn, version=version, name=street_name, obec_code=obec[1],
                                 lat=lat, lng=lng)
        if street is None:
            return None
    co, znak = slots.get("cislo_orientacni"), slots.get("znak_orientacniho")
    doors = SqlRegistryView(conn, version).address_points_by_number(
        obec_kod=obec[1], street_name_norm=None if street is None else street.name_norm,
        cislo_domovni=number, cislo_orientacni=None if co is None else int(str(co)),
        typ_so=typ,
    )
    near = [d for d in doors
            if (co is None or (d.znak_orientacniho or "").lower() == (znak or ""))
            and d.lat is not None and d.lon is not None
            and haversine_m(lat, lng, d.lat, d.lon) <= REGISTRY_PIN_CONFLICT_M]
    if len(near) != 1:
        LOG.info("maps.resolve: address %r has %d doors within %.0f m of the pick — no code",
                 name, len(near), REGISTRY_PIN_CONFLICT_M)
        return None
    with conn.cursor() as cur:
        cur.execute(_DOOR_LABEL_SQL, (near[0].kod_adm,))
        row = cur.fetchone()
    return near[0].kod_adm, None if row is None else row[0]


# THE NAME RULE (D5). The point places the chain; the pick's NAME picks the unit in
# it, and the Mapy type is a hint: its enum has no okres type, so an okres arrives
# typed `regional.region`, and "Praha 2" typed `regional.municipality` (a městská
# část no chip serves) names no unit of its chain. One named unit binds at ITS level;
# obec and okres "Jihlava" share a name, and then the typed level decides — or Mapy's
# own "okres " prefix, which names the level outright.
def _named_unit(
    chain: dict[str, tuple[int, int, str]], typed: ChipLevel, name: str,
) -> tuple[ChipLevel, int, str] | None:
    """The chain unit the pick names, as (level, code, register name), or None."""
    bare = _OKRES_PREFIX.sub("", name)
    if bare != name:
        typed = "okres"
    wanted = normalize_match_key(bare)
    named = [(level, chain[level][1], chain[level][2]) for level in _POINT_LEVELS
             if level in chain and normalize_match_key(chain[level][2]) == wanted]
    if len(named) == 1:
        return named[0]
    return next((unit for unit in named if unit[0] == typed), None)


def _resolve_admin(
    conn: Any, *, version: int, lat: float, lng: float, level: ChipLevel,
    name: str | None,
) -> tuple[ChipLevel, tuple[int, str] | None, str | None]:
    """(bound level, its unit's (code, register name) or None, obec name below the obec)."""
    chain = _containing_chain(conn, version=version, lat=lat, lng=lng)
    obec = chain.get("obec")
    if obec is None:
        return level, None, None
    obec_unit_id, obec_code, obec_name = obec
    if level == "cast_obce":
        return level, _resolve_cast_obce(
            conn, version=version, name=name or "",
            obec_unit_id=obec_unit_id, obec_code=obec_code,
        ), obec_name
    if level == "ulice":
        street = _resolve_street(
            conn, version=version, name=name, obec_code=obec_code, lat=lat, lng=lng,
        )
        return level, None if street is None else (street.code, street.name), obec_name
    unit = _named_unit(chain, level, name or "")
    if unit is None:
        LOG.info("maps.resolve: %s pick %r names no single unit of its chain — no code",
                 level, name)
        return level, None, None
    return unit[0], (unit[1], unit[2]), None


def resolve(
    conn: Any,
    *,
    lat: float,
    lng: float,
    type_: str,
    name: str,
    regional_structure: Sequence[Any],
    address_point: bool = False,
) -> dict[str, Any]:
    """A Mapy pick -> `{level, id, name, context}`: its bound unit, else its typed level.
    With `address_point` (the address picker) an address pick answers its ONE door."""
    door = address_point and type_ == "regional.address"
    level = "adresni_misto" if door else _TYPE_LEVEL.get(type_)
    out: dict[str, Any] = {"level": level, "id": None, "name": None, "context": None}
    if level is None:
        return out
    version = _registry_version(conn)
    if version is None:
        return out
    if door:
        unit = _resolve_address_point(conn, version=version, lat=lat, lng=lng, name=name,
                                      regional_structure=regional_structure)
    else:
        out["level"], unit, out["context"] = _resolve_admin(
            conn, version=version, lat=lat, lng=lng, level=level,
            name=_street_of(type_, name, regional_structure),
        )
    if unit is not None:
        out["id"], out["name"] = unit
    return out
