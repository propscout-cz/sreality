"""Own place search (#2449; spec #2441 Part 4): typed text to register places that are already chips.

One read over the current registry version, by name: tiers exact, prefix, then fuzzy (word similarity, only while the
first two fall short of the limit); within a tier, served properties of any deal type (`place_served_counts`), then the
town's latest population, then code (Q12 a: one ranking for every place box), except that a town ranks right above
its own okres and kraj on its tier when it holds one in twenty of that region's served properties (#2612: so not the
village Moravskoslezský Kočov), and above its own část on equal served properties (#2497 ruling 1 as #2548 ruling 2
narrows it: Praha's Karlín, served, goes before the village Karlín). Counts rank rows and are never returned.
A row is a chip: level, RÚIAN code, register name, context (the parent's name) and a fly-to point from `place_facts`,
or none. It answers kraj, okres, obec, část obce and street, never a cadastral area, cohesion region or the country
(Q6 a, Q9). A městská část, no served level (#2380), answers as a place shortcut (#2450): its `places` are its
quarters' chips, the register's `momc_cast_obce` pairs at the index's version, and it ranks by the sum of their served
properties; with no pair at that version it answers nothing. Any unit is also an exact match by its number, typed
in Arabic or Roman numerals, with or without the register's suffix: "Pardubice 2" is Pardubice II, "Plzeň 2" is Plzeň
2-Slovany and "Kolín 3" is Kolín III, while "Praha 1" never makes Praha 10 exact (#2497, #2499). Never reads the place
picks (migration 737): they are its holdout.

A street and a house number, or a village and its č.p., also answer RÚIAN doors (#2451; stories 44, 53): level
`adresni_misto`, kód ADM, the one door label (which names no number for a streetless door, and for a č.ev. door only
its č.o., if any), its town's okres as context, its point, and its street's chip in `street` (null for a streetless
door). A bare number is tried as the č.p. and as the č.o., never as a č.ev.; a letter on it ("12a") is the č.o.'s. Door
rows go after the whole text's exact rows and before its similar ones.

The address may carry its town (#2485): after a comma, before it, or as the words after the number ("Nádražní 12,
Kolín", "Kolín, Nádražní 12", "Nádražní 12 Kolín"). Its doors are then only those in a town of that exact name, an obec,
a část obce or a městská část (a městská část also by its number, as above: "Nádražní 12, Plzeň 2"), every homonym
kept; a town that names nothing answers no door. Each reading is one statement, tried in order until one answers, the
whole text's always last; the name read takes the whole text.

A street or část may carry its town with no number too (#2548): "Masarykova, Brno", "Údolní Brno", "Praha 6 Dejvice".
Read only when neither the whole text nor a door is exact, its rows go after the whole text's prefix tier and before
its fuzzy tier. "okres Benešov" answers okresy alone; "Praha6" reads as "Praha 6"; a street is exact under every
spelling the resolver's abbreviation folds make equal ("nám." and "náměstí"); place names are reached by similarity at
0.55 ("Olomuc" reaches Olomouc), streets at 0.6.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from location_data.name_index import normalize_name, normalize_street_name
from location_data.resolver.composite import WHOLE_LEVELS, abbreviation_spellings
from location_data.resolver.normalize import TYP_CP, house_number, split_street_and_number, strip_street_generic

LOG = logging.getLogger("location_data.place_search")

_FUZZY_MIN_CHARS = 3
_SPELLED_SLOTS = 3
_UNIT_LEVELS = ("kraj", "okres", "obec", "momc", "cast_obce")
# "okres Benešov": the word names the level, the rest the okres (#2548).
_OKRES_WORD = "okres "
# "Praha6" is "Praha 6": a digit typed straight after a letter starts a word of its own (#2548).
_DIGIT_AFTER_LETTER = re.compile(r"(?<=[a-z])(?=[0-9])")
# A text with no comma splits into words on spaces and dashes ("Praha 6 - Dejvice", "Praha 6-Dejvice"); past six words
# it names no place in a town, as no street and town together run longer (#2548).
_WORD_GAP = re.compile(r"[\s\-–]+")
_PAIR_MAX_WORDS = 6
# A town leads its own okres or kraj only with one in twenty of the region's served properties (#2612): on production
# a town named like its okres holds 7.58 % or more, a village named like its region 1.47 % or less.
_REAL_SHARE = 20
_LETTER = re.compile(r"[a-z]")
# "<street> <number> <town>": the town is what follows the LAST number that whitespace and a letter follow, so an
# ordinal ("28. října") and a č.o. ("12/3") never split, and "Praha 6" keeps its digit.
_TOWN_AFTER = re.compile(r"(?<![\w./])(\d{1,6}(?:\s*/\s*\d{1,5}[a-zA-Z]?|[a-zA-Z])?)\s+(?=[^\W\d_])")
# "Nádražní 12a", which the resolver's parser reads as no number: RÚIAN carries a letter on the č.o. alone.
_LETTERED = re.compile(r"^(?P<street>.*\S)\s+(?P<co>\d{1,5})(?P<znak>[a-zA-Z])$")
_DOOR_LEVEL = "adresni_misto"
# A městská část's number as the register writes it, Arabic (Praha 6, Plzeň 2-Slovany) or Roman (Pardubice II), each
# form to the other (#2497).
_ROMAN = {n: ("", "x", "xx", "xxx")[n // 10] + ("", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix")[n % 10]
          for n in range(1, 40)}
_NUMBER_TWIN = {**{str(n): r for n, r in _ROMAN.items()}, **{r: str(n) for n, r in _ROMAN.items()}}

# The door's label: the ONE label (rule 25) over its register fields, as the row will read.
_DOOR_LABEL = """location_display_label(
         s.name, CASE WHEN ap.typ_so = 'č.p.' THEN ap.cislo_domovni::text END,
         ap.cislo_orientacni::text || coalesce(ap.znak_orientacniho, ''),
         o.name, c.name, 'CZ', 'cz')"""
_DOOR_LABEL_JOINS = """
  LEFT JOIN ruian_streets s ON s.id = ap.street_id
  LEFT JOIN ruian_admin_units o ON o.id = ap.obec_unit_id
  LEFT JOIN ruian_admin_units c ON c.id = ap.cast_obce_unit_id"""

# The thresholds are pinned, not inherited. A place name is reached at 0.55, so the typo "olomuc" (0.57) reaches
# Olomouc (#2548); a street keeps 0.6 by its own bound ("brno" reaches Brněnská at exactly 0.6), as streets far
# outnumber places and the measured gain was in place names alone.
_BOUNDS_SQL = (
    "SELECT set_config('statement_timeout', '2s', true),"
    " set_config('pg_trgm.word_similarity_threshold', '0.55', true)"
)

# A street row at the index's version, by the version's membership (location_data/ruian_boundaries.py), under the
# alias `s`; and the parent's name and fly-to point that close every read answering place rows, under the alias `r`.
_STREET_AT_VERSION = """s.first_version_id <= (SELECT id FROM cur) AND s.last_version_id >= (SELECT id FROM cur)
     AND (s.valid_to IS NULL OR s.last_version_id > (SELECT id FROM cur))"""
_ROW_CONTEXT_JOINS = """
  LEFT JOIN ruian_admin_units par ON par.id = r.parent_id AND par.level IN ('kraj', 'okres', 'obec')
  LEFT JOIN place_facts f ON f.level = r.level AND f.code = r.code"""
_SPELLED_PREFIXES = " OR ".join(f"s.name_norm LIKE %(street_spelled_{n})s" for n in range(1, _SPELLED_SLOTS + 1))

# A unit is read AT the index's version (a load closes a re-parented unit before it publishes); a street, which has no
# index, by the version's membership (location_data/ruian_boundaries.py). A NULL prefix or fuzzy key switches its arm
# off. A městská část is a candidate only with pairs at that version, gated in both
# unit arms so one without never counts toward `near` and switches fuzzy off; `unit_id` carries its row to the pairs.
# A street is exact under every spelling the resolver's abbreviation folds make equal (its own index condition), and
# starts under each, so "nám." reaches Praha's náměstí as exactly as Třebíč's "nám." (#2548). `_SPELLED_SLOTS` prefix
# slots, each its own index condition (`LIKE ANY` takes no trigram index) and NULL when unused, cover a text with two
# abbreviations; past that a further spelling is still read when exact, just not by its start.
# A unit is also exact by its number (#2497 a městská část, #2499 every level): named the key's twin (the number in the
# other numeral form), or the key or the twin and then the register's suffix ("Plzeň 2-Slovany", "Liberec I-Staré
# Město"), never a longer number ("Praha 10" for "Praha 1"). The register writes that suffix after a hyphen or a dot
# ("Brusné 1.díl"), never a space: "v" is 5 and also "in", and "Jablonné v Podještědí" names no number. The index has
# lost the hyphen, so `_NUMBERED` reads the unit's own name. The twin's two arms are index conditions of their own; with
# no twin every such key is NULL.
# Ranking (#2497 ruling 1 as #2548 ruling 2 narrows it): a kraj or okres whose tier holds an obec under it (`path <@`,
# the lateral `t`) takes that obec's sort keys (`k_served`, `k_population`, `k_code`) and follows it (`k_after`), the
# nearer region first (`nlevel`); a row lying under an obec of its tier (`k_in_town`: the town's own část or městská
# část) follows that town on equal served properties. Every other row sorts by its own keys.
_NUMBERED = r"' ([IVX]+|[0-9]+)[-.]'"
_SUGGEST_SQL = f"""
WITH cur AS (SELECT id FROM registry_versions WHERE is_current),
units AS (
  SELECT DISTINCT ON (u.level, u.code) u.level, u.code, u.name, u.parent_id, u.id AS unit_id,
         CASE WHEN n.name_kind <> 'qualifier_stripped'
                   AND (n.name_norm = %(unit_key)s OR n.name_norm = %(unit_twin)s
                        OR ((n.name_norm LIKE %(unit_suffix)s OR n.name_norm LIKE %(unit_twin_suffix)s)
                            AND u.name ~ {_NUMBERED}))
              THEN 0 ELSE 1 END AS tier
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE n.registry_version_id = (SELECT id FROM cur)
     AND (n.name_norm LIKE %(unit_prefix)s
          OR n.name_norm = %(unit_twin)s
          OR (n.name_norm LIKE %(unit_twin_suffix)s AND u.name ~ {_NUMBERED}))
     AND n.entity_kind = ANY(%(levels)s::ruian_level[])
     AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id
     AND (u.level <> 'momc' OR EXISTS (SELECT 1 FROM ruian_admin_unit_relations rel
                                        WHERE rel.from_id = u.id AND rel.relation_type = 'momc_cast_obce'
                                          AND rel.registry_version_id = (SELECT id FROM cur)))
   ORDER BY u.level, u.code, tier, u.valid_from DESC),
