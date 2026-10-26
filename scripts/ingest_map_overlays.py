"""Ingest the map reference overlays and render their vector tiles (docs/design/map-overlays.md).

One registry, SOURCES, maps each section to its layers and each layer to its readers. Per
section: the datum control point, every layer fetched under the completeness and shrink
guards, then ONE transaction that replaces the features, upserts the registry rows and runs
map_overlay_render_section. --dry-run fetches and checks everything and writes nothing.
render_sql_section is the same flow for one SQL-sourced section, from the database alone: the
rent-map ingest (api/rent_map.py) re-renders `rent_map` with it inside its own transaction.

    python -m scripts.ingest_map_overlays [--sections metro,flood,railway] [--dry-run] [--accept-shrink]
"""

from __future__ import annotations

import argparse
import functools
import io
import itertools
import json
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal

import psycopg
import requests
from psycopg import sql
from psycopg.types.json import Jsonb

from scraper.db import connect, stamp_derived_artifact
from toolkit.rent_map import MF_INFOGRAPHIC_URL

LOG = logging.getLogger("ingest_map_overlays")

USER_AGENT = "sreality-tracker/1.0 (+https://github.com/waiff/sreality)"
PAUSE_S = 0.5
ATTEMPTS = 5
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
TIMEOUT_S = (15, 300)
CHUNK = 50
INSERT_BATCH = 100
SHRINK_FLOOR = 0.9

# PID open data, stop Florenc M1B (689/101), published in both systems.
DATUM_S_JTSK = (-741651.9, -1042827.75)
DATUM_WGS84 = (14.4369316, 50.09053)
DATUM_TOLERANCE_M = 1.0

POLYGON = "esriGeometryPolygon"
POLYLINE = "esriGeometryPolyline"
POINT = "esriGeometryPoint"

IPR_HOSTED = "https://mp.iprpraha.cz/arcgis/rest/services/Hosted"
IPR_REFERENCED = "https://mp.iprpraha.cz/arcgis/rest/services/ReferencedServices"
IPR_UNIVERSITIES = "FSV_CUR_OV_SKOLYVS_B"
MSMT_F21_URL = "https://dsia.msmt.cz/vystupy/f2/f21.xlsx"
VUV_MAPSERVER = "https://ags2.vuv.cz/arcgis/rest/services/isvs_voda/isvs_voda/MapServer"
# ČÚZK's base topographic database (1:10 000), open data CC BY 4.0 since 2023-07-01; the station
# layer carries the name, the SR70-style station code and stanice / zastávka.
CUZK_ZABAGED = "https://ags.cuzk.gov.cz/arcgis/rest/services/ZABAGED_POLOHOPIS/MapServer"
ZABAGED_STATIONS_LAYER = "Železniční stanice, zastávka"
RAILWAY_FIELDS = ("fid_zbg", "zst_k", "zst_p", "typ")
VUV_ISO_RECORD = "https://heis.vuv.cz/xmicka/record/xml/CZ-VUV-MD-ZaplavUzemi"
RUIAN_ATOM = (
    "https://atom.cuzk.gov.cz/RUIAN-STATY-SHP/datasetFeeds/CZ-00025712-CUZK_RUIAN-STATY-SHP_1.xml"
)

DATUM_CHECK_SQL = """
select st_distance(
         st_transform(st_setsrid(st_makepoint(%s, %s), 5514), 3857),
         st_transform(st_setsrid(st_makepoint(%s, %s), 4326), 3857))
"""

PREVIOUS_COUNTS_SQL = "select key, feature_count from map_overlay_layers where section = %s"

STATEMENT_TIMEOUT_SQL = "set local statement_timeout = '30min'"
LOCK_SQL = "select pg_advisory_xact_lock(hashtextextended(%s, 0))"

UPSERT_SECTION_SQL = """
insert into map_overlay_sections
       (key, label, sort, minzoom, maxzoom, attribution, licence, source_url, note,
        source_revised_at, fetched_at, version)
values (%(key)s, %(label)s, %(sort)s, %(minzoom)s, %(maxzoom)s, %(attribution)s,
        %(licence)s, %(source_url)s, %(note)s, %(revised)s, now(),
        extract(epoch from now())::bigint)
on conflict (key) do update set
       label = excluded.label, sort = excluded.sort,
       minzoom = excluded.minzoom, maxzoom = excluded.maxzoom,
       attribution = excluded.attribution, licence = excluded.licence,
       source_url = excluded.source_url, note = excluded.note,
       source_revised_at = excluded.source_revised_at,
       fetched_at = excluded.fetched_at, version = excluded.version
"""

PRUNE_LAYERS_SQL = "delete from map_overlay_layers where section = %s and key <> all(%s::text[])"

UPSERT_LAYER_SQL = """
insert into map_overlay_layers
       (key, section, label, sort, legend, scale, variants, feature_count)
values (%(key)s, %(section)s, %(label)s, %(sort)s, %(legend)s::jsonb, %(scale)s::jsonb,
        %(variants)s::jsonb, %(feature_count)s)
on conflict (key) do update set
       section = excluded.section, label = excluded.label, sort = excluded.sort,
       legend = excluded.legend, scale = excluded.scale, variants = excluded.variants,
       feature_count = excluded.feature_count
"""

DELETE_FEATURES_SQL = "delete from map_overlay_features where layer_key = %s"

INSERT_FEATURE_SQL = """
insert into map_overlay_features (layer_key, source_id, props, geom)
select %s, %s, %s::jsonb,
       st_subdivide(st_makevalid(st_transform(
           st_setsrid(st_force2d(st_geomfromgeojson(%s::text)), %s), 3857)), 256)
"""

# A `whole` layer keeps each source polygon in one piece: the SPA strokes a scale layer's
# own outline, and a subdivision cut would draw as a border inside the place.
INSERT_WHOLE_FEATURE_SQL = """
insert into map_overlay_features (layer_key, source_id, props, geom)
select %s, %s, %s::jsonb,
       st_makevalid(st_transform(
           st_setsrid(st_force2d(st_geomfromgeojson(%s::text)), %s), 3857))
"""

