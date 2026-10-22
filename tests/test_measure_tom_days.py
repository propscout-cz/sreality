"""One time-on-market, one definition (migration 617): offline, every view that publishes
`tom_days` calls the function and no Python module spells the arithmetic; on the replayed schema
(CI, `TEST_DATABASE_URL`) the function answers its documented cases, inlines, and `listings_public`
and the Python emitter return what it returns for the same row."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests._live_property import REQUIRED_DB, cur  # noqa: F401 -- `cur` is the fixture
from toolkit.measures import tom_days_sql, tom_observed_sql

_ROOT = Path(__file__).resolve().parents[1]
# 617 restated a fourth, listing_feed_public; migration 618 (merge sprint W6) dropped it.
_VIEWS = ("properties_public", "listings_public", "browse_projection")


def _effective_definition(view: str) -> str:
    pattern = re.compile(
        rf"create\s+(or\s+replace\s+)?view\s+(public\.)?{view}\b(.*?);\n", re.I | re.S,
    )
    last = ""
    for path in sorted((_ROOT / "migrations").glob("[0-9]*.sql")):
        for m in pattern.finditer(path.read_text()):
            last = m.group(0)
    assert last, f"no migration defines {view}"
    return re.sub(r"--[^\n]*", "", last)


@pytest.mark.parametrize("view", _VIEWS)
def test_a_view_publishes_tom_days_by_calling_the_measure(view: str) -> None:
    body = _effective_definition(view)
    assert re.search(r"measure_tom_days\(\s*\w+\.is_active|measure_tom_days\(\s*is_active", body), view
    assert "86400" not in body, f"{view} spells time on market inline again"


_SQL_EPOCH_SPELLING = re.compile(r"epoch\s+from[\s\S]{0,120}?first_seen_at[\s\S]{0,60}?86400", re.I)
_PYTHON_DAYS_SPELLING = re.compile(r"\bfirst_seen\w*\s*\)?\s*\.days\b")


def test_no_python_module_spells_the_tom_arithmetic() -> None:
    """Two spellings the old copies used: SQL `epoch ... first_seen_at ... 86400` and
    `(now - first_seen).days`; a bare timedelta subtraction is invisible to this census."""
    offenders = []
    for tree in ("scraper", "toolkit", "api", "scripts"):
        for path in (_ROOT / tree).rglob("*.py"):
            if path.name == "measures.py":
                continue
            text = path.read_text()
            if _SQL_EPOCH_SPELLING.search(text) or _PYTHON_DAYS_SPELLING.search(text):
                offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == [], f"time on market is re-spelled in {offenders}; call tom_days_sql"


@REQUIRED_DB
@pytest.mark.parametrize(
    ("active", "first", "last", "want"),
    [
        (True, "10 days 12 hours", "0 seconds", 10),      # open run: age so far, floored
        (True, "0 seconds", "0 seconds", 0),
        (False, "30 days", "20 days", 10),                # closed run: first -> last sighting
        (False, "30 days", "29 days 1 hour", 0),          # under a day floors to zero
        (False, "20 days", "30 days", 0),                 # last before first clamps, never negative
        (None, "30 days", "20 days", 10),                 # unknown status reads as closed
    ],
)
def test_the_measure_answers_the_documented_cases(
    cur: Any, active: bool | None, first: str, last: str, want: int,
) -> None:
    cur.execute(
        "SELECT measure_tom_days(%s, now() - %s::interval, now() - %s::interval)",
        (active, first, last),
    )
    assert cur.fetchone()[0] == want


@REQUIRED_DB
@pytest.mark.parametrize("active", [True, False])
def test_the_view_and_the_python_emitter_agree_with_the_function(cur: Any, active: bool) -> None:
    from tests._live_property import _advert, _property

    pid = _property(cur)
    lid = _advert(cur, pid, source="sreality", active=active)
    cur.execute(
        "UPDATE listings SET first_seen_at = now() - interval '12 days 5 hours', "
        "last_seen_at = now() - interval '4 days' WHERE id = %s",
        (lid,),
    )
    cur.execute(
        "SELECT measure_tom_days(is_active, first_seen_at, last_seen_at) FROM listings WHERE id = %s",
        (lid,),
    )
    want = cur.fetchone()[0]
    assert want == (12 if active else 8)

    cur.execute("SELECT tom_days FROM listings_public WHERE id = %s", (lid,))
    assert cur.fetchone()[0] == want

    cur.execute(f"SELECT {tom_days_sql('l')} FROM listings l WHERE l.id = %s", (lid,))
    assert cur.fetchone()[0] == want


@REQUIRED_DB
def test_the_measure_inlines(cur: Any) -> None:
    cur.execute(
        "EXPLAIN (VERBOSE, COSTS OFF) "
        "SELECT measure_tom_days(is_active, first_seen_at, last_seen_at) FROM listings"
    )
    plan = "\n".join(r[0] for r in cur.fetchall())
    assert "measure_tom_days" not in plan, plan


# --- "observed from the start" (migration 625) --------------------------------------------

def test_the_projection_publishes_tom_observed_through_the_measure() -> None:
    body = _effective_definition("browse_projection")
    assert re.search(r"measure_tom_observed\(k\.source, k\.category_main, k\.category_type, k\.first_seen_at\)", body)
    assert "as tom_observed" in body


def test_the_only_filter_narrows_when_true_and_is_silent_otherwise() -> None:
    from toolkit.filter_compiler import LISTINGS_GRAIN, compile_filter_where
    from toolkit.measures import tom_observed_sql

    assert compile_filter_where({"tom_observed_only": True}, LISTINGS_GRAIN) == (
        [tom_observed_sql("l")], {})
    for off in (False, None):
        assert compile_filter_where({"tom_observed_only": off}, LISTINGS_GRAIN) == ([], {})


@REQUIRED_DB
def test_sweep_days_are_the_first_day_and_the_outliers(cur: Any) -> None:
    """Seed one (portal, category) pair over three UTC days: the first day is a sweep by
    definition, the next lone sighting is ordinary, and a 150-ad day beats greatest(100, 4 x the
    pair's median) so it is a sweep too. Refreshed inside the rolled-back transaction."""
    from tests._live_property import _advert, _property

    source, main, kind = "maxima", "ostatni", "drazba"
    cur.execute(
        "SELECT count(*) FROM listings WHERE source = %s AND category_main = %s AND category_type = %s",
        (source, main, kind),
    )
    assert cur.fetchone()[0] == 0, "the seeded pair must be empty for the medians to be known"

    def seed(day: str, n: int) -> list[int]:
        ids = []
        for _ in range(n):
            lid = _advert(cur, _property(cur), source=source)
            cur.execute(
                "UPDATE listings SET category_main = %s, category_type = %s, "
                "first_seen_at = %s::timestamptz + interval '12 hours' WHERE id = %s",
                (main, kind, day, lid),
            )
            ids.append(lid)
        return ids

    first_day, ordinary, sweep = seed("2026-03-01", 2), seed("2026-03-02", 1), seed("2026-03-03", 150)
    cur.execute("REFRESH MATERIALIZED VIEW public.ingest_bulk_days")
    cur.execute(
        "SELECT day::text, is_first_day, sightings FROM ingest_bulk_days "
        "WHERE source = %s AND category_main = %s AND category_type = %s ORDER BY day",
        (source, main, kind),
    )
    assert cur.fetchall() == [("2026-03-01", True, 2), ("2026-03-03", False, 150)]

    def observed(lid: int) -> bool:
        cur.execute(
            "SELECT measure_tom_observed(source, category_main, category_type, first_seen_at) "
            "FROM listings WHERE id = %s", (lid,),
        )
        return cur.fetchone()[0]

    assert observed(ordinary[0]) is True
    assert observed(first_day[0]) is False
    assert observed(sweep[0]) is False

    cur.execute(f"SELECT {tom_observed_sql('l')} FROM listings l WHERE l.id = %s", (ordinary[0],))
    assert cur.fetchone()[0] is True
