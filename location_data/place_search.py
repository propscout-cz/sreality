"""Own place search (#2449; spec #2441 Part 4): typed text to register places that are already chips.

One read over the current registry version, by name: tiers exact, prefix, then fuzzy (word similarity, only while the
first two fall short of the limit); within a tier, served properties of any deal type (`place_served_counts`), then the
town's latest population, then code (Q12 a: one ranking for every place box), except that on an exact name tie the
obec comes first, before its namesake okres or část (#2497). Counts rank rows and are never returned.
A row is a chip: level, RÚIAN code, register name, context (the parent's name) and a fly-to point from `place_facts`,
or none. It answers kraj, okres, obec, část obce and street, never a cadastral area, cohesion region or the country
(Q6 a, Q9). A městská část, no served level (#2380), answers as a place shortcut (#2450): its `places` are its
quarters' chips, the register's `momc_cast_obce` pairs at the index's version, and it ranks by the sum of their served
properties; with no pair at that version it answers nothing. Never reads the place picks (migration 737): they are its
holdout.

A street and a house number, or a village and its č.p., also answer RÚIAN doors (#2451; stories 44, 53): level
`adresni_misto`, kód ADM, the one door label (which names no number for a streetless door, and for a č.ev. door only
its č.o., if any), its town's okres as context, its point, and its street's chip in `street` (null for a streetless
door). A bare number is tried as the č.p. and as the č.o., never as a č.ev.; a letter on it ("12a") is the č.o.'s. Door
rows go after the whole text's exact rows and before its similar ones.

The address may carry its town (#2485): after a comma, before it, or as the words after the number ("Nádražní 12,
Kolín", "Kolín, Nádražní 12", "Nádražní 12 Kolín"). Its doors are then only those in a town of that exact name, an obec,
a část obce or a městská část, every homonym kept; a town that names nothing answers no door. Each reading is one
statement, tried in order until one answers, the whole text's always last; the name read takes the whole text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import psycopg

from location_data.name_index import normalize_name, normalize_street_name
from location_data.resolver.composite import WHOLE_LEVELS, abbreviation_spellings
from location_data.resolver.normalize import TYP_CP, house_number, split_street_and_number, strip_street_generic

LOG = logging.getLogger("location_data.place_search")

_FUZZY_MIN_CHARS = 3
_LETTER = re.compile(r"[a-z]")
# "<street> <number> <town>": the town is what follows the LAST number that whitespace and a letter follow, so an
# ordinal ("28. října") and a č.o. ("12/3") never split, and "Praha 6" keeps its digit.
_TOWN_AFTER = re.compile(r"(?<![\w./])(\d{1,6}(?:\s*/\s*\d{1,5}[a-zA-Z]?|[a-zA-Z])?)\s+(?=[^\W\d_])")
# "Nádražní 12a", which the resolver's parser reads as no number: RÚIAN carries a letter on the č.o. alone.
_LETTERED = re.compile(r"^(?P<street>.*\S)\s+(?P<co>\d{1,5})(?P<znak>[a-zA-Z])$")
_DOOR_LEVEL = "adresni_misto"

# The door's label: the ONE label (rule 25) over its register fields, as the row will read. Shared with the address
# picker's preview (api/maps.py imports DOOR_LABEL_SQL until the switch-over, #2453, moves the picker to door rows, if
# addresses pass the gate, #2465).
_DOOR_LABEL = """location_display_label(
         s.name, CASE WHEN ap.typ_so = 'č.p.' THEN ap.cislo_domovni::text END,
         ap.cislo_orientacni::text || coalesce(ap.znak_orientacniho, ''),
         o.name, c.name, 'CZ', 'cz')"""
_DOOR_LABEL_JOINS = """
  LEFT JOIN ruian_streets s ON s.id = ap.street_id
  LEFT JOIN ruian_admin_units o ON o.id = ap.obec_unit_id
  LEFT JOIN ruian_admin_units c ON c.id = ap.cast_obce_unit_id"""

# `kod_adm` is the mirror's primary key (migration 381): one row or none, so no LIMIT/ORDER.
DOOR_LABEL_SQL = f"""
SELECT {_DOOR_LABEL}
  FROM ruian_address_points ap{_DOOR_LABEL_JOINS}
 WHERE ap.kod_adm = %s AND ap.valid_to IS NULL