# A dissolve layer is stored first as its source features (INSERT_FEATURE_SQL), then
# replaced in place by non-overlapping, subdivided pieces, so same-band overlaps (Berounka
# and Vltava share ~16 km2 of Q100) cannot render darker. Where two zones overlap, the
# earlier row keeps the shared area and the later one is cut back (ST_Difference against
# its overlapping predecessors only): a nationwide ST_UnaryUnion of a band ran past the
# 30-minute statement budget on the first production run (2026-10-15), while overlaps are
# a handful of confluences. Each piece keeps its own watercourse name.
DISSOLVE_SQL = """
with sources as (
    select id, source_id, props, st_collectionextract(geom, 3) as geom
      from map_overlay_features
     where layer_key = %(layer)s
),
exclusive as (
    select s.id, s.source_id, s.props,
           coalesce(st_difference(s.geom, (
               select st_union(o.geom)
                 from sources o
                where o.id < s.id
                  and o.geom && s.geom
                  and st_intersects(o.geom, s.geom)
                  and not st_touches(o.geom, s.geom))),
                    s.geom) as geom
      from sources s
),
pieces as (
    select e.source_id, e.props, p.piece
      from exclusive e
      cross join lateral st_subdivide(
          case when %(outline)s then st_boundary(e.geom) else e.geom end, 256) as p(piece)
     where not st_isempty(e.geom)
),
replaced as (
    delete from map_overlay_features where layer_key = %(layer)s
)
insert into map_overlay_features (layer_key, source_id, props, geom)
select %(layer)s, pieces.source_id,
       jsonb_strip_nulls(jsonb_build_object('class', %(class)s::text,
                                            'name', pieces.props ->> 'name')),
       pieces.piece
  from pieces
"""

RENDER_SQL = "select map_overlay_render_section(%s)"
TILE_COUNT_SQL = "select count(*) from map_overlay_tiles"

SECTION_STATS_SQL = """
select (select count(*)
          from map_overlay_features f
          join map_overlay_layers l on l.key = f.layer_key
         where l.section = %(section)s),
       coalesce(max(octet_length(t.mvt)), 0),
       coalesce(round(avg(octet_length(t.mvt))), 0)
  from map_overlay_tiles t
 where t.section = %(section)s
"""

SELECT_SOURCE_SQL = "select s.source_id, s.props, st_asgeojson(s.geom) from ({source}) s"

KRAJE_SQL = """
select b.id::text as source_id,
       jsonb_build_object('class', 'kraj', 'name', b.name) as props,
       st_transform(st_boundary(b.geom::geometry), 3857) as geom
  from admin_boundaries b
 where b.level = 'kraj'
"""

KRAJE_REVISED_SQL = "select max(ingested_at)::date from admin_boundaries where level = 'kraj'"

# The MF rent choropleth: migration 132's rent_map_choropleth without its GeoJSON column. One
# feature per priced obec / KÚ with the latest revision's reference rent per size category;
# a category the MF leaves unpriced is absent from props and paints as the scale's null_color.
RENT_MAP_SQL = """
select b.id::text as source_id,
       jsonb_strip_nulls(jsonb_build_object(
           'name', b.name, 'kraj', min(v.kraj), 'level', b.level,
           'vk1', max(v.ref_rent_per_m2) filter (where v.vk = 1),
           'vk2', max(v.ref_rent_per_m2) filter (where v.vk = 2),
           'vk3', max(v.ref_rent_per_m2) filter (where v.vk = 3),
           'vk4', max(v.ref_rent_per_m2) filter (where v.vk = 4))) as props,
       st_transform(b.geom::geometry, 3857) as geom
  from rent_map_values v
  join admin_boundaries b on b.id = v.ruian_code and b.level = v.level
 where v.source_revision = (select max(source_revision) from rent_map_revisions)
 group by b.id
"""

# The source date of the revision RENT_MAP_SQL draws (the latest), not the newest date on
# file: a re-uploaded older file becomes the latest revision with its own, older date.
RENT_MAP_REVISED_SQL = (
    "select source_date from rent_map_revisions order by source_revision desc limit 1"
)


class Refused(RuntimeError):
    """A guard refused the section; nothing of it was written."""


class DatumRefused(Refused):
    """PostGIS's S-JTSK transform is off, so no section can be trusted."""


class _Transient(requests.RequestException):
    """A response worth retrying (429/5xx, or an ArcGIS error body carrying one)."""


@dataclass(frozen=True)
class Feature:
    source_id: str
    props: dict[str, Any]
    geometry: str
    srid: int


