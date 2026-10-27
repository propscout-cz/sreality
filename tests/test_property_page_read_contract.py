"""The SPA's property page (decision 11) reads `properties_public` through a PostgREST
select string, `PROPERTY_COLS` in frontend/src/lib/queries.ts, which `tsc` cannot see —
and its ads through `property_sources_public` with `PROPERTY_SOURCE_COLS` the same way.
Every column either names must be a column of that view's latest definition, or the page
answers a PostgREST 400 in production."""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_location_w3_projection import _columns, _sql

ROOT = Path(__file__).resolve().parents[1]
_CREATES = re.compile(r"create\s+(?:or\s+replace\s+)?view\s+(?:public\.)?properties_public\b", re.I)


_CREATES_SOURCES = re.compile(
    r"create\s+(?:or\s+replace\s+)?view\s+(?:public\.)?property_sources_public\b", re.I)


def _select_cols(const: str) -> list[str]:
    src = (ROOT / "frontend" / "src" / "lib" / "queries.ts").read_text(encoding="utf-8")
    m = re.search(rf"\bconst {const}\s*=(.*?);\n", src, re.DOTALL)
    assert m, f"{const} not found in frontend/src/lib/queries.ts"
    joined = "".join(re.findall(r"'([^']*)'", m.group(1)))
    return [c.split(":")[-1] for c in joined.split(",") if c]  # `alias:column`


def _latest_view(creates: re.Pattern[str], view: str) -> tuple[str, set[str]]:
    latest = [p.name for p in sorted((ROOT / "migrations").glob("*.sql"))
              if creates.search(_sql(p.name))][-1]
    return latest, set(_columns(_sql(latest), view))


def test_the_property_page_reads_only_columns_the_view_has() -> None:
    latest, view = _latest_view(_CREATES, "properties_public")
    missing = sorted(set(_select_cols("PROPERTY_COLS")) - view)
    assert not missing, f"PROPERTY_COLS names columns properties_public ({latest}) lacks: {missing}"


def test_the_ads_rows_read_only_columns_the_view_has() -> None:
    latest, view = _latest_view(_CREATES_SOURCES, "property_sources_public")
    missing = sorted(set(_select_cols("PROPERTY_SOURCE_COLS")) - view)
    assert not missing, (
        f"PROPERTY_SOURCE_COLS names columns property_sources_public ({latest}) lacks: {missing}")
