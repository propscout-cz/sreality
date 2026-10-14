"""Reference overlays, executed (migration 600, docs/design/map-overlays.md): the datum control
point the ingest checks before it trusts PostGIS with S-JTSK, `map_overlay_render_section` on a
square, a line and a point (one MVT layer per layer key, feature ids, props as attributes, the
tile range from the Web Mercator formula), the store's posture, and the MF rent choropleth
(section `rent_map`, migration 601's cleanup) rendered from admin_boundaries × the latest
rent_map revision, both by the database-only render and inside a rent-map ingest. Runs in CI's
migrations job (`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import date
from typing import Any, Iterator

import psycopg
import pytest

from api.rent_map import insert_revision
from scripts import ingest_map_overlays as overlays
from tests._live_property import REQUIRED_DB, cur  # noqa: F401 -- `cur` is the fixture
from toolkit.rent_map import ParsedRentMap, RentValue

pytestmark = REQUIRED_DB

FLORENC_LON, FLORENC_LAT = 14.4369316, 50.09053
MINZOOM, MAXZOOM = 10, 12
SQUARE = "ST_MakeEnvelope(ST_X(p) - 1000, ST_Y(p) - 1000, ST_X(p) + 1000, ST_Y(p) + 1000, 3857)"
LINE = "ST_MakeLine(ST_Translate(p, -1500, -300), ST_Translate(p, 1500, 300))"
POINT = "p"
TABLES = ("map_overlay_sections", "map_overlay_layers", "map_overlay_features", "map_overlay_tiles")
RENDER = "public.map_overlay_render_section(text)"


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, i
        shift += 7


def _fields(buf: bytes) -> Iterator[tuple[int, Any]]:
    """(field number, value) of one protobuf message; MVT here uses varints and length-delimited only."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        if key & 7 == 0:
            value, i = _varint(buf, i)
            yield key >> 3, value
        elif key & 7 == 2:
            size, i = _varint(buf, i)
            yield key >> 3, buf[i:i + size]
            i += size
        else:
            raise AssertionError(f"unexpected protobuf wire type {key & 7}")


def _packed(buf: bytes) -> list[int]:
    out, i = [], 0
    while i < len(buf):
        value, i = _varint(buf, i)
        out.append(value)
    return out


def _value(buf: bytes) -> str | int:
    """One MVT Value: string_value (1), or an integer as int_value (4), uint_value (5) or the
    zigzag sint_value (6). PostGIS writes a jsonb integer >= 0 as uint_value."""
    ((field, value),) = list(_fields(buf))
    assert field in (1, 4, 5, 6), f"unexpected MVT value field {field}"
    if field == 1:
        return value.decode()
    return (value >> 1) ^ -(value & 1) if field == 6 else value


def _layers(tile: bytes) -> dict[str, list[tuple[int, int, dict[str, str | int]]]]:
    """Layer name -> [(feature id, geometry type, attributes)] of an MVT tile."""
    out: dict[str, list[tuple[int, int, dict[str, str | int]]]] = {}
    for field, layer in _fields(tile):
        assert field == 3, f"a tile holds only layers, got field {field}"
        msg = list(_fields(layer))
        keys = [v.decode() for f, v in msg if f == 3]
        values = [_value(v) for f, v in msg if f == 4]
        features = []
        for f, v in msg:
            if f == 2:
                feature = dict(_fields(v))
                tags = _packed(feature.get(2, b""))
                attrs = {keys[k]: values[t] for k, t in zip(tags[::2], tags[1::2])}
                features.append((feature[1], feature[3], attrs))
        name = next(v for f, v in msg if f == 1).decode()
        out[name] = sorted(features)
    return out


def _tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _section(cur: Any) -> tuple[str, str, str]:
    section = f"ci_{uuid.uuid4().hex[:12]}"
    square, line = f"{section}_square", f"{section}_line"
    cur.execute(
        "INSERT INTO map_overlay_sections (key, label, sort, minzoom, maxzoom, attribution, "
        "licence, source_url) VALUES (%s, 'CI', 99, %s, %s, 'CI', 'CC BY 4.0', "
        "'https://example.invalid/ci')",
        (section, MINZOOM, MAXZOOM),
    )
    cur.execute(
        "INSERT INTO map_overlay_layers (key, section, label, sort) "
        "VALUES (%s, %s, 'Square', 1), (%s, %s, 'Line', 2)",
        (square, section, line, section),
    )
    return section, square, line