Props = Callable[[dict[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class ArcGisLayer:
    service_url: str
    layer_name: str
    where: str
    out_sr: int
    props: Props
    geometry_type: str
    fields: tuple[str, ...]

    def read(self, ctx: Context) -> Iterator[Feature]:
        return ctx.arcgis.features(self)


@dataclass(frozen=True)
class SqlSource:
    sql: str

    def read(self, ctx: Context) -> Iterator[Feature]:
        query = sql.SQL(SELECT_SOURCE_SQL).format(source=sql.SQL(self.sql))
        with ctx.conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        for source_id, props, geometry in rows:
            yield Feature(str(source_id), props, geometry, 3857)


@dataclass(frozen=True)
class Layer:
    key: str
    label: str
    sort: int
    legend: tuple[dict[str, Any], ...]
    readers: tuple[ArcGisLayer | SqlSource | UniversitySites, ...]
    store: Literal["plain", "whole", "dissolve", "outline"] = "plain"
    scale: dict[str, Any] | None = None
    variants: tuple[dict[str, Any], ...] | None = None


RevisedAt = Callable[["Section", "Context"], date | None]


@dataclass(frozen=True)
class Section:
    label: str
    sort: int
    minzoom: int
    maxzoom: int
    attribution: str
    licence: str
    source_url: str
    note: str | None
    revised_at: RevisedAt
    layers: tuple[Layer, ...]


class Http:
    """Polite GET: the honest User-Agent, PAUSE_S between requests, backoff on 429/5xx/network."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        pause_s: float = PAUSE_S,
        attempts: int = ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session if session is not None else requests.Session()
        self.pause_s = pause_s
        self.attempts = attempts
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get(url, params, as_json=True)

    def content(self, url: str) -> bytes:
        return self._get(url, None, as_json=False)

    def _get(self, url: str, params: dict[str, Any] | None, *, as_json: bool) -> Any:
        for attempt in range(1, self.attempts + 1):
            self._pace()
            try:
                resp = self.session.get(
                    url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S,
                )
                if resp.status_code in RETRY_STATUS:
                    raise _Transient(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                if not as_json:
                    return resp.content
                payload = resp.json()
                error = payload.get("error") if isinstance(payload, dict) else None
                if error:
                    if error.get("code") in RETRY_STATUS:
                        raise _Transient(f"ArcGIS error {error.get('code')}: {error.get('message')}")
                    raise RuntimeError(f"ArcGIS error from {url}: {error}")
                return payload
            except (requests.ConnectionError, requests.Timeout, requests.JSONDecodeError,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ContentDecodingError, _Transient) as exc:
                if attempt == self.attempts:
                    raise
                delay = 2.0 ** attempt
                LOG.warning("HTTP retry %d/%d in %.0fs: %s (%s)",
                            attempt, self.attempts - 1, delay, url, exc)
                self._sleep(delay)
            finally:
                self._last = self._clock()
        raise AssertionError("unreachable")

    def _pace(self) -> None:
        if self._last is None:
            return
        wait = self.pause_s - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)


class ArcGis:
    """ArcGIS REST reader: layers resolved BY NAME, features paged by object id."""

    def __init__(self, http: Http) -> None:
        self.http = http
        self._services: dict[str, dict[str, Any]] = {}
        self._layers: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self._renames: dict[tuple[str, str], dict[str, str]] = {}

    def layer(self, src: ArcGisLayer) -> tuple[str, dict[str, Any]]:
        """The layer's URL and its ?f=json, after asserting its geometry type and fields."""
        key = (src.service_url, src.layer_name)
        if key not in self._layers:
            if src.service_url not in self._services:
                self._services[src.service_url] = self.http.json(src.service_url, {"f": "json"})
            service = self._services[src.service_url]
            ids = [lyr.get("id") for lyr in service.get("layers") or []
                   if lyr.get("name") == src.layer_name]
            if len(ids) != 1:
                raise Refused(f"{src.service_url}: {len(ids)} layers named {src.layer_name!r}")
            url = f"{src.service_url}/{ids[0]}"
            info = self.http.json(url, {"f": "json"})
            if info.get("geometryType") != src.geometry_type:
                raise Refused(f"{url}: geometryType {info.get('geometryType')!r}, "
                              f"expected {src.geometry_type!r}")
            # Field names are matched by case: the register spells one layer's fields
            # differently (Q500: `Naz_tok`, the others `NAZ_TOK`). The mapper always
            # sees the declared spelling.
            by_lower = {str(f.get("name")).lower(): str(f.get("name"))
                        for f in info.get("fields") or [] if f.get("name")}
            missing = [name for name in src.fields if name.lower() not in by_lower]
            if missing:
                raise Refused(f"{url}: missing fields {sorted(missing)}")
            self._renames[key] = {by_lower[name.lower()]: name for name in src.fields
                                  if by_lower[name.lower()] != name}
            self._layers[key] = (url, info)
        return self._layers[key]

    def features(self, src: ArcGisLayer) -> Iterator[Feature]:
        """Every feature matching src.where, or Refused: nothing partial ever comes back."""
        url, _ = self.layer(src)
        query = f"{url}/query"
        listed = self.http.json(query, {"where": src.where, "returnIdsOnly": "true", "f": "json"})
        if _exceeded(listed):
            raise Refused(f"{url}: the id list exceeded the transfer limit")
        ids = sorted(listed.get("objectIds") or [])
        oid = listed.get("objectIdFieldName") or "OBJECTID"
        counted = self.http.json(query, {"where": src.where, "returnCountOnly": "true", "f": "json"})
        count = counted.get("count")
        if count != len(ids):
            raise Refused(f"{url}: {len(ids)} ids listed for a count of {count}")
        LOG.info("FETCH %s where=%r ids=%d", src.layer_name, src.where, len(ids))
        fetched = 0
        for chunk in itertools.batched(ids, CHUNK):
            page = self.http.json(query, {
                "objectIds": ",".join(str(i) for i in chunk),
                "outFields": "*", "outSR": src.out_sr, "f": "geojson",
            })
            if _exceeded(page):
                raise Refused(f"{url}: a page exceeded the transfer limit")
            rows = page.get("features") or []
            by_id = {_feature_id(f, oid): f for f in rows}
            if len(rows) != len(chunk) or set(by_id) != set(chunk):
                raise Refused(f"{url}: asked for {len(chunk)} ids, got {len(rows)} features")
            if any(f.get("geometry") is None for f in rows):
                raise Refused(f"{url}: a feature came back without geometry")
            fetched += len(rows)
            rename = self._renames.get((src.service_url, src.layer_name), {})
            for fid in chunk:
                feature = by_id[fid]
                raw = feature.get("properties") or {}
                props = src.props({rename.get(k, k): v for k, v in raw.items()})
                if props is not None:
                    yield Feature(str(fid), props, json.dumps(feature["geometry"]), src.out_sr)
        if fetched != count:
            raise Refused(f"{url}: fetched {fetched} features for a count of {count}")


def _exceeded(payload: dict[str, Any]) -> bool:
    return bool(payload.get("exceededTransferLimit")
                or (payload.get("properties") or {}).get("exceededTransferLimit"))


def _feature_id(feature: dict[str, Any], oid: str) -> Any:
    return feature["id"] if "id" in feature else (feature.get("properties") or {}).get(oid)


@dataclass(frozen=True)
class Context:
    http: Http
    arcgis: ArcGis
    conn: psycopg.Connection


_LINE_LETTER = re.compile(r"\btrasa metra ([a-z])\b", re.IGNORECASE)
STATION_PREFIX = "Stanice metra "


def _flat(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if v is not None}


def metro_line_props(a: dict[str, Any]) -> dict[str, Any] | None:
    """stav 1 = in service (the letter), 2 = proposal (letter from nazev, dashed), 3 = reserve."""
    stav = a.get("stav")
    name = (a.get("nazev") or "").strip() or None
    if stav == 4:
        return None
    if stav == 1:
        return _flat({"class": (a.get("parametr") or "").strip().upper() or None, "name": name})
    if stav == 2:
        letter = _LINE_LETTER.search(name or "")
        if letter is None:
            raise Refused(f"metro proposal without a line letter in its name: {name!r}")
        return _flat({"class": letter.group(1).upper(), "name": name, "dash": True})
    if stav == 3:
        return _flat({"class": "reserve", "name": name})
    raise Refused(f"metro line with an unknown stav {stav!r}: {name!r}")


def metro_station_props(a: dict[str, Any]) -> dict[str, Any] | None:
    """Named planned stations = station points (druh 2) in the návrh stage (stav 2). Vestibules,
    depots, existing points and reserve-stage points are skipped (ruling 4)."""
    if a.get("druh") != 2 or a.get("stav") != 2:
        return None
    name = (a.get("nazev") or "").strip() or None
    label = name.removeprefix(STATION_PREFIX) if name else None
    return _flat({"class": "station", "name": name, "label": label})


METRO_LETTERS = ("A", "B", "C")


def metro_existing_station_props(a: dict[str, Any]) -> dict[str, Any] | None:
    """Stations in service (DRUH 1) from IPR's named metro-equipment layer. The class is the
    line letter, so the dot wears its line's colour and no legend row is added; a transfer
    station comes once per line. Vestibules (2) and depots (3) are skipped."""
    if a.get("DRUH") != 1:
        return None
    letter = (a.get("TRASA") or "").strip().upper()
    name = (a.get("NAZEV") or "").strip() or None
    if letter not in METRO_LETTERS or name is None:
        raise Refused(f"metro station without a line letter in A/B/C or a name: {a!r}")
    return _flat({"class": letter, "name": name, "label": name.removeprefix(STATION_PREFIX)})


# --- universities: IPR's sites, classed by the students MŠMT counts there -----------------

UNIVERSITY_FIELDS = ("id_zar", "nazev_zar", "typ_uap")
TEACHING_SITES_WHERE = "typ_uap in ('02_01','02_02')"
DORMS_WHERE = "typ_uap = '02_03'"
PRIMARY_STUDENTS = 2_000

# Where each Prague faculty's students study: MŠMT's F21 school/faculty code(s) → the IPR site
# (id_zar) of its main teaching building, and the label the site wears when it is primary. A
# faculty seated outside Prague (ČVUT FBMI in Kladno, VŠE FM in Jindřichův Hradec, UK's Plzeň
# and Hradec faculties) has no site here; a school seated outside Prague (ŠKODA AUTO VŠ) is a
# secondary site like any unmapped one; a private school's count is the whole school's.
UNIVERSITY_SEATS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("FF UK", 280001, ("11210",)),
    ("FSV UK", 280020, ("11230",)),
    ("PedF UK", 280014, ("11410",)),
    ("PřF UK", 280056, ("11310",)),
    ("1. LF UK", 280028, ("11110",)),
    ("PF UK", 280013, ("11220",)),
    ("MFF UK", 280081, ("11320",)),
    ("FHS UK", 280303, ("11240",)),
    ("3. LF UK", 280046, ("11120",)),
    ("2. LF UK", 280040, ("11130",)),
    ("FTVS UK", 280068, ("11510",)),
    ("KTF UK", 280086, ("11260",)),
    ("ETF UK", 280087, ("11270",)),
    ("HTF UK", 280088, ("11280",)),
    ("FSv ČVUT", 270118, ("21110",)),
    ("FS ČVUT", 270081, ("21220",)),
    ("FEL ČVUT", 270085, ("21230",)),
    ("FIT + FA ČVUT", 270203, ("21240", "21450")),
    ("FD ČVUT", 270130, ("21260",)),
    ("FJFI ČVUT", 270111, ("21340",)),
    ("MÚVS ČVUT", 270205, ("21900",)),
    ("VŠCHT", 210001, ("22310", "22320", "22330", "22340", "22900")),
    ("VŠE", 220001, ("31110", "31120", "31130", "31140", "31150")),
    ("PEF ČZU", 260006, ("41110",)),
    ("FAPPZ ČZU", 260003, ("41210",)),
    ("TF ČZU", 260025, ("41310",)),
    ("FLD + FŽP ČZU", 260104, ("41320", "41330")),
    ("FTZ ČZU", 260103, ("41340",)),
    ("HAMU", 250007, ("51110",)),
    ("DAMU", 250002, ("51210",)),
    ("FAMU", 250004, ("51310",)),
    ("AVU", 240001, ("52000",)),
    ("UMPRUM", 200001, ("53000",)),
    ("AMBIS", 10001, ("61000",)),
    ("UCP", 20001, ("63000",)),
    ("UNYP", 160001, ("6D000",)),
    ("Palestra", 190001, ("6P000",)),
    ("VŠZ", 370001, ("6S000",)),
    ("AAU", 120001, ("6U000",)),
    ("MUP", 300101, ("75000",)),
    ("PVŠPS", 130001, ("79000",)),
    ("CEVRO", 350001, ("7D000",)),
    ("Unicorn", 340101, ("7E000",)),
    ("Archip", 380001, ("7M000",)),
    ("ADI", 390001, ("7R000",)),
    ("PEUNI", 170002, ("7S000",)),
    ("VŠKK", 360001, ("7T000",)),
    ("VŠFS", 30003, ("7U000",)),
    ("VŠEM", 150001, ("7V000",)),
)

_XLSX_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XLSX_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_XLSX_RELATIONSHIP = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_F21_CODE = re.compile(r"[0-9A-Z]{5}")
_F21_COUNT = re.compile(r"\d+(\.0+)?")


def _xlsx_rows(sheet: bytes, shared: list[str]) -> Iterator[dict[str, str]]:
    """Each row of a worksheet as {column letters: cell text}."""
    for row in ET.fromstring(sheet).iter(f"{_XLSX_MAIN}row"):
        cells: dict[str, str] = {}
        for cell in row.iter(f"{_XLSX_MAIN}c"):
            value = cell.find(f"{_XLSX_MAIN}v")
            if value is None or value.text is None:
                continue
            column = "".join(ch for ch in cell.get("r", "") if ch.isalpha())
            cells[column] = shared[int(value.text)] if cell.get("t") == "s" else value.text
        yield cells


def parse_f21(document: bytes) -> tuple[int, dict[str, int]]:
    """MŠMT's F21 workbook (students by school and faculty, one sheet per year): the latest
    year and {code: students in present-form study}. Column B is the code, H that total."""
    with zipfile.ZipFile(io.BytesIO(document)) as book:
        targets = {rel.get("Id"): str(rel.get("Target")) for rel in
                   ET.fromstring(book.read("xl/_rels/workbook.xml.rels")).iter(_XLSX_RELATIONSHIP)}
        sheets = {int(str(sheet.get("name"))): targets[str(sheet.get(_XLSX_REL_ID))]
                  for sheet in ET.fromstring(book.read("xl/workbook.xml")).iter(f"{_XLSX_MAIN}sheet")
                  if str(sheet.get("name")).isdigit()}
        if not sheets:
            raise Refused(f"{MSMT_F21_URL}: no sheet named by a year")
        year = max(sheets)
        shared = ["".join(t.text or "" for t in si.iter(f"{_XLSX_MAIN}t")) for si in
                  ET.fromstring(book.read("xl/sharedStrings.xml")).iter(f"{_XLSX_MAIN}si")]
        rows = list(_xlsx_rows(book.read("xl/" + sheets[year].removeprefix("/xl/")), shared))
    students = {row["B"]: int(float(row["H"])) for row in rows
                if _F21_CODE.fullmatch(row.get("B", "")) and _F21_COUNT.fullmatch(row.get("H", ""))}
    if not students:
        raise Refused(f"{MSMT_F21_URL}: sheet {year} holds no code/count rows")
    return year, students


def students_on_site(students: dict[str, int]) -> dict[int, tuple[str, int]]:
    """Each curated site's label and present-form students (its faculties summed). A code the
    table no longer carries refuses the run: a stale join is never a silently smaller campus."""
    on_site: dict[int, tuple[str, int]] = {}
    for label, id_zar, codes in UNIVERSITY_SEATS:
        missing = [code for code in codes if code not in students]
        if missing:
            raise Refused(f"MŠMT F21 lacks code(s) {missing} for {label!r}")
        on_site[id_zar] = (label, sum(students[code] for code in codes))
    return on_site


def _site_name(a: dict[str, Any], kinds: tuple[str, ...]) -> str:
    name = re.sub(r"\s+", " ", a.get("nazev_zar") or "").strip()
    if a.get("typ_uap") not in kinds or not name:
        raise Refused(f"university site outside typ_uap {kinds} or unnamed: {a!r}")
    return name


def university_site_props(on_site: dict[int, tuple[str, int]], a: dict[str, Any]) -> dict[str, Any]:
    """A teaching (02_01) or service (02_02) site: primary and labelled when PRIMARY_STUDENTS
    or more study there by MŠMT's count, secondary otherwise."""
    name = _site_name(a, ("02_01", "02_02"))
    seat = on_site.get(a.get("id_zar"))
    if seat is None or seat[1] < PRIMARY_STUDENTS:
        return {"class": "secondary", "name": name}
    return {"class": "primary", "name": name, "label": seat[0]}


def dorm_props(a: dict[str, Any]) -> dict[str, Any]:
    return {"class": "dorm", "name": _site_name(a, ("02_03",))}


@dataclass(frozen=True)
class UniversitySites:
    """IPR's university teaching and service sites, classed by the students MŠMT counts there:
    the F21 workbook is read once, its counts bound into the props mapper, then the ArcGIS
    pager runs as for any layer. A primary seat that matched no IPR point refuses the run."""
    where: str

    def read(self, ctx: Context) -> Iterator[Feature]:
        year, students = parse_f21(ctx.http.content(MSMT_F21_URL))
        on_site = students_on_site(students)
        primary = {label for label, count in on_site.values() if count >= PRIMARY_STUDENTS}
        LOG.info("MSMT F21 year=%d codes=%d sites=%d primary=%d: %s",
                 year, len(students), len(on_site), len(primary), ", ".join(sorted(primary)))
        sites = _ipr(IPR_UNIVERSITIES, self.where, POINT,
                     functools.partial(university_site_props, on_site), UNIVERSITY_FIELDS)
        labelled: set[str] = set()
        for feature in ctx.arcgis.features(sites):
            labelled.add(feature.props.get("label", ""))
            yield feature
        if missing := primary - labelled:
            raise Refused(f"primary site(s) without an IPR point: {sorted(missing)}")


RAILWAY_CLASSES = {"stanice": "station", "zastávka": "halt"}


def railway_station_props(a: dict[str, Any]) -> dict[str, Any]:
    """A ZABAGED station point: class from `typ` (stanice / zastávka), the name as the label;
    an unknown kind or an unnamed point is refused — the publisher changed its vocabulary."""
    cls = RAILWAY_CLASSES.get((a.get("typ") or "").strip())
    name = (a.get("zst_p") or "").strip() or None
    if cls is None or name is None:
        raise Refused(f"railway station outside typ {sorted(RAILWAY_CLASSES)} or unnamed: {a!r}")
    return _flat({"class": cls, "name": name, "label": name,
                  "code": (a.get("zst_k") or "").strip() or None})


def flood_props(cls: str) -> Props:
    def props(a: dict[str, Any]) -> dict[str, Any] | None:
        return _flat({"class": cls, "name": (a.get("NAZ_TOK") or "").strip() or None})
    return props


_ISO_NS = {"gmd": "http://www.isotc211.org/2005/gmd", "gco": "http://www.isotc211.org/2005/gco"}


def parse_iso_revision(document: bytes | str) -> date | None:
    """The dataset citation's revision date (ISO 19139), else the record's gmd:dateStamp."""
    root = ET.fromstring(document)
    revisions = [
        found
        for ci in root.iterfind("gmd:identificationInfo/*/gmd:citation/*/gmd:date/gmd:CI_Date", _ISO_NS)
        if _date_type(ci) == "revision"
        and (found := _iso_date(ci.find("gmd:date/*", _ISO_NS))) is not None
    ]
    if revisions:
        return max(revisions)
    return _iso_date(root.find("gmd:dateStamp/*", _ISO_NS))


def _date_type(ci: ET.Element) -> str | None:
    code = ci.find("gmd:dateType/gmd:CI_DateTypeCode", _ISO_NS)
    if code is None:
        return None
    return code.get("codeListValue") or (code.text or "").strip() or None


def _iso_date(element: ET.Element | None) -> date | None:
    text = (element.text or "").strip() if element is not None else ""
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def latest_edit_date(layer_infos: Iterable[dict[str, Any]]) -> date | None:
    """The newest editingInfo.lastEditDate (ms since the epoch) across the layers, as a UTC date."""
    stamps = [
        stamp for info in layer_infos
        if isinstance(stamp := (info.get("editingInfo") or {}).get("lastEditDate"), (int, float))
    ]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps) / 1000, tz=timezone.utc).date()


