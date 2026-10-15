"""scripts.ingest_map_overlays: the registry, the props mappers on real samples, the ArcGIS
pager's guards, the revision-date parsers, the shrink guard and the per-section flow.

No network and no database. tests/fixtures/map_overlays/ holds real responses fetched on
2026-10-14; the IPR line geometries were generalised server-side (maxAllowableOffset=25) to
keep them small, and the ISO record was stripped of its personal contact blocks.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from collections import Counter
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import requests

from scripts import ingest_map_overlays as mod
from scraper import db as scraper_db
from tests.sql_corpus import discover
from tests.test_sql_placeholders import _invalid_placeholder

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "map_overlays"
NAVRH_SERVICE = f"{mod.IPR_HOSTED}/MPP_CUR_MPP_600_DI_TRASY_L/FeatureServer"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _layer(key: str) -> mod.Layer:
    return next(layer for s in mod.SOURCES.values() for layer in s.layers if layer.key == key)


def _props(fixture: str, mapper: mod.Props) -> list[dict[str, Any] | None]:
    return [mapper(f["properties"]) for f in _load(fixture)["features"]]


# --- the registry -----------------------------------------------------------------------


def test_the_registry_holds_the_three_sections_in_order() -> None:
    assert {key: (s.label, s.sort, s.minzoom, s.maxzoom) for key, s in mod.SOURCES.items()} == {
        "metro": ("Metro", 1, 9, 12),
        "flood": ("Záplavová území", 2, 10, 12),
        "kraje": ("Kraje", 3, 3, 9),
    }
    assert list(mod.SOURCES) == ["metro", "flood", "kraje"]


def test_layers_their_sort_and_how_they_are_stored() -> None:
    assert {key: [(lyr.key, lyr.sort, lyr.store) for lyr in s.layers]
            for key, s in mod.SOURCES.items()} == {
        "metro": [("metro", 1, "plain")],
        "flood": [("flood_q5", 1, "dissolve"), ("flood_q20", 2, "dissolve"),
                  ("flood_q100", 3, "dissolve"), ("flood_q500", 4, "dissolve"),
                  ("flood_az", 5, "outline")],
        "kraje": [("kraje", 1, "plain")],
    }


def test_the_metro_legend() -> None:
    assert list(_layer("metro").legend) == [
        {"class": "A", "label": "Metro A", "color": "#00A562", "geom": "line"},
        {"class": "B", "label": "Metro B", "color": "#FCC400", "geom": "line"},
        {"class": "C", "label": "Metro C", "color": "#CF003D", "geom": "line"},
        {"class": "D", "label": "Metro D – návrh", "color": "#1F77D0", "dash": True},
        {"class": "reserve", "label": "Územní rezerva", "color": "#8C9196", "dash": True},
        {"class": "station", "label": "Plánovaná stanice", "color": "#1F77D0", "geom": "point"},
    ]


def test_the_flood_and_kraje_legends() -> None:
    assert [list(_layer(k).legend) for k in
            ("flood_q5", "flood_q20", "flood_q100", "flood_q500", "flood_az", "kraje")] == [
        [{"class": "q5", "label": "Q5 – pětiletá voda", "color": "#1D4F91"}],
        [{"class": "q20", "label": "Q20 – dvacetiletá voda", "color": "#3B7DD8"}],
        [{"class": "q100", "label": "Q100 – stoletá voda", "color": "#7FB3E6"}],
        [{"class": "q500", "label": "Q500 – mapováno jen místy", "color": "#C6DCF2"}],
        [{"class": "az", "label": "Aktivní zóna", "color": "#B3261E", "dash": True}],
        [{"class": "kraj", "label": "Hranice krajů", "color": "rgba(40,40,70,0.55)", "geom": "line"}],
    ]


def test_the_notes_attribution_and_licences() -> None:
    assert mod.SOURCES["metro"].note == (
        "Metropolitní plán hl. m. Prahy (platný od 1. 9. 2026): stav, návrh a územní rezervy. "
        "Rezerva „výhledové tangenciální kolejové spojení“ (v tisku metro E) nemá rozhodnutou "
        "trasu ani technologii."
    )
    assert mod.SOURCES["flood"].note == (
        "Stanovená záplavová území (zákonný rozsah, orientační). Nezohledňují protipovodňová "
        "opatření (např. Karlín), Q500 je vymezeno jen místy, překryv pásem je tmavší, "
        "přívalové povodně nejsou zahrnuty. Závazné informace: vodoprávní úřad."
    )
    assert mod.SOURCES["kraje"].note is None
    assert {k: (s.attribution, s.licence) for k, s in mod.SOURCES.items()} == {
        "metro": ("datový podklad © IPR Praha", "CC BY 4.0"),
        "flood": ("© VÚV TGM pro MŽP (ISVS VODA)", "CC BY 4.0"),
        "kraje": ("© ČÚZK (RÚIAN)", "CC BY 4.0"),
    }


def test_the_readers_ask_each_publisher_for_its_native_crs() -> None:
    metro = _layer("metro").readers
    assert [(r.layer_name, r.where, r.out_sr, r.geometry_type) for r in metro] == [
        ("MPP_CUR_MPP_STAV_600_DI_TRASY_L", "kapitola=621", 3857, mod.POLYLINE),
        ("MPP_CUR_MPP_600_DI_TRASY_L", "kapitola=621", 3857, mod.POLYLINE),
        ("MPP_CUR_MPP_600_DI_ZARIZENI_B", "kapitola=621 and druh=2", 3857, mod.POINT),
    ]
    assert all(r.service_url == f"{mod.IPR_HOSTED}/{r.layer_name}/FeatureServer" for r in metro)
    assert [(lyr.key, lyr.readers[0].layer_name, lyr.readers[0].out_sr)
            for lyr in mod.SOURCES["flood"].layers] == [
        ("flood_q5", "ZaplavUzemi_Q5", 5514), ("flood_q20", "ZaplavUzemi_Q20", 5514),
        ("flood_q100", "ZaplavUzemi_Q100", 5514), ("flood_q500", "ZaplavUzemi_Q500", 5514),
        ("flood_az", "ZaplavUzemi_AktivniZony", 5514),
    ]
    assert all(lyr.readers[0].service_url == mod.VUV_MAPSERVER for lyr in mod.SOURCES["flood"].layers)
    assert _layer("kraje").readers == (mod.SqlSource(mod.KRAJE_SQL),)


def test_registry_invariants() -> None:
    keys = [lyr.key for s in mod.SOURCES.values() for lyr in s.layers]
    assert len(keys) == len(set(keys))
    for section in mod.SOURCES.values():
        assert section.minzoom <= section.maxzoom
        assert section.attribution and section.licence and section.source_url
        for layer in section.layers:
            classes = [entry["class"] for entry in layer.legend]
            assert len(classes) == len(set(classes)), layer.key
            if layer.store != "plain":
                assert len(classes) == 1, f"{layer.key}: a dissolved layer has one class"
                assert all(isinstance(r, mod.ArcGisLayer) and r.geometry_type == mod.POLYGON
                           for r in layer.readers), layer.key


# --- the props mappers, on real samples -------------------------------------------------


def test_metro_lines_in_service_are_classed_by_their_letter() -> None:
    props = _props("ipr_metro_stav_query.json", mod.metro_line_props)
    assert sorted(p["class"] for p in props) == ["A", "B", "C"]
    assert all(set(p) == {"class"} for p in props), "nazev is null in service: no name, no dash"


def test_metro_proposals_take_the_letter_from_the_name_and_reserves_are_one_class() -> None:
    by_name = {p["name"]: p for p in _props("ipr_metro_navrh_query.json", mod.metro_line_props)}
    assert len(by_name) == 15
    assert by_name["Trasa metra B - prodloužení do Depa Zličín"] == {
        "class": "B", "name": "Trasa metra B - prodloužení do Depa Zličín", "dash": True,
    }
    assert by_name["Trasa metra D - úsek Pankrác - Nové Dvory"] == {
        "class": "D", "name": "Trasa metra D - úsek Pankrác - Nové Dvory", "dash": True,
    }
    assert by_name["Trasa metra A, úsek Želivského - Zahradní město"] == {
        "class": "reserve", "name": "Trasa metra A, úsek Želivského - Zahradní město",
    }
    assert Counter(p["class"] for p in by_name.values()) == {"D": 5, "B": 1, "reserve": 9}
    tangential = [n for n in by_name if n.startswith("Výhledové tangenciální spojení")]
    assert len(tangential) == 4
    assert all(by_name[n]["class"] == "reserve" and "dash" not in by_name[n] for n in tangential)


def test_planned_stations_are_labelled_without_the_prefix() -> None:
    props = _props("ipr_metro_stations_query.json", mod.metro_station_props)
    # The fixture's reserve-stage station (Labe, stav 3) is skipped, not drawn as planned.
    assert props.count(None) == 1
    assert sorted((p for p in props if p), key=lambda p: p["label"]) == [
        {"class": "station", "name": "Stanice metra Náměstí Míru (D)", "label": "Náměstí Míru (D)"},
        {"class": "station", "name": "Stanice metra Olbrachtova", "label": "Olbrachtova"},
    ]


def test_a_line_letter_is_normalised() -> None:
    assert mod.metro_line_props({"stav": 1, "parametr": " a "}) == {"class": "A"}


def test_cancelled_rows_vestibules_and_depots_are_skipped() -> None:
    assert mod.metro_line_props({"stav": 4, "nazev": "Trasa metra D - úsek X"}) is None
    assert mod.metro_station_props({"druh": 3, "stav": 2, "nazev": "Vestibul stanice metra Libuš"}) is None
    assert mod.metro_station_props({"druh": 1, "stav": 2, "nazev": "Depo metra Písnice"}) is None
    assert mod.metro_station_props({"druh": 2, "stav": 4, "nazev": "Stanice metra Kačerov"}) is None
    # Reserve-stage (stav 3) and existing (stav 1) station points are not "planned stations".
    assert mod.metro_station_props({"druh": 2, "stav": 3, "nazev": "Stanice metra Labe"}) is None
    assert mod.metro_station_props({"druh": 2, "stav": 1, "nazev": "Stanice metra Florenc"}) is None


@pytest.mark.parametrize("attrs", [
    {"stav": 2, "nazev": "Výhledové kolejové spojení Pankrác - Smíchov"},
    {"stav": 5, "nazev": "Trasa metra A"},
])
def test_a_line_the_mapper_cannot_class_is_refused(attrs: dict[str, Any]) -> None:
    with pytest.raises(mod.Refused):
        mod.metro_line_props(attrs)


def test_flood_features_keep_the_watercourse_as_their_name() -> None:
    assert _props("vuv_q5_query.json", mod.flood_props("q5")) == [
        {"class": "q5", "name": "Olešenka"}, {"class": "q5", "name": "Rakovec"},
    ]


# --- the ArcGIS pager -------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int, payload: Any = None, content: bytes = b"") -> None:
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakeServer:
    """One ArcGIS layer served from its fixtures; attributes inject the faults."""

    def __init__(self, service_url: str, service: str, layer_id: int, layer: str,
                 collection: str, oid: str, log: list[str] | None = None) -> None:
        self.service_url = service_url
        self.service = _load(service)
        self.layer_url = f"{service_url}/{layer_id}"
        self.layer = _load(layer)
        self.features = {f["id"]: f for f in _load(collection)["features"]}
        self.oid = oid
        self.log = log if log is not None else []
        self.count: int | None = None
        self.ids_exceeded = False
        self.page_exceeded = False
        self.drop: set[int] = set()
        self.no_geometry: set[int] = set()
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def get(self, url: str, params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None, timeout: Any = None) -> _Resp:
        params = dict(params or {})
        self.calls.append((url, params, dict(headers or {})))
        self.log.append("GET")
        if url == self.service_url and params == {"f": "json"}:
            return _Resp(200, self.service)
        if url == self.layer_url and params == {"f": "json"}:
            return _Resp(200, self.layer)
        if url != f"{self.layer_url}/query":
            return _Resp(404)
        if params.get("returnIdsOnly") == "true":
            return _Resp(200, {"exceededTransferLimit": self.ids_exceeded,
                               "objectIdFieldName": self.oid, "objectIds": list(self.features)})
        if params.get("returnCountOnly") == "true":
            return _Resp(200, {"count": len(self.features) if self.count is None else self.count})
        page = []
        for fid in (int(i) for i in params["objectIds"].split(",")):
            if fid not in self.drop:
                feature = dict(self.features[fid])
                if fid in self.no_geometry:
                    feature["geometry"] = None
                page.append(feature)
        return _Resp(200, {"type": "FeatureCollection", "features": page,
                           "properties": {"exceededTransferLimit": self.page_exceeded}})


def _navrh_server() -> _FakeServer:
    return _FakeServer(NAVRH_SERVICE, "ipr_metro_navrh_service.json", 0,
                       "ipr_metro_navrh_layer.json", "ipr_metro_navrh_query.json", "objectid")


def _q5_server(log: list[str] | None = None) -> _FakeServer:
    return _FakeServer(mod.VUV_MAPSERVER, "vuv_service.json", 15, "vuv_q5_layer.json",
                       "vuv_q5_query.json", "OBJECTID", log)


def _http(session: Any, sleeps: list[float] | None = None) -> mod.Http:
    return mod.Http(session, sleep=(sleeps if sleeps is not None else []).append,
                    clock=lambda: 0.0)


def _navrh_reader() -> mod.ArcGisLayer:
    return _layer("metro").readers[1]


def test_the_pager_resolves_by_name_and_fetches_in_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "CHUNK", 4)
    server, sleeps = _navrh_server(), []
    features = list(mod.ArcGis(_http(server, sleeps)).features(_navrh_reader()))

    assert [f.source_id for f in features] == [str(i) for i in sorted(server.features)]
    assert {(f.srid, json.loads(f.geometry)["type"]) for f in features} == {
        (3857, "LineString"), (3857, "MultiLineString"),
    }
    assert [url for url, _, _ in server.calls[:2]] == [NAVRH_SERVICE, f"{NAVRH_SERVICE}/0"]
    assert server.calls[2][1] == {"where": "kapitola=621", "returnIdsOnly": "true", "f": "json"}
    assert server.calls[3][1] == {"where": "kapitola=621", "returnCountOnly": "true", "f": "json"}
    pages = [params for _, params, _ in server.calls if "objectIds" in params]
    assert [len(p["objectIds"].split(",")) for p in pages] == [4, 4, 4, 3]
    assert all((p["outFields"], p["outSR"], p["f"]) == ("*", 3857, "geojson") for p in pages)
    assert all(headers["User-Agent"] == mod.USER_AGENT for _, _, headers in server.calls)
    assert mod.USER_AGENT == "sreality-tracker/1.0 (+https://github.com/waiff/sreality)"
    assert sleeps == [mod.PAUSE_S] * (len(server.calls) - 1)


def test_the_default_chunk_is_at_most_fifty_ids() -> None:
    assert mod.CHUNK <= 50


def test_the_pager_reads_flood_zones_in_the_native_crs() -> None:
    server = _q5_server()
    features = list(mod.ArcGis(_http(server)).features(_layer("flood_q5").readers[0]))
    assert [(f.source_id, f.props, f.srid) for f in features] == [
        ("678", {"class": "q5", "name": "Olešenka"}, 5514),
        ("928", {"class": "q5", "name": "Rakovec"}, 5514),
    ]
    assert server.calls[0][0] == mod.VUV_MAPSERVER and server.calls[1][0].endswith("/MapServer/15")
    assert json.loads(features[0].geometry)["coordinates"][0][0][0] < -400000  # S-JTSK metres


@pytest.mark.parametrize("fault", ["ids_exceeded", "page_exceeded", "short_page",
                                   "null_geometry", "count_mismatch"])
def test_the_pager_refuses_a_partial_answer(fault: str) -> None:
    server = _navrh_server()
    if fault == "ids_exceeded":
        server.ids_exceeded = True
    elif fault == "page_exceeded":
        server.page_exceeded = True
    elif fault == "short_page":
        server.drop = {37}
    elif fault == "null_geometry":
        server.no_geometry = {37}
    else:
        server.count = 16
    with pytest.raises(mod.Refused):
        list(mod.ArcGis(_http(server)).features(_navrh_reader()))


@pytest.mark.parametrize("change", [
    {"layer_name": "MPP_CUR_MPP_600_DI_TRASY_X"},
    {"geometry_type": mod.POINT},
    {"fields": ("kapitola", "stav", "parametr", "nazev", "trasa")},
])
def test_layer_resolution_asserts_the_name_the_geometry_and_the_fields(change: dict[str, Any]) -> None:
    reader = dataclasses.replace(_navrh_reader(), **change)
    with pytest.raises(mod.Refused):
        mod.ArcGis(_http(_navrh_server())).layer(reader)


def test_a_layer_spelling_its_fields_in_another_case_is_read_by_the_declared_names() -> None:
    """The register's Q500 layer spells `Naz_tok` where the other four spell `NAZ_TOK` — the
    first production run refused the whole flood section on it (2026-10-15)."""
    server = _FakeServer(mod.VUV_MAPSERVER, "vuv_service.json", 12, "vuv_q5_layer.json",
                         "vuv_q5_query.json", "OBJECTID")
    server.layer = {**server.layer, "id": 12, "name": "ZaplavUzemi_Q500", "fields": [
        {**f, "name": "Naz_tok" if f["name"] == "NAZ_TOK" else f["name"]}
        for f in server.layer["fields"]]}
    for feature in server.features.values():
        feature["properties"] = {("Naz_tok" if k == "NAZ_TOK" else k): v
                                 for k, v in feature["properties"].items()}
    reader = mod._flood("ZaplavUzemi_Q500", "q500")
    names = [f.props.get("name") for f in mod.ArcGis(_http(server)).features(reader)]
    assert names and all(names)
    with pytest.raises(mod.Refused, match="missing fields"):
        mod.ArcGis(_http(server)).layer(dataclasses.replace(reader, fields=("NAZ_TOK", "Rec_naz_x")))


def test_the_layer_metadata_is_fetched_once() -> None:
    server = _navrh_server()
    arcgis = mod.ArcGis(_http(server))
    arcgis.layer(_navrh_reader())
    arcgis.layer(_navrh_reader())
    assert len(server.calls) == 2


class _Routes:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def get(self, url: str, params: Any = None, headers: Any = None, timeout: Any = None) -> _Resp:
        self.urls.append(url)
        return _Resp(200, self.routes[url]) if url in self.routes else _Resp(404)


def test_one_service_listing_serves_every_layer_of_that_service() -> None:
    q20_info = {**_load("vuv_q5_layer.json"), "id": 14, "name": "ZaplavUzemi_Q20"}
    session = _Routes({
        mod.VUV_MAPSERVER: _load("vuv_service.json"),
        f"{mod.VUV_MAPSERVER}/15": _load("vuv_q5_layer.json"),
        f"{mod.VUV_MAPSERVER}/14": q20_info,
    })
    arcgis = mod.ArcGis(_http(session))
    assert arcgis.layer(_layer("flood_q5").readers[0])[0] == f"{mod.VUV_MAPSERVER}/15"
    assert arcgis.layer(_layer("flood_q20").readers[0])[0] == f"{mod.VUV_MAPSERVER}/14"
    assert session.urls == [mod.VUV_MAPSERVER, f"{mod.VUV_MAPSERVER}/15", f"{mod.VUV_MAPSERVER}/14"]


class _Scripted:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, url: str, params: Any = None, headers: Any = None, timeout: Any = None) -> _Resp:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_http_backs_off_on_429_5xx_and_network_errors() -> None:
    session = _Scripted(_Resp(503), requests.ConnectionError("reset"), _Resp(429),
                        _Resp(200, {"ok": 1}))
    sleeps: list[float] = []
    assert _http(session, sleeps).json("https://example.test/q") == {"ok": 1}
    assert session.calls == 4
    assert [s for s in sleeps if s != mod.PAUSE_S] == [2.0, 4.0, 8.0]


def test_http_retries_an_arcgis_5xx_body_but_not_a_client_error() -> None:
    session = _Scripted(_Resp(200, {"error": {"code": 500, "message": "busy"}}), _Resp(200, {"ok": 1}))
    assert _http(session).json("https://example.test/q") == {"ok": 1}
    with pytest.raises(RuntimeError, match="bad where"):
        _http(_Scripted(_Resp(200, {"error": {"code": 400, "message": "bad where"}}))).json("https://x.test")
    not_found = _Scripted(_Resp(404))
    with pytest.raises(requests.HTTPError):
        _http(not_found).json("https://x.test")
    assert not_found.calls == 1


def test_http_retries_a_connection_dropped_mid_body() -> None:
    session = _Scripted(requests.exceptions.ChunkedEncodingError("Connection broken"),
                        _Resp(200, {"ok": 1}))
    assert _http(session).json("https://example.test/q") == {"ok": 1}
    assert session.calls == 2


def test_a_transient_revision_source_degrades_to_none_not_a_failed_section() -> None:
    """heis.vuv.cz answering 503 on the ISO record must not throw away a finished flood fetch."""
    def busy(section: mod.Section, ctx: mod.Context) -> date | None:
        raise mod._Transient("HTTP 503")

    conn = _FakeConn()
    http = _http(_q5_server())
    section = dataclasses.replace(_q5_section(), revised_at=busy)
    mod.run_section(conn, http, mod.ArcGis(http), "flood", section,
                    dry_run=False, accept_shrink=False)
    assert conn.params["section"][0]["revised"] is None and conn.log[-1] == "COMMIT"


def test_http_gives_up_after_the_last_attempt() -> None:
    session = _Scripted(*[_Resp(502) for _ in range(mod.ATTEMPTS)])
    with pytest.raises(Exception, match="HTTP 502"):
        _http(session).json("https://example.test/q")
    assert session.calls == mod.ATTEMPTS


# --- the source revision dates ----------------------------------------------------------


def test_the_iso_record_gives_the_dataset_revision_date() -> None:
    assert mod.parse_iso_revision((FIXTURES / "vuv_iso_record.xml").read_bytes()) == date(2025, 8, 31)


def test_without_a_revision_date_the_record_date_stamp_is_used() -> None:
    xml = (FIXTURES / "vuv_iso_record.xml").read_text(encoding="utf-8")
    without = xml.replace('codeListValue="revision"', 'codeListValue="creation"')
    assert without != xml
    assert mod.parse_iso_revision(without) == date(2025, 10, 30)
    empty = '<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd"/>'
    assert mod.parse_iso_revision(empty) is None


def test_the_hosted_layer_edit_date_is_a_utc_date() -> None:
    ipr, vuv = _load("ipr_metro_navrh_layer.json"), _load("vuv_q5_layer.json")
    assert ipr["editingInfo"]["lastEditDate"] == 1791441589572
    assert mod.latest_edit_date([ipr, vuv]) == date(2026, 10, 8)
    assert mod.latest_edit_date([vuv]) is None
    newer = {"editingInfo": {"lastEditDate": 1791528640144}}
    assert mod.latest_edit_date([ipr, newer]) == date(2026, 10, 9)


# --- the shrink guard -------------------------------------------------------------------


@pytest.mark.parametrize(("count", "previous"), [(964, 964), (868, 964), (1000, 964), (5, 0)])
def test_the_shrink_guard_passes(count: int, previous: int) -> None:
    mod.check_shrink("flood_q5", count, previous, accept_shrink=False)


@pytest.mark.parametrize(("count", "previous"), [(867, 964), (0, 964), (0, 0)])
def test_the_shrink_guard_refuses_a_shrunk_or_empty_layer(count: int, previous: int) -> None:
    with pytest.raises(mod.Refused, match="accept-shrink"):
        mod.check_shrink("flood_q5", count, previous, accept_shrink=False)
    mod.check_shrink("flood_q5", count, previous, accept_shrink=True)


# --- the SQL ----------------------------------------------------------------------------


def test_every_sql_constant_passes_the_placeholder_rules() -> None:
    items = [i for i in discover(include_inline=False)
             if i.origin.startswith("scripts/ingest_map_overlays.py")]
    assert {i.name for i in items} >= {
        "DATUM_CHECK_SQL", "PREVIOUS_COUNTS_SQL", "UPSERT_SECTION_SQL", "PRUNE_LAYERS_SQL",
        "UPSERT_LAYER_SQL", "DELETE_FEATURES_SQL", "INSERT_FEATURE_SQL", "DISSOLVE_SQL",
        "RENDER_SQL", "SECTION_STATS_SQL", "SELECT_SOURCE_SQL", "KRAJE_SQL", "KRAJE_REVISED_SQL",
    }
    assert [(i.name, err) for i in items if (err := _invalid_placeholder(i.sql))] == []


def _compact(text: str) -> str:
    return "".join(text.split())


def test_the_insert_makes_valid_reprojects_and_subdivides() -> None:
    assert ("st_subdivide(st_makevalid(st_transform(st_setsrid(st_force2d("
            "st_geomfromgeojson(%s::text)),%s),3857)),256)") in _compact(mod.INSERT_FEATURE_SQL)


def test_a_dissolve_unions_the_layer_then_subdivides_and_names_the_pieces() -> None:
    text = _compact(mod.DISSOLVE_SQL)
    assert "st_unaryunion(st_collect(st_collectionextract(geom,3)))" in text
    assert "st_subdivide(casewhen%(outline)sthenst_boundary(merged)elsemergedend,256)" in text
    assert "deletefrommap_overlay_featureswherelayer_key=%(layer)s" in text
    assert "st_dwithin(f.geom,pieces.anchor,0.5)" in text


def test_the_kraje_source_draws_each_kraj_boundary_in_3857() -> None:
    text = _compact(mod.KRAJE_SQL)
    assert "st_transform(st_boundary(b.geom::geometry),3857)" in text
    assert "whereb.level='kraj'" in text


# --- the per-section flow ---------------------------------------------------------------


_TAGS = {
    mod.DATUM_CHECK_SQL: "datum", mod.PREVIOUS_COUNTS_SQL: "previous",
    mod.STATEMENT_TIMEOUT_SQL: "timeout", mod.LOCK_SQL: "lock",
    scraper_db.STAMP_DERIVED_ARTIFACT_SQL: "stamp", mod.UPSERT_SECTION_SQL: "section",
    mod.PRUNE_LAYERS_SQL: "prune", mod.UPSERT_LAYER_SQL: "layer",
    mod.DELETE_FEATURES_SQL: "delete", mod.INSERT_FEATURE_SQL: "insert",
    mod.DISSOLVE_SQL: "dissolve", mod.RENDER_SQL: "render", mod.SECTION_STATS_SQL: "stats",
    mod.TILE_COUNT_SQL: "tilecount",
}


class _FakeCursor:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self.rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, query: Any, params: Any = None) -> None:
        if not isinstance(query, str):
            self.conn.log.append("select-source")
            self.conn.composed.append(query.as_string(None))
            self.rows = list(self.conn.source_rows)
            return
        tag = _TAGS[query]
        self.conn.log.append(tag)
        self.conn.params.setdefault(tag, []).append(params)
        self.rows = {
            "datum": [(self.conn.datum,)],
            "previous": list(self.conn.previous.items()),
            "render": [(42,)],
            "stats": [(100, 5000, 1200)],
            "tilecount": [(1234,)],
        }.get(tag, [])

    def executemany(self, query: str, rows: list[tuple[Any, ...]]) -> None:
        self.conn.log.append(_TAGS[query])
        self.conn.inserted.extend(rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class _FakeConn:
    def __init__(self, *, datum: float = 0.44, previous: dict[str, int] | None = None) -> None:
        self.datum = datum
        self.previous = previous or {}
        self.log: list[str] = []
        self.params: dict[str, list[Any]] = {}
        self.inserted: list[tuple[Any, ...]] = []
        self.composed: list[str] = []
        self.source_rows: list[tuple[Any, ...]] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        self.log.append("BEGIN")
        try:
            yield
        except BaseException:
            self.log.append("ROLLBACK")
            raise
        self.log.append("COMMIT")


def _q5_section() -> mod.Section:
    flood = mod.SOURCES["flood"]
    return dataclasses.replace(flood, layers=flood.layers[:1],
                               revised_at=lambda section, ctx: date(2025, 8, 31))


def _run(conn: _FakeConn, server: _FakeServer, *, dry_run: bool = False,
         accept_shrink: bool = False) -> None:
    http = _http(server)
    mod.run_section(conn, http, mod.ArcGis(http), "flood", _q5_section(),
                    dry_run=dry_run, accept_shrink=accept_shrink)


def test_a_section_is_fetched_first_then_written_in_one_transaction() -> None:
    conn = _FakeConn()
    _run(conn, _q5_server(conn.log))

    writes = conn.log[conn.log.index("BEGIN"):]
    assert conn.log[:2] == ["datum", "previous"]
    assert "GET" not in writes
    assert writes == ["BEGIN", "timeout", "lock", "section", "prune", "layer", "delete", "insert",
                      "dissolve", "render", "stats", "COMMIT"]
    assert conn.params["section"][0]["revised"] == date(2025, 8, 31)
    assert conn.params["prune"] == [("flood", ["flood_q5"])]
    layer = conn.params["layer"][0]
    assert (layer["key"], layer["section"], layer["feature_count"]) == ("flood_q5", "flood", 2)
    assert layer["legend"].obj == [{"class": "q5", "label": "Q5 – pětiletá voda", "color": "#1D4F91"}]
    assert [(r[0], r[1], r[2].obj, r[4]) for r in conn.inserted] == [
        ("flood_q5", "678", {"class": "q5", "name": "Olešenka"}, 5514),
        ("flood_q5", "928", {"class": "q5", "name": "Rakovec"}, 5514),
    ]
    assert conn.params["dissolve"] == [{"layer": "flood_q5", "class": "q5", "outline": False}]
    assert conn.params["render"] == [("flood",)]


def test_the_active_zone_is_stored_as_the_outline_of_its_union() -> None:
    conn = _FakeConn()
    feature = mod.Feature("1", {"class": "az", "name": "Vltava"}, '{"type":"Polygon"}', 5514)
    mod.write_section(conn, "flood", mod.SOURCES["flood"], [(_layer("flood_az"), [feature])], None)
    assert conn.params["dissolve"] == [{"layer": "flood_az", "class": "az", "outline": True}]


def test_a_plain_layer_is_not_dissolved() -> None:
    conn = _FakeConn()
    feature = mod.Feature("2", {"class": "A"}, '{"type":"LineString"}', 3857)
    mod.write_section(conn, "metro", mod.SOURCES["metro"], [(_layer("metro"), [feature])], None)
    assert "dissolve" not in conn.log and conn.log[-1] == "COMMIT"


def test_a_dry_run_fetches_and_checks_but_writes_nothing() -> None:
    conn = _FakeConn()
    server = _q5_server(conn.log)
    _run(conn, server, dry_run=True)
    assert "BEGIN" not in conn.log and conn.inserted == []
    assert any("objectIds" in params for _, params, _ in server.calls)


def test_a_shifted_datum_refuses_before_any_fetch() -> None:
    conn = _FakeConn(datum=10.3)
    server = _q5_server(conn.log)
    with pytest.raises(mod.DatumRefused):
        _run(conn, server)
    assert conn.log == ["datum"] and server.calls == []


def test_a_shrunk_layer_is_refused_before_the_transaction_unless_accepted() -> None:
    conn = _FakeConn(previous={"flood_q5": 964})
    with pytest.raises(mod.Refused, match="flood_q5"):
        _run(conn, _q5_server(conn.log))
    assert "BEGIN" not in conn.log

    accepted = _FakeConn(previous={"flood_q5": 964})
    _run(accepted, _q5_server(accepted.log), accept_shrink=True)
    assert accepted.log[-1] == "COMMIT"


def test_an_unreadable_revision_date_degrades_to_none() -> None:
    def unreachable(section: mod.Section, ctx: mod.Context) -> date | None:
        raise requests.ConnectionError("heis.vuv.cz is down")

    conn = _FakeConn()
    http = _http(_q5_server())
    section = dataclasses.replace(_q5_section(), revised_at=unreachable)
    mod.run_section(conn, http, mod.ArcGis(http), "flood", section,
                    dry_run=False, accept_shrink=False)
    assert conn.params["section"][0]["revised"] is None and conn.log[-1] == "COMMIT"


def test_a_class_outside_the_legend_is_refused() -> None:
    conn = _FakeConn()
    section = dataclasses.replace(_q5_section(), layers=(
        dataclasses.replace(_layer("flood_q5"), legend=(
            {"class": "q20", "label": "Q20", "color": "#3B7DD8"},)),))
    http = _http(_q5_server())
    with pytest.raises(mod.Refused, match="not in its legend"):
        mod.run_section(conn, http, mod.ArcGis(http), "flood", section,
                        dry_run=False, accept_shrink=False)


def test_the_sql_source_reads_its_select_through_the_geojson_wrapper() -> None:
    conn = _FakeConn()
    conn.source_rows = [(19, {"class": "kraj", "name": "Hlavní město Praha"}, '{"type":"MultiLineString"}')]
    http = _http(_Scripted())
    features = list(mod.SqlSource(mod.KRAJE_SQL).read(mod.Context(http, mod.ArcGis(http), conn)))
    assert features == [mod.Feature("19", {"class": "kraj", "name": "Hlavní město Praha"},
                                    '{"type":"MultiLineString"}', 3857)]
    assert conn.composed[0].startswith("select s.source_id, s.props, st_asgeojson(s.geom) from (")
    assert mod.KRAJE_SQL in conn.composed[0]


# --- the command line -------------------------------------------------------------------


def test_sections_are_selected_in_registry_order() -> None:
    assert mod.select_sections("") == ["metro", "flood", "kraje"]
    assert mod.select_sections("kraje, metro") == ["metro", "kraje"]
    with pytest.raises(ValueError, match="tram"):
        mod.select_sections("metro,tram")


def test_an_unknown_section_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        mod.main(["--sections", "tram"])
    assert exc.value.code == 2


def _fake_run(seen: list[str], fail: dict[str, Exception]) -> Any:
    def run(conn: Any, http: Any, arcgis: Any, key: str, section: Any, **kwargs: Any) -> int:
        seen.append(key)
        if key in fail:
            raise fail[key]
        return 7
    return run


def test_one_refused_section_does_not_block_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(mod, "run_section", _fake_run(seen, {"flood": mod.Refused("shrunk")}))
    monkeypatch.setattr(mod, "connect", lambda: contextlib.nullcontext(object()))
    assert mod.main([]) == 1
    assert seen == ["metro", "flood", "kraje"]


def test_a_datum_refusal_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(mod, "run_section", _fake_run(seen, {"metro": mod.DatumRefused("off")}))
    monkeypatch.setattr(mod, "connect", lambda: contextlib.nullcontext(object()))
    assert mod.main([]) == 1
    assert seen == ["metro"]


def test_a_clean_full_run_exits_zero_and_stamps_the_store_wide_tile_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    stamps: list[tuple[Any, ...]] = []
    monkeypatch.setattr(mod, "run_section", _fake_run(seen, {}))
    monkeypatch.setattr(mod, "connect", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(mod, "count_tiles", lambda conn: 1234)
    monkeypatch.setattr(mod, "stamp_derived_artifact",
                        lambda conn, name, **kw: stamps.append((name, kw["rows"])))
    assert mod.main([]) == 0
    assert seen == list(mod.SOURCES)
    assert stamps == [("map_overlay_tiles", 1234)]


def test_a_partial_run_stamps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """ingest_boundaries' `--sections kraje` step must not mark metro + flood fresh."""
    seen: list[str] = []
    stamps: list[str] = []
    monkeypatch.setattr(mod, "run_section", _fake_run(seen, {}))
    monkeypatch.setattr(mod, "connect", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(mod, "stamp_derived_artifact",
                        lambda conn, name, **kw: stamps.append(name))
    assert mod.main(["--sections", "kraje"]) == 0
    assert seen == ["kraje"]
    assert stamps == []


def test_a_dry_run_and_a_failed_run_stamp_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    stamps: list[str] = []
    monkeypatch.setattr(mod, "connect", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(mod, "stamp_derived_artifact",
                        lambda conn, name, **kw: stamps.append(name))
    monkeypatch.setattr(mod, "run_section", _fake_run([], {}))
    assert mod.main(["--sections", "kraje", "--dry-run"]) == 0
    monkeypatch.setattr(mod, "run_section", _fake_run([], {"kraje": mod.Refused("short page")}))
    assert mod.main(["--sections", "kraje"]) == 1
    assert stamps == []