def _feature(cur: Any, layer_key: str, props: dict[str, str], geom_sql: str) -> int:
    """One feature built around Florenc in EPSG:3857; `p` is the centre point."""
    cur.execute(
        "WITH c AS (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 3857) AS p) "
        "INSERT INTO map_overlay_features (layer_key, source_id, props, geom) "
        f"SELECT %s, %s, %s::jsonb, {geom_sql} FROM c RETURNING id",
        (FLORENC_LON, FLORENC_LAT, layer_key, f"ci-{uuid.uuid4().hex[:8]}", json.dumps(props)),
    )
    return int(cur.fetchone()[0])


def _render(cur: Any, section: str) -> tuple[int, dict[tuple[int, int, int], bytes]]:
    cur.execute("SELECT map_overlay_render_section(%s)", (section,))
    count = int(cur.fetchone()[0])
    cur.execute("SELECT z, x, y, mvt FROM map_overlay_tiles WHERE section = %s", (section,))
    return count, {(z, x, y): bytes(mvt) for z, x, y, mvt in cur.fetchall()}


def test_the_datum_control_point_lands_within_a_metre(cur: Any) -> None:
    """Florenc M1B (PID stop 689/101) in S-JTSK and in WGS84 meet in EPSG:3857 within 1 m: the
    flood ingest fetches S-JTSK and lets PostGIS transform, because the publisher's own WGS84
    output lands about 10 m off."""
    cur.execute(
        "SELECT ST_Distance("
        "  ST_Transform(ST_SetSRID(ST_MakePoint(-741651.9, -1042827.75), 5514), 3857),"
        "  ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 3857))",
        (FLORENC_LON, FLORENC_LAT),
    )
    assert cur.fetchone()[0] < 1.0


def test_the_render_writes_one_layer_per_key_into_every_covering_tile(cur: Any) -> None:
    section, square_layer, line_layer = _section(cur)
    square = _feature(cur, square_layer, {"class": "A", "name": "Florenc"}, SQUARE)
    line = _feature(cur, line_layer, {"class": "B"}, LINE)
    point = _feature(cur, line_layer, {"class": "station", "label": "Florenc"}, POINT)

    count, tiles = _render(cur, section)

    expected = {(z, *_tile(FLORENC_LON, FLORENC_LAT, z)) for z in range(MINZOOM, MAXZOOM + 1)}
    assert set(tiles) == expected
    assert count == len(tiles)
    for key, mvt in tiles.items():
        assert len(mvt) > 0, key
        assert square_layer.encode() in mvt and line_layer.encode() in mvt, key
        assert _layers(mvt) == {
            square_layer: [(square, 3, {"class": "A", "name": "Florenc"})],
            line_layer: sorted([(line, 2, {"class": "B"}),
                                (point, 1, {"class": "station", "label": "Florenc"})]),
        }, key


def test_a_re_render_replaces_the_tiles_and_an_emptied_section_keeps_none(cur: Any) -> None:
    section, square_layer, line_layer = _section(cur)
    _feature(cur, square_layer, {"class": "A"}, SQUARE)
    _feature(cur, line_layer, {"class": "B"}, LINE)

    count, tiles = _render(cur, section)
    assert count == len(tiles) == MAXZOOM - MINZOOM + 1
    again_count, again = _render(cur, section)
    assert (again_count, set(again)) == (count, set(tiles))
    assert all(_layers(again[key]) == _layers(tiles[key]) for key in tiles)

    cur.execute("DELETE FROM map_overlay_features WHERE layer_key IN (%s, %s)",
                (square_layer, line_layer))
    assert _render(cur, section) == (0, {})


def test_an_unknown_section_is_refused(cur: Any) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="unknown section"):
        cur.execute("SELECT map_overlay_render_section(%s)", (f"ci_{uuid.uuid4().hex}",))