def iso_revised_at(url: str) -> RevisedAt:
    def read(section: Section, ctx: Context) -> date | None:
        return parse_iso_revision(ctx.http.content(url))
    return read


def arcgis_revised_at(section: Section, ctx: Context) -> date | None:
    return latest_edit_date(
        ctx.arcgis.layer(reader)[1]
        for layer in section.layers for reader in layer.readers
        if isinstance(reader, ArcGisLayer)
    )


def sql_revised_at(query: str) -> RevisedAt:
    def read(section: Section, ctx: Context) -> date | None:
        with ctx.conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
        return row[0] if row else None
    return read


def _entry(cls: str, label: str, color: str, *, dash: bool = False,
           geom: str | None = None) -> dict[str, Any]:
    """One legend class; `geom` ('fill' | 'line' | 'point') only shapes the chip's swatch."""
    return {"class": cls, "label": label, "color": color,
            **({"dash": True} if dash else {}), **({"geom": geom} if geom else {})}


def _ipr(service: str, where: str, geometry_type: str, props: Props, fields: tuple[str, ...],
         *, base: str = IPR_HOSTED, out_sr: int = 3857) -> ArcGisLayer:
    return ArcGisLayer(f"{base}/{service}/FeatureServer", service, where, out_sr,
                       props, geometry_type, fields)