"""

# The threshold is pinned, not inherited: "brno" reaches Brněnská at exactly 0.6.
_BOUNDS_SQL = (
    "SELECT set_config('statement_timeout', '2s', true),"
    " set_config('pg_trgm.word_similarity_threshold', '0.6', true)"
)

# A unit is read AT the index's version, as api/maps.py's part lookup reads it (a load closes a re-parented unit before
# it publishes); a street, which has no index, by the version's membership (location_data/ruian_boundaries.py). A NULL
# prefix or fuzzy key switches its arm off. A městská část is a candidate only with pairs at that version, gated in both
# unit arms so one without never counts toward `near` and switches fuzzy off; `unit_id` carries its row to the pairs.
_SUGGEST_SQL = """
WITH cur AS (SELECT id FROM registry_versions WHERE is_current),
units AS (
  SELECT DISTINCT ON (u.level, u.code) u.level, u.code, u.name, u.parent_id, u.id AS unit_id,
         CASE WHEN n.name_norm = %(unit_key)s AND n.name_kind <> 'qualifier_stripped' THEN 0 ELSE 1 END AS tier
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE n.registry_version_id = (SELECT id FROM cur)
     AND n.name_norm LIKE %(unit_prefix)s
     AND n.entity_kind IN ('kraj', 'okres', 'obec', 'momc', 'cast_obce')
     AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id
     AND (u.level <> 'momc' OR EXISTS (SELECT 1 FROM ruian_admin_unit_relations rel
                                        WHERE rel.from_id = u.id AND rel.relation_type = 'momc_cast_obce'
                                          AND rel.registry_version_id = (SELECT id FROM cur)))
   ORDER BY u.level, u.code, tier, u.valid_from DESC),
streets AS (
  SELECT DISTINCT ON (s.code) 'ulice'::ruian_level AS level, s.code, s.name, s.obec_unit_id AS parent_id,
         NULL::bigint AS unit_id, CASE WHEN s.name_norm = %(street_key)s THEN 0 ELSE 1 END AS tier
    FROM ruian_streets s
   WHERE s.name_norm LIKE %(street_prefix)s
     AND s.first_version_id <= (SELECT id FROM cur) AND s.last_version_id >= (SELECT id FROM cur)
     AND (s.valid_to IS NULL OR s.last_version_id > (SELECT id FROM cur))
   ORDER BY s.code, tier, s.valid_from DESC),
near AS (SELECT * FROM units UNION ALL SELECT * FROM streets),
fuzzy AS (
  SELECT u.level, u.code, u.name, u.parent_id, u.id AS unit_id, 2 AS tier
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE (SELECT count(*) FROM near) < %(limit)s
     AND %(unit_fuzzy)s <%% n.name_norm
     AND n.registry_version_id = (SELECT id FROM cur)
     AND n.entity_kind IN ('kraj', 'okres', 'obec', 'momc', 'cast_obce')
     AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id
     AND (u.level <> 'momc' OR EXISTS (SELECT 1 FROM ruian_admin_unit_relations rel
                                        WHERE rel.from_id = u.id AND rel.relation_type = 'momc_cast_obce'
                                          AND rel.registry_version_id = (SELECT id FROM cur)))
  UNION ALL
  SELECT 'ulice'::ruian_level, s.code, s.name, s.obec_unit_id, NULL::bigint, 2
    FROM ruian_streets s
   WHERE (SELECT count(*) FROM near) < %(limit)s
     AND %(street_fuzzy)s <%% s.name_norm
     AND s.first_version_id <= (SELECT id FROM cur) AND s.last_version_id >= (SELECT id FROM cur)
     AND (s.valid_to IS NULL OR s.last_version_id > (SELECT id FROM cur))),
hits AS (
  SELECT DISTINCT ON (a.level, a.code) a.*
    FROM (SELECT * FROM near UNION ALL SELECT * FROM fuzzy) a
   ORDER BY a.level, a.code, a.tier),