streets AS (
  SELECT DISTINCT ON (s.code) 'ulice'::ruian_level AS level, s.code, s.name, s.obec_unit_id AS parent_id,
         NULL::bigint AS unit_id, CASE WHEN s.name_norm = ANY(%(street_keys)s::text[]) THEN 0 ELSE 1 END AS tier
    FROM ruian_streets s
   WHERE (s.name_norm LIKE %(street_prefix)s OR s.name_norm = ANY(%(street_keys)s::text[]) OR {_SPELLED_PREFIXES})
     AND {_STREET_AT_VERSION}
   ORDER BY s.code, tier, s.valid_from DESC),
near AS (SELECT * FROM units UNION ALL SELECT * FROM streets),
fuzzy AS (
  SELECT u.level, u.code, u.name, u.parent_id, u.id AS unit_id, 2 AS tier
    FROM ruian_name_index n
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
   WHERE (SELECT count(*) FROM near) < %(limit)s
     AND %(unit_fuzzy)s <%% n.name_norm
     AND n.registry_version_id = (SELECT id FROM cur)
     AND n.entity_kind = ANY(%(levels)s::ruian_level[])
     AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id
     AND (u.level <> 'momc' OR EXISTS (SELECT 1 FROM ruian_admin_unit_relations rel
                                        WHERE rel.from_id = u.id AND rel.relation_type = 'momc_cast_obce'
                                          AND rel.registry_version_id = (SELECT id FROM cur)))
  UNION ALL
  SELECT 'ulice'::ruian_level, s.code, s.name, s.obec_unit_id, NULL::bigint, 2
    FROM ruian_streets s
   WHERE (SELECT count(*) FROM near) < %(limit)s
     AND %(street_fuzzy)s <%% s.name_norm AND word_similarity(%(street_fuzzy)s, s.name_norm) >= 0.6::real
     AND {_STREET_AT_VERSION}),
