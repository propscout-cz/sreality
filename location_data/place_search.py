"""Own place search (#2449; spec #2441 Part 4): typed text to register places that are already chips.

One read over the current registry version, by name: tiers exact, prefix, then fuzzy (word similarity, only while the
first two fall short of the limit); within a tier, served properties of any deal type (`place_served_counts`), then the
town's latest population, then code (Q12 a: one ranking for every place box). Counts rank rows and are never returned.
A row is a chip: level, RÚIAN code, register name, context (the parent's name) and a fly-to point from `place_facts`,
or none. It answers kraj, okres, obec, část obce and street, never a cadastral area, cohesion region or the country
(Q6 a, Q9). A městská část, no served level (#2380), answers as a place shortcut (#2450): its `places` are its
quarters' chips, the register's `momc_cast_obce` pairs at the index's version, and it ranks by the sum of their served
properties; with no pair at that version it answers nothing. Never reads the place picks (migration 737): they are its
holdout.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import psycopg

from location_data.name_index import normalize_name, normalize_street_name
from location_data.resolver.normalize import TYP_CP, house_number, split_street_and_number

LOG = logging.getLogger("location_data.place_search")

_FUZZY_MIN_CHARS = 3
_LETTER = re.compile(r"[a-z]")
DOOR_LEVEL = "adresni_misto"

# The door's label: the ONE label (rule 25) over its register fields, as the row will read. Shared with the address
# picker's preview (api/maps.py imports DOOR_LABEL_SQL until the Mapy path goes, #2454).
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
   ORDER BY h.tier, served DESC, pop.population DESC NULLS LAST, h.code, h.level
   LIMIT %(limit)s)
SELECT r.level::text, r.code, r.name, par.name, f.lat, f.lng,
       (SELECT json_agg(json_build_array(q.level, q.code, q.name, qp.name)) FROM quarters q
          LEFT JOIN ruian_admin_units qp ON qp.id = q.parent_id AND qp.level IN ('kraj', 'okres', 'obec')
         WHERE q.unit_id = r.unit_id),
       r.tier
  FROM ranked r
  LEFT JOIN ruian_admin_units par ON par.id = r.parent_id AND par.level IN ('kraj', 'okres', 'obec')
  LEFT JOIN place_facts f ON f.level = r.level AND f.code = r.code
 ORDER BY r.tier, r.served DESC, r.population DESC NULLS LAST, r.code, r.level
"""