def _flood(layer_name: str, cls: str) -> ArcGisLayer:
    return ArcGisLayer(VUV_MAPSERVER, layer_name, "1=1", 5514, flood_props(cls), POLYGON,
                       ("NAZ_TOK",))


_METRO_LINE_FIELDS = ("kapitola", "stav", "parametr", "nazev")

SOURCES: dict[str, Section] = {
    "metro": Section(
        label="Metro", sort=1, minzoom=9, maxzoom=12,
        attribution="datový podklad © IPR Praha", licence="CC BY 4.0", source_url=IPR_HOSTED,
        note="Metropolitní plán hl. m. Prahy (platný od 1. 9. 2026): stav, návrh a územní "
             "rezervy. Rezerva „výhledové tangenciální kolejové spojení“ (v tisku metro E) "
             "nemá rozhodnutou trasu ani technologii. Stanice v provozu v barvě své linky; "
             "přestupní stanice má bod za každou linku.",
        revised_at=arcgis_revised_at,
        layers=(
            Layer(
                "metro", "Metro", 1,
                legend=(
                    _entry("A", "Metro A", "#00A562", geom="line"),
                    _entry("B", "Metro B", "#FCC400", geom="line"),
                    _entry("C", "Metro C", "#CF003D", geom="line"),
                    _entry("D", "Metro D – návrh", "#1F77D0", dash=True),
                    _entry("reserve", "Územní rezerva", "#8C9196", dash=True),
                    _entry("station", "Plánovaná stanice", "#1F77D0", geom="point"),
                ),
                readers=(
                    _ipr("MPP_CUR_MPP_STAV_600_DI_TRASY_L", "kapitola=621", POLYLINE,
                         metro_line_props, _METRO_LINE_FIELDS),
                    _ipr("MPP_CUR_MPP_600_DI_TRASY_L", "kapitola=621", POLYLINE,
                         metro_line_props, _METRO_LINE_FIELDS),
                    _ipr("MPP_CUR_MPP_600_DI_ZARIZENI_B", "kapitola=621 and druh=2", POINT,
                         metro_station_props, ("kapitola", "druh", "stav", "nazev")),
                    # Stations in service, named, with their line letter: the plan's own
                    # existing-station points carry no names. Native S-JTSK, like the flood
                    # register: this server's WGS84 output is the same ~10 m datum shift off.
                    _ipr("DOP_MetroZarizeni_b", "DRUH=1", POINT, metro_existing_station_props,
                         ("DRUH", "NAZEV", "TRASA"), base=IPR_REFERENCED, out_sr=5514),
                ),
            ),
        ),
    ),
    "flood": Section(
        label="Záplavová území", sort=2, minzoom=10, maxzoom=12,
        attribution="© VÚV TGM pro MŽP (ISVS VODA)", licence="CC BY 4.0",
        source_url=VUV_MAPSERVER,
        note="Stanovená záplavová území (zákonný rozsah, orientační). Nezohledňují "
             "protipovodňová opatření (např. Karlín), Q500 je vymezeno jen místy, překryv "
             "pásem je tmavší, přívalové povodně nejsou zahrnuty. Závazné informace: "
             "vodoprávní úřad.",
        revised_at=iso_revised_at(VUV_ISO_RECORD),
        layers=(
            Layer("flood_q5", "Q5", 1, (_entry("q5", "Q5 – pětiletá voda", "#1D4F91"),),
                  (_flood("ZaplavUzemi_Q5", "q5"),), store="dissolve"),
            Layer("flood_q20", "Q20", 2, (_entry("q20", "Q20 – dvacetiletá voda", "#3B7DD8"),),
                  (_flood("ZaplavUzemi_Q20", "q20"),), store="dissolve"),
            Layer("flood_q100", "Q100", 3, (_entry("q100", "Q100 – stoletá voda", "#7FB3E6"),),
                  (_flood("ZaplavUzemi_Q100", "q100"),), store="dissolve"),
            Layer("flood_q500", "Q500", 4,
                  (_entry("q500", "Q500 – mapováno jen místy", "#C6DCF2"),),
                  (_flood("ZaplavUzemi_Q500", "q500"),), store="dissolve"),
            Layer("flood_az", "Aktivní zóna", 5,
                  (_entry("az", "Aktivní zóna", "#B3261E", dash=True),),
                  (_flood("ZaplavUzemi_AktivniZony", "az"),), store="outline"),
        ),
    ),
    "kraje": Section(
        label="Kraje", sort=3, minzoom=3, maxzoom=9,
        attribution="© ČÚZK (RÚIAN)", licence="CC BY 4.0", source_url=RUIAN_ATOM, note=None,
        revised_at=sql_revised_at(KRAJE_REVISED_SQL),
        layers=(
            Layer("kraje", "Kraje", 1, (_entry("kraj", "Hranice krajů", "rgba(40,40,70,0.55)", geom="line"),),
                  (SqlSource(KRAJE_SQL),)),
        ),
    ),
    # Prague's university sites from IPR's planning-analysis layer, each teaching or service
    # site classed by the present-form students MŠMT counts there (UNIVERSITY_SEATS); dorms a
    # toggle of their own. From z9 like the metro: Browse's whole-of-Prague view sits just
    # under z10 in the split view's map column, and MapLibre loads no tile below a source's
    # minzoom (the chip then says "Přibližte mapu").
    "universities": Section(
        label="Vysoké školy", sort=4, minzoom=9, maxzoom=12,
        attribution="datový podklad © IPR Praha; počty studentů MŠMT", licence="CC BY 4.0 (IPR Praha)",
        source_url=f"{IPR_HOSTED}/{IPR_UNIVERSITIES}/FeatureServer",
        note="Místa vysokých škol v Praze podle ÚAP IPR Praha. Hlavní místo výuky = budova "
             "fakulty nebo školy, kde podle MŠMT (SIMS, poslední zveřejněný rok) studuje 2 000 "
             "a více studentů prezenčního studia; u škol s pobočkami mimo Prahu jde o počet za "
             "celou školu. Další pracoviště: ostatní budovy, kliniky, rektoráty, menzy, "
             "sportoviště.",
        revised_at=arcgis_revised_at,
        layers=(
            Layer("universities", "Výuka a pracoviště", 1,
                  legend=(_entry("primary", "Hlavní místo výuky (2 000+ studentů)", "#5E35B1",
                                 geom="point"),
                          _entry("secondary", "Další pracoviště VŠ", "#B39DDB", geom="point")),
                  readers=(UniversitySites(TEACHING_SITES_WHERE),)),
            Layer("dorms", "Koleje", 2, legend=(_entry("dorm", "Kolej", "#F57C00", geom="point"),),
                  readers=(_ipr(IPR_UNIVERSITIES, DORMS_WHERE, POINT, dorm_props, UNIVERSITY_FIELDS),)),
        ),
    ),
    # Every railway station and stop in the country from ČÚZK's ZABAGED® (2,797 points, 1:10 000;
    # stanice vs zastávka from the layer's own `typ`), fetched in its native S-JTSK like the flood
    # register. From z7: a regional view shows the network's stops, the country view does not.
    "railway": Section(
        label="Železniční stanice", sort=5, minzoom=7, maxzoom=12,
        attribution="© ČÚZK (ZABAGED®)", licence="CC BY 4.0",
        source_url=f"{CUZK_ZABAGED}/58",
        note="Železniční stanice a zastávky v celé ČR podle ZABAGED® ČÚZK; název a druh podle "
             "Správy železnic (kód SR70 v detailu).",
        revised_at=arcgis_revised_at,
        layers=(
            Layer("railway_stations", "Stanice a zastávky", 1,
                  legend=(_entry("station", "Železniční stanice", "#37474F", geom="point"),
                          _entry("halt", "Železniční zastávka", "#90A4AE", geom="point")),
                  readers=(ArcGisLayer(CUZK_ZABAGED, ZABAGED_STATIONS_LAYER, "1=1", 5514,
                                       railway_station_props, POINT, RAILWAY_FIELDS),)),
        ),
    ),
    # The choropleth the Browse map drew from rent_map_choropleth_public until migration 601:
    # the same ramp, null colour, opacity and size categories. Drawn from z5 like kraje, so
    # the whole-country view keeps it (a z5/z6 tile of all 7.6k territories is ~1 MB).
    # The MF states no licence for the data; its site is "© Ministerstvo financí ČR".
    "rent_map": Section(
        label="Cenová mapa nájemného", sort=0, minzoom=3, maxzoom=12,
        attribution="Ministerstvo financí ČR – Cenová mapa nájemného",
        licence="Licence neuvedena (© Ministerstvo financí ČR)", source_url=MF_INFOGRAPHIC_URL,
        note=None,
        revised_at=sql_revised_at(RENT_MAP_REVISED_SQL),
        layers=(
            Layer(
                "rent_map", "Cenová mapa nájemného", 1, legend=(),
                readers=(SqlSource(RENT_MAP_SQL),), store="whole",
                scale={
                    "prop": "vk1",
                    "stops": [[150, "#eaf0f6"], [250, "#9ec3e6"], [350, "#5a7fd6"],
                              [450, "#3b4fb5"], [550, "#2a2a8a"], [600, "#1a1a5e"]],
                    "null_color": "rgba(140, 140, 140, 0.35)",
                    "unit": "rent_per_m2",
                    "opacity": 0.62,
                    "outline": {"color": "rgba(60, 60, 90, 0.25)", "width": 0.4},
                },
                variants=(
                    {"key": "vk1", "label": "1+kk, 1+1", "prop": "vk1"},
                    {"key": "vk2", "label": "2+kk, 2+1", "prop": "vk2"},
                    {"key": "vk3", "label": "3+kk, 3+1", "prop": "vk3"},
                    {"key": "vk4", "label": "4+kk, 4+1", "prop": "vk4"},
                ),
            ),
        ),
    ),
}


