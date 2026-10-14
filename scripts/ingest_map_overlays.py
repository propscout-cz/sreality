"""Ingest the map reference overlays and render their vector tiles (docs/design/map-overlays.md).

One registry, SOURCES, maps each section to its layers and each layer to its readers. Per
section: the datum control point, every layer fetched under the completeness and shrink
guards, then ONE transaction that replaces the features, upserts the registry rows and runs
map_overlay_render_section. --dry-run fetches and checks everything and writes nothing.

    python -m scripts.ingest_map_overlays [--sections metro,flood] [--dry-run] [--accept-shrink]
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal

import psycopg
import requests
from psycopg import sql
from psycopg.types.json import Jsonb

from scraper.db import connect, stamp_derived_artifact

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
VUV_MAPSERVER = "https://ags2.vuv.cz/arcgis/rest/services/isvs_voda/isvs_voda/MapServer"
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

# A dissolve layer is stored first as its source features (INSERT_FEATURE_SQL), then
# replaced in place by the subdivided union, so same-band overlaps (Berounka and Vltava
# share ~16 km2 of Q100) cannot render darker. Each piece is named after the source
# features under its anchor point; the snapshot still sees the rows being deleted.
DISSOLVE_SQL = """
with pieces as (
    select piece, st_pointonsurface(piece) as anchor
      from (select st_subdivide(
                       case when %(outline)s then st_boundary(merged) else merged end,
                       256) as piece
              from (select st_unaryunion(st_collect(st_collectionextract(geom, 3))) as merged
                      from map_overlay_features
                     where layer_key = %(layer)s) dissolved) subdivided
),
replaced as (
    delete from map_overlay_features where layer_key = %(layer)s
)
insert into map_overlay_features (layer_key, source_id, props, geom)
select %(layer)s, origin.source_id,
       jsonb_strip_nulls(jsonb_build_object('class', %(class)s::text, 'name', origin.name)),
       pieces.piece
  from pieces
  left join lateral (
      select min(f.source_id) as source_id,
             string_agg(distinct f.props ->> 'name', ', ' order by f.props ->> 'name') as name
        from map_overlay_features f
       where f.layer_key = %(layer)s
         and st_dwithin(f.geom, pieces.anchor, 0.5)
  ) origin on true
"""

RENDER_SQL = "select map_overlay_render_section(%s)"

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


class Refused(RuntimeError):
    """A guard refused the section; nothing of it was written."""


class DatumRefused(Refused):
    """PostGIS's S-JTSK transform is off, so no section can be trusted."""


class _Transient(Exception):
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
    readers: tuple[ArcGisLayer | SqlSource, ...]
    store: Literal["plain", "dissolve", "outline"] = "plain"
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
                    _Transient) as exc:
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
            missing = set(src.fields) - {f.get("name") for f in info.get("fields") or []}
            if missing:
                raise Refused(f"{url}: missing fields {sorted(missing)}")
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
            for fid in chunk:
                feature = by_id[fid]
                props = src.props(feature.get("properties") or {})
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
    """Named planned stations; vestibules (druh 3), depots (druh 1) and stav 4 are skipped."""
    if a.get("druh") != 2 or a.get("stav") == 4:
        return None
    name = (a.get("nazev") or "").strip() or None
    label = name.removeprefix(STATION_PREFIX) if name else None
    return _flat({"class": "station", "name": name, "label": label})


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


def _ipr(service: str, where: str, geometry_type: str, props: Props,
         fields: tuple[str, ...]) -> ArcGisLayer:
    return ArcGisLayer(f"{IPR_HOSTED}/{service}/FeatureServer", service, where, 3857,
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
             "nemá rozhodnutou trasu ani technologii.",
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
        label="Kraje", sort=3, minzoom=5, maxzoom=9,
        attribution="© ČÚZK (RÚIAN)", licence="CC BY 4.0", source_url=RUIAN_ATOM, note=None,
        revised_at=sql_revised_at(KRAJE_REVISED_SQL),
        layers=(
            Layer("kraje", "Kraje", 1, (_entry("kraj", "Hranice krajů", "rgba(40,40,70,0.55)", geom="line"),),
                  (SqlSource(KRAJE_SQL),)),
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
            for batch in itertools.batched(features, INSERT_BATCH):
                cur.executemany(INSERT_FEATURE_SQL, [
                    (layer.key, f.source_id, Jsonb(f.props), f.geometry, f.srid) for f in batch
                ])
            if layer.store != "plain":
                cur.execute(DISSOLVE_SQL, {
                    "layer": layer.key, "class": layer.legend[0]["class"],
                    "outline": layer.store == "outline",
                })
        cur.execute(RENDER_SQL, (key,))
        tiles = int(cur.fetchone()[0])
        cur.execute(SECTION_STATS_SQL, {"section": key})
        rows, max_bytes, avg_bytes = cur.fetchone()
    return int(rows), tiles, int(max_bytes), int(avg_bytes)


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
        if not args.dry_run:
            stamp_derived_artifact(conn, "map_overlay_tiles", rows=tiles,
                                   duration_ms=int((time.monotonic() - started) * 1000))
    LOG.info("RUN done sections=%s tiles=%d", ",".join(keys), tiles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
