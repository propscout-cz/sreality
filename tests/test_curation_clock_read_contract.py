"""The SPA's curation sync owner reads the curation clock through `curation_clock_public` with a PostgREST select
string, `CURATION_CLOCK_COLS` in frontend/src/lib/queries.ts, which `tsc` cannot see (docs/design/curation-sync.md
§ 3.3, W4). A column the view's latest definition lacks answers a PostgREST 400: the owner reads it as a permanent
failure, so every window quietly stops following curation. The list is also exactly the `ClockRow` the owner diffs."""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_location_w3_projection import _columns, _sql

ROOT = Path(__file__).resolve().parents[1]
_CREATES = re.compile(r"create\s+(?:or\s+replace\s+)?view\s+(?:public\.)?curation_clock_public\b", re.I)


def _select_cols() -> list[str]:
    src = (ROOT / "frontend" / "src" / "lib" / "queries.ts").read_text(encoding="utf-8")
    m = re.search(r"\bconst CURATION_CLOCK_COLS\s*=(.*?);\n", src, re.DOTALL)
    assert m, "CURATION_CLOCK_COLS not found in frontend/src/lib/queries.ts"
    joined = "".join(re.findall(r"'([^']*)'", m.group(1)))
    return [c.strip() for c in joined.split(",") if c.strip()]


def _clock_row_fields() -> list[str]:
    src = (ROOT / "frontend" / "src" / "lib" / "curationSync.ts").read_text(encoding="utf-8")
    m = re.search(r"\binterface ClockRow\s*\{(.*?)\}", src, re.DOTALL)
    assert m, "interface ClockRow not found in frontend/src/lib/curationSync.ts"
    return re.findall(r"^\s*([a-z_]+)\??\s*:", m.group(1), re.MULTILINE)


def test_the_clock_read_names_only_columns_the_view_has() -> None:
    latest = [p.name for p in sorted((ROOT / "migrations").glob("*.sql")) if _CREATES.search(_sql(p.name))][-1]
    view = set(_columns(_sql(latest), "curation_clock_public"))
    missing = sorted(set(_select_cols()) - view)
    assert not missing, f"CURATION_CLOCK_COLS names columns curation_clock_public ({latest}) lacks: {missing}"


def test_the_clock_read_selects_exactly_the_clock_row() -> None:
    assert _select_cols() == _clock_row_fields() == ["account_id", "family", "counter", "mine", "age_ms"]


def test_the_clock_read_never_acts() -> None:
    """§ 3.4: the audience is the clock policy's to state (706); `x-act-as` would narrow it to the caller's own rows."""
    src = (ROOT / "frontend" / "src" / "lib" / "queries.ts").read_text(encoding="utf-8")
    m = re.search(r"export const fetchCurationClock\b(.*?)\n};\n", src, re.DOTALL)
    assert m, "fetchCurationClock not found in frontend/src/lib/queries.ts"
    assert "actAs" not in m.group(1) and "x-act-as" not in m.group(1)