hits AS (
  SELECT DISTINCT ON (a.level, a.code) a.*
    FROM (SELECT * FROM near UNION ALL SELECT * FROM fuzzy) a
   ORDER BY a.level, a.code, a.tier),
quarters AS (
  SELECT rel.from_id AS unit_id, q.level, q.code, q.name, q.parent_id
    FROM ruian_admin_unit_relations rel JOIN ruian_admin_units q ON q.id = rel.to_id
   WHERE rel.relation_type = 'momc_cast_obce' AND rel.registry_version_id = (SELECT id FROM cur)
     AND rel.from_id IN (SELECT h.unit_id FROM hits h WHERE h.level = 'momc')),
scored AS (
  SELECT h.*, coalesce(c.n, parts.n, 0) AS served, pop.population, hu.path
    FROM hits h
    LEFT JOIN ruian_admin_units hu ON hu.id = h.unit_id
    LEFT JOIN place_served_counts c ON c.level = h.level AND c.code = h.code
    LEFT JOIN LATERAL (SELECT sum(qc.n) AS n FROM quarters q
                         JOIN place_served_counts qc ON qc.level = q.level AND qc.code = q.code
                        WHERE q.unit_id = h.unit_id) parts ON true
    LEFT JOIN LATERAL (SELECT p.population FROM obec_population p
                        WHERE h.level = 'obec' AND p.obec_kod = h.code
                        ORDER BY p.year DESC LIMIT 1) pop ON true),