def check_datum(conn: psycopg.Connection) -> float:
    """Metres between the control point's S-JTSK and WGS84 coordinates once both are in 3857."""
    with conn.cursor() as cur:
        cur.execute(DATUM_CHECK_SQL, (*DATUM_S_JTSK, *DATUM_WGS84))
        row = cur.fetchone()
    distance = float(row[0])
    if distance > DATUM_TOLERANCE_M:
        raise DatumRefused(f"control point Florenc lands {distance:.2f} m apart "
                           f"(tolerance {DATUM_TOLERANCE_M} m): the S-JTSK transform is off")
    return distance


def check_shrink(key: str, count: int, previous: int, *, accept_shrink: bool) -> None:
    """Refuse an empty layer, or one below SHRINK_FLOOR of its previous feature_count."""
    if count > 0 and count >= SHRINK_FLOOR * previous:
        return
    if accept_shrink:
        LOG.warning("SHRINK accepted layer=%s features=%d previous=%d", key, count, previous)
        return
    raise Refused(f"layer {key}: {count} features against {previous} before "
                  f"(floor {SHRINK_FLOOR:.0%}); --accept-shrink overrides")


def read_layer(ctx: Context, layer: Layer) -> list[Feature]:
    classes = {entry["class"] for entry in layer.legend}
    features: list[Feature] = []
    for reader in layer.readers:
        for feature in reader.read(ctx):
            if classes and feature.props.get("class") not in classes:
                raise Refused(f"layer {layer.key}: class {feature.props.get('class')!r} "
                              f"is not in its legend")
            if any(isinstance(v, (dict, list)) for v in feature.props.values()):
                raise Refused(f"layer {layer.key}: props must be flat scalars: {feature.props!r}")
            features.append(feature)
    return features