def test_only_the_service_role_may_render(cur: Any) -> None:
    cur.execute(
        "SELECT has_function_privilege('anon', %s, 'EXECUTE'), "
        "       has_function_privilege('authenticated', %s, 'EXECUTE'), "
        "       has_function_privilege('service_role', %s, 'EXECUTE')",
        (RENDER, RENDER, RENDER),
    )
    assert cur.fetchone() == (False, False, True)


@pytest.mark.parametrize("table", TABLES)
def test_the_store_is_dark_to_the_browser_roles(cur: Any, table: str) -> None:
    cur.execute(
        "SELECT c.relrowsecurity, "
        "       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid), "
        "       has_table_privilege('anon', c.oid, 'SELECT'), "
        "       has_table_privilege('authenticated', c.oid, 'SELECT') "
        "FROM pg_class c WHERE c.oid = %s::regclass",
        (f"public.{table}",),
    )
    assert cur.fetchone() == (True, 0, False, False)


# --- the MF rent choropleth: section `rent_map` -----------------------------------------

OBEC, KU, NO_SHAPE, NO_RENT = 990_101, 990_102, 990_103, 990_104


def _boundary(cur: Any, code: int, level: str, name: str, east: int) -> None:
    """A 0.01° square `east` hundredths of a degree east of Florenc."""
    lon = FLORENC_LON + east * 0.01
    cur.execute(
        "INSERT INTO admin_boundaries (id, level, name, geom) VALUES (%s, %s, %s, "
        "ST_Multi(ST_MakeEnvelope(%s, %s, %s, %s, 4326))::geography)",
        (code, level, name, lon, FLORENC_LAT, lon + 0.01, FLORENC_LAT + 0.01),
    )


def _rent_revision(cur: Any, source_date: date,
                   rows: list[tuple[int, str, str | None, int, int | None]]) -> int:
    cur.execute(
        "INSERT INTO rent_map_revisions (source_date, source_filename, file_sha256, row_count) "
        "VALUES (%s, 'ci.xlsx', %s, %s) RETURNING source_revision",
        (source_date, f"ci-{uuid.uuid4().hex}", len(rows)),
    )
    revision = int(cur.fetchone()[0])
    for code, level, kraj, vk, rent in rows:
        cur.execute(
            "INSERT INTO rent_map_values (source_revision, ruian_code, level, kraj, vk, "
            "ref_rent_per_m2) VALUES (%s, %s, %s, %s, %s, %s)",
            (revision, code, level, kraj, vk, rent),
        )
    return revision


def _rent_overlay(cur: Any) -> tuple[Any, ...] | None:
    cur.execute("SELECT version, fetched_at, source_revised_at FROM map_overlay_sections "
                "WHERE key = 'rent_map'")
    return cur.fetchone()