ranked AS (
  SELECT s.*, coalesce(t.served, s.served) AS k_served,
         CASE WHEN t.code IS NULL THEN s.population ELSE t.population END AS k_population,
         coalesce(t.code, s.code) AS k_code, t.code IS NOT NULL AS k_after,
         EXISTS (SELECT 1 FROM scored o WHERE s.level <> 'obec' AND o.level = 'obec' AND o.tier = s.tier
                                          AND s.path <@ o.path) AS k_in_town
    FROM scored s
    LEFT JOIN LATERAL (SELECT o.served, o.population, o.code FROM scored o
                        WHERE s.level IN ('kraj', 'okres') AND o.level = 'obec' AND o.tier = s.tier
                          AND o.path <@ s.path AND o.served * {_REAL_SHARE} >= s.served
                        ORDER BY o.served DESC, o.population DESC NULLS LAST, o.code LIMIT 1) t ON true
   ORDER BY s.tier, k_served DESC, k_in_town, k_population DESC NULLS LAST, k_code, k_after,
            nlevel(s.path) DESC NULLS LAST, s.level
   LIMIT %(limit)s)
SELECT r.level::text, r.code, r.name, par.name, f.lat, f.lng,
       (SELECT json_agg(json_build_array(q.level, q.code, q.name, qp.name)) FROM quarters q
          LEFT JOIN ruian_admin_units qp ON qp.id = q.parent_id AND qp.level IN ('kraj', 'okres', 'obec')
         WHERE q.unit_id = r.unit_id),
       r.tier
  FROM ranked r{_ROW_CONTEXT_JOINS}
 ORDER BY r.tier, r.k_served DESC, r.k_in_town, r.k_population DESC NULLS LAST, r.k_code, r.k_after,
          nlevel(r.path) DESC NULLS LAST, r.level
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
# homonyms all kept, and also by its number as the name read takes it (#2497, #2499: "Plzeň 2" is Plzeň 2-Slovany,
# "Kolín 3" is Kolín III), each arm an index condition. The read narrows a list and binds nothing, so the resolver's whole-string
# ladder and its fail-closed rule do not apply, but its levels do (an okres or a kraj is a region, never the town).
# Streets and villages narrow to its obce, and doors to those that lie in it: its obec or část by code, or its městská
# část through every row of the code (a street runs through several of either). With no town each condition folds away
# at plan time: the statement is unnamed, its parameters planned in.
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
   WHERE n.registry_version_id = (SELECT id FROM cur)
     AND (n.name_norm = %(town)s::text
          OR n.name_norm = %(town_twin)s::text
          OR (n.name_norm LIKE %(town_suffix)s::text AND u.name ~ {_NUMBERED})
          OR (n.name_norm LIKE %(town_twin_suffix)s::text AND u.name ~ {_NUMBERED}))
     AND n.entity_kind IN ({_TOWN_LEVELS})
     AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id),