quarters AS (
  SELECT rel.from_id AS unit_id, q.level, q.code, q.name, q.parent_id
    FROM ruian_admin_unit_relations rel JOIN ruian_admin_units q ON q.id = rel.to_id
   WHERE rel.relation_type = 'momc_cast_obce' AND rel.registry_version_id = (SELECT id FROM cur)
     AND rel.from_id IN (SELECT h.unit_id FROM hits h WHERE h.level = 'momc')),
ranked AS (
  SELECT h.*, coalesce(c.n, parts.n, 0) AS served, pop.population
    FROM hits h
    LEFT JOIN place_served_counts c ON c.level = h.level AND c.code = h.code
    LEFT JOIN LATERAL (SELECT sum(qc.n) AS n FROM quarters q
                         JOIN place_served_counts qc ON qc.level = q.level AND qc.code = q.code
                        WHERE q.unit_id = h.unit_id) parts ON true
    LEFT JOIN LATERAL (SELECT p.population FROM obec_population p
                        WHERE h.level = 'obec' AND p.obec_kod = h.code
                        ORDER BY p.year DESC LIMIT 1) pop ON true
   ORDER BY h.tier, (h.tier = 0 AND h.level <> 'obec'), served DESC, pop.population DESC NULLS LAST, h.code,
            h.level
   LIMIT %(limit)s)
SELECT r.level::text, r.code, r.name, par.name, f.lat, f.lng,
       (SELECT json_agg(json_build_array(q.level, q.code, q.name, qp.name)) FROM quarters q
          LEFT JOIN ruian_admin_units qp ON qp.id = q.parent_id AND qp.level IN ('kraj', 'okres', 'obec')
         WHERE q.unit_id = r.unit_id),
       r.tier
  FROM ranked r
  LEFT JOIN ruian_admin_units par ON par.id = r.parent_id AND par.level IN ('kraj', 'okres', 'obec')
  LEFT JOIN place_facts f ON f.level = r.level AND f.code = r.code
 ORDER BY r.tier, (r.tier = 0 AND r.level <> 'obec'), r.served DESC, r.population DESC NULLS LAST, r.code, r.level
"""

# THE DOOR READ (#2451): one batched read over EVERY exact namesake street, and every exact namesake část obce for a
# streetless door (a village is a část, never the town: a č.p. is unique only within its část), ranked as the name read
# ranks (Q12 a). Every namesake, not the top few: about 77 % of street names repeat in another town and only about one
# Nádražní in seven has a door 12, so a short list answers almost nothing. The LIMIT 1000 per kind guards against a
# runaway name; it is no ranking (the largest, Zahradní, is 705 streets). Candidates are read at the index's version and
# reach their doors by stable code, through every street row of the code and every obec row of the town (resolve_db's
# rule): a load updates doors in place, re-pointing them to the rows it reopens. A door it inserts waits for its version
# to publish. The č.p. and the č.o. are two arms, each an index condition on ruian_ap_street_hn, as the OR form would
# use only its street_id. Labels are computed for the `top` rows alone.
#
# A typed town (#2485) is every obec, část obce and městská část of that name at the index's version, every name kind,
# homonyms all kept: the read narrows a list and binds nothing, so the resolver's whole-string ladder and its
# fail-closed rule do not apply, but its levels do (an okres or a kraj is a region, never the town). Streets and
# villages narrow to its obce, and doors to those that lie in it: its obec or část by code, or its městská část through
# every row of the code (a street runs through several of either). With no town each condition folds away at plan
# time: the statement is unnamed, its parameters planned in.
_TOWN_LEVELS = ", ".join(f"'{level}'" for level in WHOLE_LEVELS)
_DOOR_IN_TOWN = """
         AND (%(town)s::text IS NULL OR ap.obec_kod IN (SELECT code FROM town WHERE level = 'obec')
              OR ap.cast_obce_kod IN (SELECT code FROM town WHERE level = 'cast_obce')
              OR ap.momc_unit_id IN (SELECT m.id FROM ruian_admin_units m
                                      JOIN town t ON t.level = 'momc' AND m.level = 'momc' AND m.code = t.code))"""
_DOORS_SQL = f"""
WITH cur AS (SELECT id FROM registry_versions WHERE is_current),
town AS (
  SELECT DISTINCT u.level, u.code, CASE WHEN u.level = 'obec' THEN u.code ELSE po.code END AS obec_code
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
    LEFT JOIN ruian_admin_units po ON po.id = u.parent_id AND po.level = 'obec'
   WHERE n.registry_version_id = (SELECT id FROM cur) AND n.name_norm = %(town)s::text
     AND n.entity_kind IN ({_TOWN_LEVELS})
     AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id),
