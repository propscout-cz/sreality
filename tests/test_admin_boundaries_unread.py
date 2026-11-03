"""Location W8 (#2362): `admin_boundaries` retires onto the RÚIAN mirror, the one polygon store (ruling Q12b). W8-3
moved every reader (migrations 725-728); W8-4 (migration 729) dropped the table with its view, its loader and that
loader's workflow, `price_stat_localities`, `city_population`, the price-stat entity columns,
`curated_cities.default_radius_m` and the view columns only the old SPA read. Offline over the code, so none comes
back by name, and live over the replayed catalog.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tests._live_property import REQUIRED_DB

ROOT = Path(__file__).resolve().parents[1]
CODE = ("api", "toolkit", "scraper", "scripts", "location_data", "autodedup", "tests", "frontend/src",
        "chrome-extension/src", ".github/workflows")
SUFFIXES = {".py", ".ts", ".tsx", ".yml", ".yaml", ".json", ".sql", ".sh"}
GONE = re.compile(r"\b(admin_boundar\w*|ingest_boundaries|price_stat_localities|city_population|"
                  r"price_stat_growth_shapes)\b")
_SELF = "tests/test_admin_boundaries_unread.py"


def test_no_code_names_a_retired_object() -> None:
    found = sorted(
        f"{rel}:{n}" for top in CODE for path in (ROOT / top).rglob("*")
        if path.suffix in SUFFIXES and "node_modules" not in path.parts
        and (rel := path.relative_to(ROOT).as_posix()) != _SELF
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if GONE.search(line))
    assert not found, ("read the RÚIAN mirror (ruian_unit_render, ruian_place_names_public, obec_population, "
                       "ruian_translation):\n" + "\n".join(found))


@REQUIRED_DB
def test_the_retired_relations_columns_and_readers_are_gone(cur: Any) -> None:
    cur.execute("SELECT to_regclass('public.admin_boundaries'), to_regclass('public.admin_boundaries_public'), "
                "to_regclass('public.price_stat_localities'), to_regclass('public.city_population'), "
                "to_regprocedure('public.price_stat_growth_shapes(bigint)')")
    assert cur.fetchone() == (None,) * 5
    cur.execute("SELECT attrelid::regclass::text || '.' || attname FROM pg_attribute WHERE NOT attisdropped AND ("
                " (attrelid IN ('public.price_stat_observations'::regclass, 'public.price_stat_locality_no_data'::regclass,"
                "   'public.price_stat_city_metrics'::regclass) AND attname IN ('entity_type', 'entity_id'))"
                " OR (attrelid = 'public.curated_cities'::regclass AND attname = 'default_radius_m')"
                " OR (attrelid = 'public.curated_cities_public'::regclass"
                "     AND attname IN ('admin_boundary_id', 'lat', 'lng', 'default_radius_m'))"
                " OR (attrelid = 'public.price_stat_obce_picker_public'::regclass AND attname = 'sreality_id'))")
    assert cur.fetchall() == []
    cur.execute("SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace "
                "AND p.prosrc ~ '\\m(admin_boundar\\w*|price_stat_localities|city_population|default_radius_m)\\M'")
    assert cur.fetchall() == []
