"""Reference overlays, executed (migration 600, docs/design/map-overlays.md): the datum control
point the ingest checks before it trusts PostGIS with S-JTSK, `map_overlay_render_section` on a
square, a line and a point (one MVT layer per layer key, feature ids, props as attributes, the
tile range from the Web Mercator formula), and the store's posture. Runs in CI's migrations job
(`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any, Iterator

import psycopg
import pytest

from tests._live_property import REQUIRED_DB, cur  # noqa: F401 -- `cur` is the fixture

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


def _layers(tile: bytes) -> dict[str, list[tuple[int, int, dict[str, str]]]]:
    """Layer name -> [(feature id, geometry type, string attributes)] of an MVT tile."""
    out: dict[str, list[tuple[int, int, dict[str, str]]]] = {}
    for field, layer in _fields(tile):
        assert field == 3, f"a tile holds only layers, got field {field}"
        msg = list(_fields(layer))
        keys = [v.decode() for f, v in msg if f == 3]
        values = [dict(_fields(v))[1].decode() for f, v in msg if f == 4]
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