town_obce AS (SELECT o.id FROM ruian_admin_units o WHERE o.level = 'obec' AND o.code IN (SELECT obec_code FROM town)),
streets AS (
  SELECT DISTINCT ON (s.code) s.code, s.name, s.obec_unit_id
    FROM ruian_streets s
   WHERE s.name_norm = ANY(%(street_keys)s::text[])
     AND {_STREET_AT_VERSION}
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

# A STREET OR ČÁST WITH ITS TOWN, NO NUMBER (#2548): "Masarykova, Brno", "Brno, Masarykova", "Údolní Brno", "Praha 6
# Dejvice". Every split of the text is one pair, each piece tried as the place and as the town; one statement reads
# them all, as a text that names neither (most of them) must cost one round trip. The town is the door read's (an obec,
# část obce or městská část at the index's version, by its exact name or its number's other form; the suffix form,
# "Plzeň 2" for Plzeň 2-Slovany, only reaches the door read), every homonym kept. It answers the streets of its obce
# and, for an obec or a městská část, its části obce (a městská část's through its pairs), exact (under every spelling
# for a street) before by their start, then served properties, then code. The comma split is the door read's
# (`_comma_sides`); the word split also takes dashes ("Praha 6-Dejvice", "Praha 6 - Dejvice").
_IN_TOWN_SQL = f"""
WITH cur AS (SELECT id FROM registry_versions WHERE is_current),
pairs AS (
  SELECT * FROM jsonb_to_recordset(%(pairs)s::jsonb)
         AS p(place_key text, place_prefix text, street_keys text[], street_prefix text, town text, twin text)),
town AS (
  SELECT DISTINCT p.place_key, p.place_prefix, p.street_keys, p.street_prefix, u.level, u.id AS unit_id,
         CASE WHEN u.level = 'obec' THEN u.code ELSE po.code END AS obec_code
    FROM ruian_name_index n
    JOIN pairs p ON n.name_norm = p.town OR n.name_norm = p.twin
    JOIN ruian_admin_units u ON u.id = n.entity_id AND u.level = n.entity_kind
    LEFT JOIN ruian_admin_units po ON po.id = u.parent_id AND po.level = 'obec'
   WHERE n.registry_version_id = (SELECT id FROM cur) AND n.name_norm = ANY(%(town_keys)s::text[])
     AND n.entity_kind IN ({_TOWN_LEVELS})
     AND (SELECT id FROM cur) BETWEEN u.first_version_id AND u.last_version_id),
obce AS (
  SELECT t.*, o.id AS obec_id FROM town t JOIN ruian_admin_units o ON o.level = 'obec' AND o.code = t.obec_code),
found AS (
  (SELECT DISTINCT ON (s.code) 'ulice'::ruian_level AS level, s.code, s.name, s.obec_unit_id AS parent_id,
          CASE WHEN s.name_norm = ANY(b.street_keys) THEN 0 ELSE 1 END AS tier
     FROM obce b JOIN ruian_streets s ON s.obec_unit_id = b.obec_id
    WHERE (s.name_norm = ANY(b.street_keys) OR s.name_norm LIKE b.street_prefix)
      AND {_STREET_AT_VERSION}
    ORDER BY s.code, tier, s.valid_from DESC)
  UNION ALL
  (SELECT DISTINCT ON (q.code) q.level, q.code, q.name, q.parent_id,
          CASE WHEN q.name_norm = b.place_key THEN 0 ELSE 1 END AS tier
     FROM obce b JOIN ruian_admin_units q ON q.level = 'cast_obce' AND q.parent_id = b.obec_id
    WHERE b.level IN ('obec', 'momc') AND (q.name_norm = b.place_key OR q.name_norm LIKE b.place_prefix)
      AND (SELECT id FROM cur) BETWEEN q.first_version_id AND q.last_version_id
      AND (b.level <> 'momc' OR EXISTS (SELECT 1 FROM ruian_admin_unit_relations rel
                                         WHERE rel.from_id = b.unit_id AND rel.to_id = q.id
                                           AND rel.relation_type = 'momc_cast_obce'
                                           AND rel.registry_version_id = (SELECT id FROM cur)))
    ORDER BY q.code, tier, q.valid_from DESC)),
ranked AS (
  SELECT f.*, coalesce(c.n, 0) AS served
    FROM found f LEFT JOIN place_served_counts c ON c.level = f.level AND c.code = f.code
   ORDER BY f.tier, served DESC, f.code, f.level
   LIMIT %(limit)s)
SELECT r.level::text, r.code, r.name, par.name, f.lat, f.lng
  FROM ranked r{_ROW_CONTEXT_JOINS}
 ORDER BY r.tier, r.served DESC, r.code, r.level
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


def _by_number(key: str | None) -> tuple[str | None, str | None, str | None]:
    """A unit's names by the key's number (#2497, #2499): the twin, the key with its last token, a number 1-39, in the
    other numeral form ("pardubice 2" and "pardubice ii"), then the key and the twin each followed by the register's
    suffix, as LIKE patterns ("plzen 2 %"). All None when the key ends in no such number or is the number alone."""
    base, _, last = (key or "").rpartition(" ")
    number = _NUMBER_TWIN.get(last)
    if not base or number is None:
        return None, None, None
    twin = f"{base} {number}"
    return twin, f"{key} %", f"{twin} %"


def _unit_key(text: str) -> str:
    """A text's place-name key, a digit typed straight after a letter starting a word ("Praha6" is "praha 6")."""
    return _DIGIT_AFTER_LETTER.sub(" ", normalize_name(text))


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
        town_twin, town_suffix, town_twin_suffix = _by_number(self.town)
        return {"street_keys": _street_keys(self.street), "part_key": normalize_name(self.street),
                "domovni": self.domovni, "orientacni": self.orientacni, "znak": self.znak, "typ": self.typ or TYP_CP,
                "bare": self.bare, "town": self.town, "town_twin": town_twin, "town_suffix": town_suffix,
                "town_twin_suffix": town_twin_suffix, "limit": limit}


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
    key = _unit_key(town)
    return _address(text, key) if _LETTER.search(key) else None


def _comma_sides(query: str) -> tuple[str, str] | None:
    """The two non-empty sides of a text's one comma; None for no comma, two or more, or an empty side."""
    pieces = [piece.strip() for piece in query.split(",")]
    return (pieces[0], pieces[1]) if len(pieces) == 2 and all(pieces) else None


def _addresses(query: str) -> list[_Reading]:
    """The readings in the order they are tried; the whole text's, as #2451 read it, always last."""
    readings: list[_Reading | None] = []
    if sides := _comma_sides(query):
        left, right = sides
        readings += [_in_town(left, right), _in_town(right, left)]
    elif "," not in query and (splits := list(_TOWN_AFTER.finditer(query))):
        last = splits[-1]
        readings.append(_in_town(query[:last.end(1)], query[last.end():]))
    readings.append(_address(query, None))
    return [r for r in readings if r is not None]


@dataclass(frozen=True)
class _Pair:
    """One (place, town) reading's keys, as `_IN_TOWN_SQL`'s `pairs` record names them."""

    place_key: str
    place_prefix: str
    street_keys: list[str]
    street_prefix: str
    town: str
    twin: str | None

    @classmethod
    def of(cls, place: str, town: str) -> _Pair | None:
        """None when either piece has no letter to name a place by."""
        place_key, street_key, town_key = normalize_name(place), normalize_street_name(place), _unit_key(town)
        if not (_LETTER.search(place_key) and _LETTER.search(street_key) and _LETTER.search(town_key)):
            return None
        return cls(place_key, f"{place_key}%", _street_keys(place), f"{street_key}%", town_key,
                   _by_number(town_key)[0])


def _pairs(query: str) -> list[_Pair]:
    """A comma's two sides each way round, or else every split of the words each way round."""
    if sides := _comma_sides(query):
        splits = [sides]
    else:
        words = [word for word in _WORD_GAP.split(query.strip()) if word] if "," not in query else []
        if not 2 <= len(words) <= _PAIR_MAX_WORDS:
            return []
        splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
    pairs = (_Pair.of(*pair) for left, right in splits for pair in ((left, right), (right, left)))
    return [pair for pair in pairs if pair]


def _unique(places: list[Place]) -> list[Place]:
    """The first row of each place: one found in its town comes again as only similar to the whole text."""
    seen: set[tuple[str, int]] = set()
    unique = []
    for place in places:
        if (place.level, place.id) not in seen:
            seen.add((place.level, place.id))
            unique.append(place)
    return unique


def suggest(conn: psycopg.Connection, query: str, *, limit: int) -> list[Place]:
    unit_key, street_key = _unit_key(query), normalize_street_name(query)
    okres_only = unit_key.startswith(_OKRES_WORD) and bool(_LETTER.search(unit_key[len(_OKRES_WORD):]))
    if okres_only:  # an okres alone: no street, door or place in a town
        unit_key, street_key = unit_key[len(_OKRES_WORD):], ""
    unit_prefix, unit_fuzzy = _arm(unit_key)
    if unit_prefix is None:
        return []
    street_prefix, street_fuzzy = _arm(street_key)
    street_keys = _street_keys(query) if street_prefix else []
    spelled = [f"{key}%" for key in street_keys if key != street_key][:_SPELLED_SLOTS]
    spelled += [None] * (_SPELLED_SLOTS - len(spelled))
    unit_twin, unit_suffix, unit_twin_suffix = _by_number(unit_key)
    params = {
        "unit_key": unit_key, "unit_prefix": unit_prefix, "unit_fuzzy": unit_fuzzy,
        "unit_twin": unit_twin, "unit_suffix": unit_suffix, "unit_twin_suffix": unit_twin_suffix,
        "street_keys": street_keys, "street_prefix": street_prefix, "street_fuzzy": street_fuzzy,
        **{f"street_spelled_{n}": prefix for n, prefix in enumerate(spelled, 1)},
        "levels": ["okres"] if okres_only else list(_UNIT_LEVELS), "limit": limit,
    }
    readings = [] if okres_only else _addresses(query)
    door_params = [bound for bound in (r.params(limit) for r in readings) if bound["street_keys"]]
    pairs = [] if okres_only else _pairs(query)
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
            in_town: list[tuple[Any, ...]] = []
            if pairs and not doors and not any(tier == 0 for *_, tier in rows):
                town_keys = sorted({key for pair in pairs for key in (pair.town, pair.twin) if key})
                cur.execute(_IN_TOWN_SQL, {"pairs": Jsonb([asdict(pair) for pair in pairs]), "town_keys": town_keys,
                                           "limit": limit})
                in_town = cur.fetchall()
    except psycopg.Error as exc:
        # The SQLSTATE alone: a psycopg message can quote the statement's parameters, the typed text among them.
        LOG.warning("place_search: read failed (SQLSTATE %s)", exc.sqlstate)
        raise SearchUnavailable from None
    names = [(tier, Place(level, int(code), name, context, lat, lng, _places(places)))
             for level, code, name, context, lat, lng, places, tier in rows]
    # A door's street part is an exact name, so doors rank with the exact tier: after the whole text's exact rows
    # ("Praha 6", the register street "Na Hroudách 1"), before its prefix and fuzzy rows. A place found in its town is
    # read only when neither answered; it goes after the prefix tier ("Frýdek-Mís" is the town typed so far before it is
    # Frýdek's street Mís...) and before the fuzzy tier.
    found = [Place(level, int(code), name, context, lat, lng) for level, code, name, context, lat, lng in in_town]
    return _unique([p for tier, p in names if tier == 0] + [_door(row) for row in doors]
                   + [p for tier, p in names if tier == 1] + found + [p for tier, p in names if tier > 1])[:limit]