town_obce AS (SELECT o.id FROM ruian_admin_units o WHERE o.level = 'obec' AND o.code IN (SELECT obec_code FROM town)),
streets AS (
  SELECT DISTINCT ON (s.code) s.code, s.name, s.obec_unit_id
    FROM ruian_streets s
   WHERE s.name_norm = ANY(%(street_keys)s::text[])
     AND s.first_version_id <= (SELECT id FROM cur) AND s.last_version_id >= (SELECT id FROM cur)
     AND (s.valid_to IS NULL OR s.last_version_id > (SELECT id FROM cur))
     AND (%(town)s::text IS NULL OR s.obec_unit_id IN (SELECT id FROM town_obce))
   ORDER BY s.code, s.valid_from DESC),
parts AS (
  SELECT DISTINCT ON (u.code) u.code, u.parent_id,
         CASE WHEN n.name_kind <> 'qualifier_stripped' THEN 0 ELSE 1 END AS tier
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE n.registry_version_id = (SELECT id FROM cur) AND n.name_norm = %(part_key)s::text
     AND n.entity_kind = 'cast_obce' AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id
     AND (%(town)s::text IS NULL OR u.parent_id IN (SELECT id FROM town_obce))
   ORDER BY u.code, tier, u.valid_from DESC),
candidates AS (
  (SELECT 0 AS tier, coalesce(c.n, 0) AS served, st.code AS street_code, st.name AS street_name,
          st.obec_unit_id AS street_obec, NULL::bigint AS part_code, NULL::bigint AS part_parent
     FROM streets st LEFT JOIN place_served_counts c ON c.level = 'ulice' AND c.code = st.code
    ORDER BY 2 DESC, 3 LIMIT 1000)
  UNION ALL
  (SELECT p.tier, coalesce(c.n, 0), NULL, NULL, NULL, p.code, p.parent_id
     FROM parts p LEFT JOIN place_served_counts c ON c.level = 'cast_obce' AND c.code = p.code
    ORDER BY 1, 2 DESC, 6 LIMIT 1000)),
doors AS (
  SELECT k.tier, k.served, k.street_code, k.street_name, k.street_obec, a.kod_adm
    FROM candidates k
    JOIN ruian_streets sr ON sr.code = k.street_code
    CROSS JOIN LATERAL (
      SELECT ap.kod_adm FROM ruian_address_points ap
       WHERE ap.street_id = sr.id AND ap.cislo_domovni = %(domovni)s::integer AND ap.typ_so = %(typ)s::text
         AND (%(orientacni)s::integer IS NULL OR ap.cislo_orientacni = %(orientacni)s::integer)
         AND (%(znak)s::text IS NULL OR lower(ap.znak_orientacniho) = %(znak)s::text){_DOOR_IN_TOWN}
         AND ap.valid_to IS NULL AND ap.first_version_id <= (SELECT id FROM cur)
      UNION
      SELECT ap.kod_adm FROM ruian_address_points ap
       WHERE ap.street_id = sr.id AND ap.cislo_orientacni = %(bare)s::integer
         AND (%(znak)s::text IS NULL OR lower(ap.znak_orientacniho) = %(znak)s::text){_DOOR_IN_TOWN}
         AND ap.valid_to IS NULL AND ap.first_version_id <= (SELECT id FROM cur)) a
  UNION ALL
  SELECT k.tier, k.served, NULL, NULL, NULL, ap.kod_adm
    FROM candidates k
    JOIN ruian_admin_units po ON po.id = k.part_parent AND po.level = 'obec'
    JOIN ruian_admin_units ob ON ob.level = 'obec' AND ob.code = po.code
    JOIN ruian_address_points ap ON ap.obec_unit_id = ob.id
   WHERE ap.cislo_domovni = %(domovni)s::integer AND ap.typ_so = %(typ)s::text
     AND ap.street_id IS NULL AND ap.cast_obce_kod = k.part_code{_DOOR_IN_TOWN}
     AND %(orientacni)s::integer IS NULL AND ap.valid_to IS NULL AND ap.first_version_id <= (SELECT id FROM cur)),