def previous_counts(conn: psycopg.Connection, section: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(PREVIOUS_COUNTS_SQL, (section,))
        return {key: int(count) for key, count in cur.fetchall()}


def _jsonb(value: Any) -> Jsonb | None:
    return None if value is None else Jsonb(list(value) if isinstance(value, tuple) else value)


def write_section(
    conn: psycopg.Connection,
    key: str,
    section: Section,
    layers: list[tuple[Layer, list[Feature]]],
    revised: date | None,
) -> tuple[int, int, int, int]:
    """Replace the section in ONE transaction; returns (rows, tiles, max bytes, avg bytes)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(STATEMENT_TIMEOUT_SQL)
        cur.execute(LOCK_SQL, (f"map_overlays:{key}",))
        cur.execute(UPSERT_SECTION_SQL, {
            "key": key, "label": section.label, "sort": section.sort,
            "minzoom": section.minzoom, "maxzoom": section.maxzoom,
            "attribution": section.attribution, "licence": section.licence,
            "source_url": section.source_url, "note": section.note, "revised": revised,
        })
        cur.execute(PRUNE_LAYERS_SQL, (key, [layer.key for layer, _ in layers]))
        for layer, features in layers:
            cur.execute(UPSERT_LAYER_SQL, {
                "key": layer.key, "section": key, "label": layer.label, "sort": layer.sort,
                "legend": _jsonb(layer.legend), "scale": _jsonb(layer.scale),
                "variants": _jsonb(layer.variants), "feature_count": len(features),
            })
            cur.execute(DELETE_FEATURES_SQL, (layer.key,))
            insert_sql = INSERT_WHOLE_FEATURE_SQL if layer.store == "whole" else INSERT_FEATURE_SQL
            for batch in itertools.batched(features, INSERT_BATCH):
                cur.executemany(insert_sql, [
                    (layer.key, f.source_id, Jsonb(f.props), f.geometry, f.srid) for f in batch
                ])
            if layer.store in ("dissolve", "outline"):
                cur.execute(DISSOLVE_SQL, {
                    "layer": layer.key, "class": layer.legend[0]["class"],
                    "outline": layer.store == "outline",
                })
        cur.execute(RENDER_SQL, (key,))
        tiles = int(cur.fetchone()[0])
        cur.execute(SECTION_STATS_SQL, {"section": key})
        rows, max_bytes, avg_bytes = cur.fetchone()
    return int(rows), tiles, int(max_bytes), int(avg_bytes)


def count_tiles(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(TILE_COUNT_SQL)
        return int(cur.fetchone()[0])


def run_section(
    conn: psycopg.Connection,
    http: Http,
    arcgis: ArcGis,
    key: str,
    section: Section,
    *,
    dry_run: bool,
    accept_shrink: bool,
) -> int:
    """Fetch, check and (unless dry-run) replace one section; returns the tiles stored."""
    started = time.monotonic()
    datum = check_datum(conn)
    ctx = Context(http, arcgis, conn)
    previous = previous_counts(conn, key)
    layers: list[tuple[Layer, list[Feature]]] = []
    for layer in section.layers:
        features = read_layer(ctx, layer)
        before = previous.get(layer.key, 0)
        check_shrink(layer.key, len(features), before, accept_shrink=accept_shrink)
        LOG.info("LAYER section=%s key=%s features=%d previous=%d",
                 key, layer.key, len(features), before)
        layers.append((layer, features))
    revised = _revised_at(key, section, ctx)
    total = sum(len(features) for _, features in layers)
    if dry_run:
        LOG.info("DRY-RUN section=%s features=%d revised=%s datum=%.2fm seconds=%.1f "
                 "(nothing written)", key, total, revised, datum, time.monotonic() - started)
        return 0
    rows, tiles, max_bytes, avg_bytes = write_section(conn, key, section, layers, revised)
    LOG.info("SECTION key=%s features=%d rows=%d tiles=%d max_tile_bytes=%d "
             "avg_tile_bytes=%d revised=%s datum=%.2fm seconds=%.1f",
             key, total, rows, tiles, max_bytes, avg_bytes, revised, datum,
             time.monotonic() - started)
    return tiles


def _revised_at(key: str, section: Section, ctx: Context) -> date | None:
    """The publisher's revision date, or None (the chip falls back to fetched_at) if unreadable."""
    try:
        return section.revised_at(section, ctx)
    except (requests.RequestException, ET.ParseError, ValueError, psycopg.Error) as exc:
        LOG.warning("REVISED unknown section=%s: %r", key, exc)
        return None


class _NoNetwork:
    """The session of a database-only render: a fetch fails at once and is never retried."""

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        raise requests.RequestException(f"a database-only render reached for the network: {url}")


def render_sql_section(conn: psycopg.Connection, key: str) -> int:
    """Replace one SQL-sourced section without the network, under a run's guards and in its one
    write transaction (a savepoint inside a caller's); returns the tiles stored."""
    section = SOURCES[key]
    if not all(isinstance(r, SqlSource) for layer in section.layers for r in layer.readers):
        raise ValueError(f"section {key!r} is not SQL-sourced")
    http = Http(_NoNetwork())
    return run_section(conn, http, ArcGis(http), key, section, dry_run=False, accept_shrink=False)


def select_sections(text: str) -> list[str]:
    wanted = {part.strip() for part in text.split(",") if part.strip()}
    unknown = wanted - set(SOURCES)
    if unknown:
        raise ValueError(f"unknown section(s) {sorted(unknown)}; known: {', '.join(SOURCES)}")
    return [key for key in SOURCES if not wanted or key in wanted]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest_map_overlays",
        description="Ingest the map reference overlays and render their vector tiles.",
    )
    parser.add_argument("--sections", default="",
                        help=f"comma-separated subset of {','.join(SOURCES)} (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and check every layer, write nothing")
    parser.add_argument("--accept-shrink", action="store_true",
                        help="accept a layer that came back below 90 %% of its previous "
                             "feature count")
    args = parser.parse_args(argv)
    try:
        keys = select_sections(args.sections)
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    http = Http()
    arcgis = ArcGis(http)
    failed: list[str] = []
    tiles = 0
    started = time.monotonic()
    with connect() as conn:
        for key in keys:
            try:
                tiles += run_section(conn, http, arcgis, key, SOURCES[key],
                                     dry_run=args.dry_run, accept_shrink=args.accept_shrink)
            except DatumRefused as exc:
                LOG.error("REFUSED datum control point: %s", exc)
                return 1
            except Refused as exc:
                LOG.error("REFUSED section=%s: %s", key, exc)
                failed.append(key)
            except Exception:
                # Sections are independent: one publisher's outage must not block the others.
                LOG.exception("FAILED section=%s", key)
                failed.append(key)
        if failed:
            LOG.error("RUN done failed=%s", ",".join(failed))
            return 1
        # Only a run over EVERY section refreshes the whole store, so only that run stamps it:
        # a kraje-only step after a boundaries ingest must not hide a dead quarterly job.
        if not args.dry_run and keys == list(SOURCES):
            stamp_derived_artifact(conn, "map_overlay_tiles", rows=count_tiles(conn),
                                   duration_ms=int((time.monotonic() - started) * 1000))
    LOG.info("RUN done sections=%s tiles=%d", ",".join(keys), tiles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