# THE DOOR READ (#2451): one batched read over EVERY exact namesake street, and every exact namesake část obce for a
# streetless door, ranked as the name read ranks (Q12 a). Every namesake, not the top few: about 77 % of street names
# repeat in another town and only about one Nádražní in seven has a door 12, so a short list answers almost nothing.
# The LIMIT 1000 per kind is a guard against a runaway name, not a ranking (the largest, Zahradní, is 705 streets).
_DOORS_SQL = f"""
WITH cur AS (SELECT id FROM registry_versions WHERE is_current),
streets AS (
  SELECT DISTINCT ON (s.code) s.code, s.name, s.obec_unit_id
    FROM ruian_streets s
   WHERE s.name_norm = ANY(%(street_keys)s::text[])
     AND s.first_version_id <= (SELECT id FROM cur) AND s.last_version_id >= (SELECT id FROM cur)
     AND (s.valid_to IS NULL OR s.last_version_id > (SELECT id FROM cur))
   ORDER BY s.code, s.valid_from DESC),
parts AS (
  SELECT DISTINCT ON (u.code) u.code, u.parent_id
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE n.registry_version_id = (SELECT id FROM cur) AND n.name_norm = %(part_key)s::text
     AND n.entity_kind = 'cast_obce' AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id
   ORDER BY u.code, u.valid_from DESC),
candidates AS (
  (SELECT coalesce(c.n, 0) AS served, st.code AS street_code, st.name AS street_name,
          st.obec_unit_id AS street_obec, NULL::bigint AS part_code, NULL::bigint AS part_parent
     FROM streets st LEFT JOIN place_served_counts c ON c.level = 'ulice' AND c.code = st.code
    ORDER BY 1 DESC, 2 LIMIT 1000)
  UNION ALL
  (SELECT coalesce(c.n, 0), NULL, NULL, NULL, p.code, p.parent_id
     FROM parts p LEFT JOIN place_served_counts c ON c.level = 'cast_obce' AND c.code = p.code
    ORDER BY 1 DESC, 5 LIMIT 1000)),
doors AS (
  SELECT k.served, k.street_code, k.street_name, k.street_obec, a.kod_adm
    FROM candidates k
    JOIN ruian_streets sr ON sr.code = k.street_code
    CROSS JOIN LATERAL (
      SELECT ap.kod_adm FROM ruian_address_points ap
       WHERE ap.street_id = sr.id AND ap.cislo_domovni = %(domovni)s::integer AND ap.typ_so = %(typ)s::text
         AND (%(orientacni)s::integer IS NULL OR ap.cislo_orientacni = %(orientacni)s::integer)
         AND (%(znak)s::text IS NULL OR lower(ap.znak_orientacniho) = %(znak)s::text)
         AND ap.valid_to IS NULL AND ap.first_version_id <= (SELECT id FROM cur)
      UNION
      SELECT ap.kod_adm FROM ruian_address_points ap
       WHERE ap.street_id = sr.id AND ap.cislo_orientacni = %(bare)s::integer
         AND ap.valid_to IS NULL AND ap.first_version_id <= (SELECT id FROM cur)) a
  UNION ALL
  SELECT k.served, NULL, NULL, NULL, ap.kod_adm
    FROM candidates k
    JOIN ruian_admin_units po ON po.id = k.part_parent AND po.level = 'obec'
    JOIN ruian_admin_units ob ON ob.level = 'obec' AND ob.code = po.code
    JOIN ruian_address_points ap ON ap.obec_unit_id = ob.id
   WHERE ap.cislo_domovni = %(domovni)s::integer AND ap.typ_so = %(typ)s::text
     AND ap.street_id IS NULL AND ap.cast_obce_kod = k.part_code
     AND %(orientacni)s::integer IS NULL AND ap.valid_to IS NULL AND ap.first_version_id <= (SELECT id FROM cur)),
top AS (SELECT * FROM doors ORDER BY served DESC, kod_adm LIMIT %(limit)s)
SELECT d.kod_adm, {_DOOR_LABEL}, tc.name, ST_Y(ap.geom), ST_X(ap.geom), d.street_code, d.street_name, so.name
  FROM top d
  JOIN ruian_address_points ap ON ap.kod_adm = d.kod_adm{_DOOR_LABEL_JOINS}
  LEFT JOIN ruian_admin_units tc ON tc.id = o.parent_id AND tc.level IN ('kraj', 'okres', 'obec')
  LEFT JOIN ruian_admin_units so ON so.id = d.street_obec AND so.level IN ('kraj', 'okres', 'obec')
 ORDER BY d.served DESC, d.kod_adm
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
        if self.level == DOOR_LEVEL:
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


def _door(row: tuple[Any, ...]) -> Place:
    kod, label, context, lat, lng, street_code, street_name, street_context = row
    street = None if street_code is None else Place("ulice", int(street_code), street_name, street_context)
    return Place(DOOR_LEVEL, int(kod), label, context, lat, lng, street=street)


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
    street_part, slots = split_street_and_number(query)
    domovni, typ = house_number(slots)
    orientacni = int(str(slots["cislo_orientacni"])) if "cislo_orientacni" in slots else None
    znak = slots.get("znak_orientacniho")
    street_keys = [normalize_street_name(street_part)] if domovni is not None else []
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_BOUNDS_SQL)
            cur.execute(_SUGGEST_SQL, params)
            rows = cur.fetchall()
            doors: list[tuple[Any, ...]] = []
            if street_keys:
                cur.execute(_DOORS_SQL, {"street_keys": street_keys, "part_key": normalize_name(street_part),
                                         "domovni": domovni, "orientacni": orientacni, "znak": znak,
                                         "typ": typ or TYP_CP,
                                         "bare": domovni if orientacni is None and typ is None else None,
                                         "limit": limit})
                doors = cur.fetchall()
    except psycopg.Error as exc:
        # The SQLSTATE alone: a psycopg message can quote the statement's parameters, the typed text among them.
        LOG.warning("place_search: read failed (SQLSTATE %s)", exc.sqlstate)
        raise SearchUnavailable from None
    names = [(tier, Place(level, int(code), name, context, lat, lng, _places(places)))
             for level, code, name, context, lat, lng, places, tier in rows]
    return ([p for tier, p in names if tier == 0] + [_door(row) for row in doors]
            + [p for tier, p in names if tier > 0])[:limit]