top AS (SELECT * FROM doors ORDER BY tier, served DESC, kod_adm LIMIT %(limit)s)
SELECT d.kod_adm, {_DOOR_LABEL}, tc.name, ST_Y(ap.geom), ST_X(ap.geom), d.street_code, d.street_name, so.name
  FROM top d
  JOIN ruian_address_points ap ON ap.kod_adm = d.kod_adm{_DOOR_LABEL_JOINS}
  LEFT JOIN ruian_admin_units tc ON tc.id = o.parent_id AND tc.level IN ('kraj', 'okres', 'obec')
  LEFT JOIN ruian_admin_units so ON so.id = d.street_obec AND so.level IN ('kraj', 'okres', 'obec')
 ORDER BY d.tier, d.served DESC, d.kod_adm
"""


@dataclass(frozen=True)
class Place:
    level: str
    id: int
    name: str
    context: str | None
    lat: float | None = None
    lng: float | None = None
    places: tuple[Place, ...] | None = None  # a shortcut's chips; None on every other row
    street: Place | None = None  # a door's street chip; None on a streetless door and every other row

    def chip(self) -> dict[str, Any]:
        return {"level": self.level, "id": self.id, "name": self.name, "context": self.context}

    def item(self) -> dict[str, Any]:
        point = None if self.lat is None or self.lng is None else {"lat": self.lat, "lng": self.lng}
        item = {**self.chip(), "point": point}
        if self.places is not None:
            item["places"] = [p.chip() for p in self.places]
        if self.level == _DOOR_LEVEL:
            item["street"] = None if self.street is None else self.street.chip()
        return item


def _places(rows: list[list[Any]] | None) -> tuple[Place, ...] | None:
    """A shortcut's places in name order as the place boxes list them, ties by code: sorted here, not by a collation."""
    if rows is None:
        return None
    chips = (Place(level, int(code), name, context) for level, code, name, context in rows)
    return tuple(sorted(chips, key=lambda p: (normalize_name(p.name), p.id)))


class SearchUnavailable(Exception):
    """The read failed; its SQLSTATE is logged, never the typed text."""


def _arm(key: str) -> tuple[str | None, str | None]:
    """A key's prefix pattern and fuzzy key, both None once no letter is left (a PSČ; 'ulice .' to the street arms),
    where the pattern would be LIKE '%' over every name. The key's alphabet is [0-9a-z ], so it needs no escaping."""
    if not _LETTER.search(key):
        return None, None
    return key + "%", key if len(key) >= _FUZZY_MIN_CHARS else None


def _street_keys(part: str) -> list[str]:
    """The exact keys a door's street part names a street by: as typed, without the generic word ("Nádražní ulice"),
    and each in every spelling the resolver's abbreviation folds make equal, on both sides as the resolver folds them
    ("nám. Míru" reaches the register's náměstí Míru, "náměstí Svobody" its "Nám. Svobody", "ČSA" its "Československé
    armády"). The key as typed stays, as the register spells some streets with the word ("Nová ulice")."""
    keys = {normalize_street_name(part), normalize_street_name(strip_street_generic(part))}
    return sorted(set().union(*(abbreviation_spellings(k) | {k} for k in keys)) - {""})


