"""Own place search (#2449; spec #2441 Part 4): typed text to register places that are already chips.

One read over the current registry version, by name: tiers exact, prefix, then fuzzy (word similarity, only while the
first two fall short of the limit); within a tier, served properties of any deal type (`place_served_counts`), then the
town's latest population, then code (Q12 a: one ranking for every place box). Counts rank rows and are never returned.
A row is a chip: level, RÚIAN code, register name, context (the parent's name) and a fly-to point from `place_facts`,
or none. It answers kraj, okres, obec, část obce and street, never a cadastral area, městská část, cohesion region or
the country (Q6 a, Q9). Never reads the place picks (migration 737): they are its holdout.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import psycopg

from location_data.name_index import normalize_name, normalize_street_name

LOG = logging.getLogger("location_data.place_search")

_FUZZY_MIN_CHARS = 3
_LETTER = re.compile(r"[a-z]")

# The threshold is pinned, not inherited: "brno" reaches Brněnská at exactly 0.6.
_BOUNDS_SQL = (
    "SELECT set_config('statement_timeout', '2s', true),"
    " set_config('pg_trgm.word_similarity_threshold', '0.6', true)"
)

# A unit is read AT the index's version, as api/maps.py's part lookup reads it (a load closes a re-parented unit before
# it publishes); a street, which has no index, by the version's membership (location_data/ruian_boundaries.py). A NULL
# prefix or fuzzy key switches its arm off.
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
  SELECT h.*, coalesce(c.n, 0) AS served, pop.population
    FROM hits h
    LEFT JOIN place_served_counts c ON c.level = h.level AND c.code = h.code
    LEFT JOIN LATERAL (SELECT p.population FROM obec_population p
                        WHERE h.level = 'obec' AND p.obec_kod = h.code
                        ORDER BY p.year DESC LIMIT 1) pop ON true
   ORDER BY h.tier, served DESC, pop.population DESC NULLS LAST, h.code, h.level
   LIMIT %(limit)s)
SELECT r.level::text, r.code, r.name, par.name, f.lat, f.lng,
       (SELECT json_agg(json_build_array(q.level, q.code, q.name, qp.name)) FROM quarters q
          LEFT JOIN ruian_admin_units qp ON qp.id = q.parent_id AND qp.level IN ('kraj', 'okres', 'obec')
         WHERE q.unit_id = r.unit_id)
  FROM ranked r
  LEFT JOIN ruian_admin_units par ON par.id = r.parent_id AND par.level IN ('kraj', 'okres', 'obec')
  LEFT JOIN place_facts f ON f.level = r.level AND f.code = r.code
 ORDER BY r.tier, r.served DESC, r.population DESC NULLS LAST, r.code, r.level
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

    def chip(self) -> dict[str, Any]:
        return {"level": self.level, "id": self.id, "name": self.name, "context": self.context}

    def item(self) -> dict[str, Any]:
        point = None if self.lat is None or self.lng is None else {"lat": self.lat, "lng": self.lng}
        item = {**self.chip(), "point": point}
        if self.places is not None:
            item["places"] = [p.chip() for p in self.places]
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
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_BOUNDS_SQL)
            cur.execute(_SUGGEST_SQL, params)
            rows = cur.fetchall()
    except psycopg.Error as exc:
        # The SQLSTATE alone: a psycopg message can quote the statement's parameters, the typed text among them.
        LOG.warning("place_search: read failed (SQLSTATE %s)", exc.sqlstate)
        raise SearchUnavailable from None
    return [Place(level, int(code), name, context, lat, lng, _places(places))
            for level, code, name, context, lat, lng, places in rows]