def test_the_rent_map_draws_the_latest_revision_per_priced_territory(cur: Any) -> None:
    """One feature per territory the latest revision prices AND admin_boundaries can draw, at
    the level the MF priced it, its VK rents as flat numeric props (an unpriced VK and a NULL
    kraj stripped); the stale revision, an undrawable code and an unpriced boundary are absent."""
    _boundary(cur, OBEC, "obec", "Obecov", 0)
    _boundary(cur, KU, "ku", "KU Test", 1)
    _boundary(cur, NO_RENT, "obec", "Nikde", 2)
    _rent_revision(cur, date(2026, 5, 15), [(OBEC, "obec", "Kraj Test", 1, 999)])
    _rent_revision(cur, date(2026, 8, 15), [
        (OBEC, "obec", "Kraj Test", 1, 300), (OBEC, "obec", "Kraj Test", 2, 250),
        (OBEC, "obec", "Kraj Test", 3, 220), (OBEC, "obec", "Kraj Test", 4, 180),
        (KU, "ku", None, 2, 412), (KU, "ku", None, 3, None),
        (NO_SHAPE, "obec", "Kraj Test", 1, 310),
    ])

    tiles = overlays.render_sql_section(cur.connection, "rent_map")

    section, layer = overlays.SOURCES["rent_map"], overlays.SOURCES["rent_map"].layers[0]
    obec_props = {"name": "Obecov", "kraj": "Kraj Test", "level": "obec",
                  "vk1": 300, "vk2": 250, "vk3": 220, "vk4": 180}
    ku_props = {"name": "KU Test", "level": "ku", "vk2": 412}
    cur.execute("SELECT id, source_id, props FROM map_overlay_features "
                "WHERE layer_key = 'rent_map' ORDER BY source_id")
    features = cur.fetchall()
    assert [(source_id, props) for _, source_id, props in features] == [
        (str(OBEC), obec_props), (str(KU), ku_props)]
    cur.execute("SELECT label, minzoom, maxzoom, attribution, licence, source_url, note, "
                "source_revised_at, fetched_at IS NOT NULL FROM map_overlay_sections "
                "WHERE key = 'rent_map'")
    assert cur.fetchone() == (section.label, 3, 12, section.attribution, section.licence,
                              section.source_url, section.note, date(2026, 8, 15), True)
    cur.execute("SELECT legend, scale, variants, feature_count FROM map_overlay_layers "
                "WHERE key = 'rent_map'")
    assert cur.fetchone() == ([], layer.scale, list(layer.variants), 2)

    cur.execute("SELECT z, x, y, mvt FROM map_overlay_tiles WHERE section = 'rent_map'")
    rendered = {(z, x, y): bytes(mvt) for z, x, y, mvt in cur.fetchall()}
    assert len(rendered) == tiles
    inside = {(z, *_tile(FLORENC_LON + 0.005, FLORENC_LAT + 0.005, z)) for z in range(5, 13)}
    assert inside <= set(rendered) and {z for z, _, _ in rendered} == set(range(5, 13))
    ids = {source_id: fid for fid, source_id, _ in features}
    for key in inside:
        drawn = {fid: attrs for fid, _, attrs in _layers(rendered[key])["rent_map"]}
        assert drawn[ids[str(OBEC)]] == obec_props, key


def test_a_rent_map_ingest_re_renders_the_overlay_from_its_own_revision(cur: Any) -> None:
    """api/rent_map.py renders the overlay where it refreshed rent_map_choropleth, inside the
    ingest's transaction: the tiles carry the revision being written, before any commit, and
    the ingest completes without the matview, which migration 601 dropped."""
    _boundary(cur, OBEC, "obec", "Obecov", 0)
    parsed = ParsedRentMap(
        values=[RentValue(OBEC, "obec", "Kraj Test", None, "Obecov", 2, 260, 300, None)],
        adjustments=[], source_date=date(2026, 11, 15),
    )
    revision = insert_revision(cur.connection, parsed, source_filename="ci.xlsx",
                               file_sha256=f"ci-{uuid.uuid4().hex}",
                               source_date=date(2026, 11, 15), uploaded_by=None).revision

    assert revision is not None
    cur.execute("SELECT props FROM map_overlay_features WHERE layer_key = 'rent_map'")
    assert cur.fetchall() == [({"name": "Obecov", "kraj": "Kraj Test", "level": "obec",
                                "vk2": 260},)]
    assert _rent_overlay(cur)[2] == date(2026, 11, 15)
    cur.execute("SELECT to_regclass('public.rent_map_choropleth')")
    assert cur.fetchone() == (None,)


def test_a_refused_rent_map_render_keeps_the_revision_and_the_previous_map(cur: Any) -> None:
    """Nothing to draw (no boundary for the priced code) is an empty first map, which the
    shrink guard refuses before writing: the revision still lands, the overlay is untouched,
    and the ingest's transaction stays usable."""
    before = _rent_overlay(cur)
    parsed = ParsedRentMap(
        values=[RentValue(NO_SHAPE, "obec", "Kraj Test", None, "Nikde", 1, 310, None, None)],
        adjustments=[], source_date=date(2026, 11, 15),
    )
    revision = insert_revision(cur.connection, parsed, source_filename="ci.xlsx",
                               file_sha256=f"ci-{uuid.uuid4().hex}",
                               source_date=date(2026, 11, 15), uploaded_by=None).revision

    cur.execute("SELECT count(*) FROM rent_map_values WHERE source_revision = %s", (revision,))
    assert cur.fetchone() == (1,)
    assert _rent_overlay(cur) == before
