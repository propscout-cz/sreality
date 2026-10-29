"""Rule #3 made total, executed: every active ad of a walking portal is in exactly ONE nomination scope.

A sighting is label-blind and a unit's nomination is not, so an ad whose label no walked unit covers was
never checked once it left the index (703 such ads on 2026-10-22, 430 of them unclosable since June:
ceskereality's /drazby/ auctions and realitymix's chaty with a NULL label). The runner now nominates them
as orphans once a walk cycle reached the end over every scope. This rail builds each portal as its walk does, from the
replayed schema's `portals` row, takes each unit's scope from the runner (`portal_runner._scope`), seeds one
active ad per label an ad can carry (every canonical main x type, the scopes' own subtypes, NULL in each
column) and runs the real nomination queries: each unit scope's `db.presence_candidates`, then
`db.orphan_candidates`. An ad no query returns is the class coming back; an ad two return is a double
nomination under two flip-cap descriptors. The cycle's ledger read (`db.walk_ledger`) runs here too: it is
what decides the orphans' anchor. Runs in CI's migrations lane (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`);
skipped locally.
"""

from __future__ import annotations

import itertools
import os
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from psycopg.types.json import Jsonb

from scraper import db, parser, portal_factory, portal_runner
from scraper.portal import load_portal_config

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

@pytest.fixture()
def conn() -> Iterator[Any]:
    """One connection in one transaction, always rolled back."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
                    "misconfigured and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _portal_scopes(conn: Any, source: str) -> tuple[Any, list[dict[str, Any]]]:
    portal = portal_factory.build_portal(source, load_portal_config(conn, source))
    found = (portal_runner._scope(portal, c) for c in portal.categories())
    return portal, [dict(k) for k in {portal_runner._scope_key(s) for s in found if s is not None}]


def _labels(scopes: list[dict[str, Any]]) -> list[tuple[str | None, str | None, str | None]]:
    subtypes = {s["subtype"] for s in scopes if s.get("subtype") is not None} | {"rail-other", None}
    mains = [*parser.CATEGORY_MAIN.values(), None]
    types = [*parser.CATEGORY_TYPE.values(), None]
    return list(itertools.product(mains, types, sorted(subtypes, key=str)))


@pytest.mark.parametrize("source", sorted(portal_factory.PORTAL_CLASSES))
def test_every_active_ad_is_in_exactly_one_nomination_scope(conn: Any, source: str) -> None:
    portal, scopes = _portal_scopes(conn, source)
    # The walk's own key: sreality excludes its seen set on the integer sreality_id it stores.
    seen_key = getattr(portal, "seen_key", "native")
    by_id = seen_key == "sreality_id"
    seeded = {}
    for i, (main, kind, subtype) in enumerate(_labels(scopes)):
        native = str(900_000_000 + i)
        seeded[native] = (main, kind, subtype)
        conn.execute(
            "insert into listings (source, source_id_native, sreality_id, raw_json, category_main, "
            "category_type, subtype, is_active, last_seen_at) "
            "values (%s, %s, %s, '{}'::jsonb, %s, %s, %s, true, now() - interval '3 days')",
            (source, native, int(native) if by_id else None, main, kind, subtype))
    unseen = {0} if by_id else {"rail-not-an-ad"}
    nominated: Counter[str] = Counter()
    for scope in scopes:
        candidates, _ = db.presence_candidates(conn, source, scope, unseen, seen_key=seen_key)
        nominated.update(n for n, _ref, _price in candidates if n in seeded)
    orphans, _ = db.orphan_candidates(conn, source, scopes, datetime.now(timezone.utc), seen_key=seen_key)
    nominated.update(n for n, _ref, _price in orphans if n in seeded)
    wrong = {seeded[n]: nominated[n] for n in seeded if nominated[n] != 1}
    assert not wrong, (
        f"{source}: (category_main, category_type, subtype) -> times nominated, expected once each "
        f"(scopes {scopes}): {dict(sorted(wrong.items(), key=str))}")


def test_the_cycle_ledger_reads_back_what_each_walk_recorded(conn: Any) -> None:
    """Each other index run of the portal in the window, oldest first, with each entry's scope, verdict and count;
    an entry from before the cycle (no scope) left out, the run's own start read from its row."""
    anchor = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)
    scope = {"category_main": "byt", "category_type": "prodej"}
    flags = {"total_is_count": False, "upper_bound": False}
    unit = portal_runner._entry("byt", "prodej", scope=scope, cycle=anchor, declared=3, collected=2,
                                reached=True, **flags)
    loose = portal_runner._entry(None, None, scope=None, cycle=anchor, declared=0, collected=0,
                                 reached=False, **flags)
    ended = portal_runner._entry(None, None, scope={}, cycle=anchor, declared=0, collected=0,
                                 reached=True, **flags)
    old = {k: v for k, v in unit.items() if k not in ("scope", "cycle_started_at")}

    def run(source: str, run_type: str, ago: timedelta, entries: list[dict[str, Any]]) -> int:
        row = conn.execute(
            "insert into scrape_runs (source, run_type, started_at, by_category) values (%s, %s, now() - %s, %s) "
            "returning id", (source, run_type, ago, Jsonb(entries))).fetchone()
        return int(row[0])

    run("idnes", "index", timedelta(days=20), [unit])                    # outside the window
    run("idnes", "index", timedelta(hours=7), [old])                      # from before the cycle
    run("idnes", "index", timedelta(hours=6), [unit, loose, ended])
    run("idnes", "detail", timedelta(hours=5), [unit])                    # a drain, not a walk
    run("bazos", "index", timedelta(hours=5), [unit])                     # another portal
    run("idnes", "index", timedelta(hours=1), [unit])
    own = run("idnes", "index", timedelta(minutes=1), [unit])
    start, runs = db.walk_ledger(conn, "idnes", own, 14)
    assert start == conn.execute("select started_at from scrape_runs where id = %s", (own,)).fetchone()[0]
    got = [sorted((e["scope"] is None, str(e["scope"]), e["reached"], e["collected"]) for e in entries)
           for _started, entries in runs]
    assert got == [
        [],
        sorted([(False, str(scope), True, 2), (True, "None", False, 0), (False, "{}", True, 0)]),
        [(False, str(scope), True, 2)],
    ]
    assert [started for started, _ in runs] == sorted(started for started, _ in runs)
    assert db.walk_ledger(conn, "idnes", None, 14)[0] == conn.execute("select now()").fetchone()[0]
