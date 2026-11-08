"""Own place search (#2449; spec #2441 Part 4): typed text to register places that are already chips.

One read over the current registry version, by name: tiers exact, prefix, then fuzzy (word similarity, only while the
first two fall short of the limit). A row is a chip: level, RÚIAN code, register name, context (the parent's name) and
a fly-to point from `place_facts`, or none. Never reads `place_picks`: they are its holdout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import psycopg

from location_data.name_index import normalize_name, normalize_street_name

LOG = logging.getLogger("location_data.place_search")

_FUZZY_MIN_CHARS = 3

# The threshold is pinned, not inherited: "brno" reaches Brněnská at exactly 0.6.
_BOUNDS_SQL = (
    "SELECT set_config('statement_timeout', '2s', true),"
    " set_config('pg_trgm.word_similarity_threshold', '0.6', true)"
)

_SUGGEST_SQL = """
WITH cur AS (SELECT id FROM registry_versions WHERE is_current),
units AS (
  SELECT DISTINCT ON (u.level, u.code) u.level, u.code, u.name, u.parent_id,
         CASE WHEN n.name_norm = %(unit_key)s AND n.name_kind <> 'qualifier_stripped' THEN 0 ELSE 1 END AS tier
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE n.registry_version_id = (SELECT id FROM cur)
     AND n.name_norm LIKE %(unit_prefix)s
   ORDER BY u.level, u.code, tier, u.valid_from DESC),
streets AS (
  SELECT DISTINCT ON (s.code) 'ulice'::ruian_level AS level, s.code, s.name, s.obec_unit_id AS parent_id,
         CASE WHEN s.name_norm = %(street_key)s THEN 0 ELSE 1 END AS tier
    FROM ruian_streets s
   WHERE s.name_norm LIKE %(street_prefix)s
   ORDER BY s.code, tier, s.valid_from DESC),
near AS (SELECT * FROM units UNION ALL SELECT * FROM streets),
fuzzy AS (
  SELECT u.level, u.code, u.name, u.parent_id, 2 AS tier
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE (SELECT count(*) FROM near) < %(limit)s
     AND %(unit_fuzzy)s <%% n.name_norm
     AND n.registry_version_id = (SELECT id FROM cur)
  UNION ALL
  SELECT 'ulice'::ruian_level, s.code, s.name, s.obec_unit_id, 2
    FROM ruian_streets s
   WHERE (SELECT count(*) FROM near) < %(limit)s
     AND %(street_fuzzy)s <%% s.name_norm),
hits AS (
  SELECT DISTINCT ON (a.level, a.code) a.*
    FROM (SELECT * FROM near UNION ALL SELECT * FROM fuzzy) a
   ORDER BY a.level, a.code, a.tier)
SELECT h.level::text, h.code, h.name, par.name, f.lat, f.lng
  FROM hits h
  LEFT JOIN ruian_admin_units par ON par.id = h.parent_id AND par.level IN ('kraj', 'okres', 'obec')
  LEFT JOIN place_facts f ON f.level = h.level AND f.code = h.code
 ORDER BY h.tier, h.code, h.level
 LIMIT %(limit)s
"""


@dataclass(frozen=True)
class Place:
    level: str
    id: int
    name: str
    context: str | None
    lat: float | None
    lng: float | None

    def item(self) -> dict[str, Any]:
        point = None if self.lat is None or self.lng is None else {"lat": self.lat, "lng": self.lng}
        return {"level": self.level, "id": self.id, "name": self.name, "context": self.context, "point": point}


def _fuzzy_key(key: str) -> str | None:
    return key if len(key) >= _FUZZY_MIN_CHARS else None


def suggest(conn: psycopg.Connection, query: str, *, limit: int) -> list[Place]:
    unit_key = normalize_name(query)
    street_key = normalize_street_name(query)
    params = {
        "unit_key": unit_key, "unit_prefix": unit_key + "%", "unit_fuzzy": _fuzzy_key(unit_key),
        "street_key": street_key, "street_prefix": street_key + "%", "street_fuzzy": _fuzzy_key(street_key),
        "limit": limit,
    }
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_BOUNDS_SQL)
        cur.execute(_SUGGEST_SQL, params)
        rows = cur.fetchall()
    return [Place(level, int(code), name, context, lat, lng) for level, code, name, context, lat, lng in rows]