def _door(row: tuple[Any, ...]) -> Place:
    kod, label, context, lat, lng, street_code, street_name, street_context = row
    street = None if street_code is None else Place("ulice", int(street_code), street_name, street_context)
    return Place(_DOOR_LEVEL, int(kod), label, context, lat, lng, street=street)


@dataclass(frozen=True)
class _Reading:
    """One way to read the text as an address: its street part, number slots and town key (None: every town)."""

    street: str
    domovni: int | None
    typ: str | None
    orientacni: int | None
    znak: str | None
    bare: int | None  # tried as the č.o. alone
    town: str | None

    def params(self, limit: int) -> dict[str, Any]:
        return {"street_keys": _street_keys(self.street), "part_key": normalize_name(self.street),
                "domovni": self.domovni, "orientacni": self.orientacni, "znak": self.znak, "typ": self.typ or TYP_CP,
                "bare": self.bare, "town": self.town, "limit": limit}


def _address(text: str, town: str | None) -> _Reading | None:
    street_part, slots = split_street_and_number(text)
    domovni, typ = house_number(slots)
    if domovni is None:
        lettered = _LETTERED.match(text.strip())
        if lettered is None:
            return None
        return _Reading(lettered["street"], None, None, None, lettered["znak"].lower(), int(lettered["co"]), town)
    orientacni = int(str(slots["cislo_orientacni"])) if "cislo_orientacni" in slots else None
    return _Reading(street_part, domovni, typ, orientacni, slots.get("znak_orientacniho"),
                    domovni if orientacni is None and typ is None else None, town)


def _in_town(text: str, town: str) -> _Reading | None:
    """None, not a reading in every town, when the town piece has no letter to name a town by (a PSČ)."""
    key = normalize_name(town)
    return _address(text, key) if _LETTER.search(key) else None


def _addresses(query: str) -> list[_Reading]:
    """The readings in the order they are tried; the whole text's, as #2451 read it, always last."""
    readings: list[_Reading | None] = []
    pieces = [piece.strip() for piece in query.split(",")]
    if len(pieces) == 2 and all(pieces):
        left, right = pieces
        readings += [_in_town(left, right), _in_town(right, left)]
    elif len(pieces) == 1 and (splits := list(_TOWN_AFTER.finditer(query))):
        last = splits[-1]
        readings.append(_in_town(query[:last.end(1)], query[last.end():]))
    readings.append(_address(query, None))
    return [r for r in readings if r is not None]


def suggest(conn: psycopg.Connection, query: str, *, limit: int) -> list[Place]:
    unit_key, street_key = normalize_name(query), normalize_street_name(query)
    unit_prefix, unit_fuzzy = _arm(unit_key)
    if unit_prefix is None:
        return []
    street_prefix, street_fuzzy = _arm(street_key)
    params = {
        "unit_key": unit_key, "unit_prefix": unit_prefix, "unit_fuzzy": unit_fuzzy,
        "street_key": street_key, "street_prefix": street_prefix, "street_fuzzy": street_fuzzy,
        "limit": limit,
    }
    door_params = [bound for bound in (r.params(limit) for r in _addresses(query)) if bound["street_keys"]]
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_BOUNDS_SQL)
            cur.execute(_SUGGEST_SQL, params)
            rows = cur.fetchall()
            doors: list[tuple[Any, ...]] = []
            for bound in door_params:
                cur.execute(_DOORS_SQL, bound)
                doors = cur.fetchall()
                if doors:
                    break
    except psycopg.Error as exc:
        # The SQLSTATE alone: a psycopg message can quote the statement's parameters, the typed text among them.
        LOG.warning("place_search: read failed (SQLSTATE %s)", exc.sqlstate)
        raise SearchUnavailable from None
    names = [(tier, Place(level, int(code), name, context, lat, lng, _places(places)))
             for level, code, name, context, lat, lng, places, tier in rows]
    # A door's street part is an exact name, so doors rank with the exact tier: after the whole text's exact rows
    # ("Praha 6", the register street "Na Hroudách 1"), before what is only similar to it.
    return ([p for tier, p in names if tier == 0] + [_door(row) for row in doors]
            + [p for tier, p in names if tier > 0])[:limit]
