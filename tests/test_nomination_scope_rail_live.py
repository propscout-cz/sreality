"""Rule #3 made total, executed: every active ad of a walking portal is in exactly ONE nomination scope.

A sighting is label-blind and a unit's nomination is not, so an ad whose label no walked unit covers was
never checked once it left the index (703 such ads on 2026-10-22, 430 of them unclosable since June:
ceskereality's /drazby/ auctions and realitymix's chaty with a NULL label). The runner now nominates them
as orphans once every unit reached the end. This rail builds each portal as its walk does, from the
replayed schema's `portals` row, takes each unit's scope from the runner (`portal_runner._scope`), seeds one
active ad per label an ad can carry (every canonical main x type, the scopes' own subtypes, NULL in each
column) and runs the real nomination queries: each unit scope's `db.presence_candidates`, then
`db.orphan_candidates`. An ad no query returns is the class coming back; an ad two return is a double
nomination under two flip-cap descriptors. Runs in CI's migrations lane (`TEST_DATABASE_URL`,
`DB_RAILS_REQUIRED=1`); skipped locally.
"""

from __future__ import annotations

import itertools
import os
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest

from scraper import db, parser, portal_factory, portal_runner
from scraper.portal import load_portal_config

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)

_UNSEEN = {"rail-not-an-ad"}


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


def _scopes(conn: Any, source: str) -> list[dict[str, Any]]:
    portal = portal_factory.build_portal(source, load_portal_config(conn, source))
    found = (portal_runner._scope(portal, c) for c in portal.categories())
    return [dict(k) for k in {portal_runner._scope_key(s) for s in found if s is not None}]


def _labels(scopes: list[dict[str, Any]]) -> list[tuple[str | None, str | None, str | None]]:
    subtypes = {s["subtype"] for s in scopes if s.get("subtype") is not None} | {"rail-other", None}
    mains = [*parser.CATEGORY_MAIN.values(), None]
    types = [*parser.CATEGORY_TYPE.values(), None]
    return list(itertools.product(mains, types, sorted(subtypes, key=str)))


@pytest.mark.parametrize("source", sorted(portal_factory.PORTAL_CLASSES))
def test_every_active_ad_is_in_exactly_one_nomination_scope(conn: Any, source: str) -> None:
    scopes = _scopes(conn, source)
    seeded = {}
    for i, (main, kind, subtype) in enumerate(_labels(scopes)):
        native = f"rail-{i}"
        seeded[native] = (main, kind, subtype)
        conn.execute(
            "insert into listings (source, source_id_native, sreality_id, raw_json, category_main, "
            "category_type, subtype, is_active, last_seen_at) "
            "values (%s, %s, %s, '{}'::jsonb, %s, %s, %s, true, now() - interval '3 days')",
            (source, native, 900_000_000 + i if source == "sreality" else None, main, kind, subtype))
    nominated: Counter[str] = Counter()
    for scope in scopes:
        candidates, _ = db.presence_candidates(conn, source, scope, _UNSEEN)
        nominated.update(n for n, _ref, _price in candidates if n in seeded)
    orphans, _ = db.orphan_candidates(conn, source, scopes, _UNSEEN)
    nominated.update(n for n, _ref, _price in orphans if n in seeded)
    wrong = {seeded[n]: nominated[n] for n in seeded if nominated[n] != 1}
    assert not wrong, (
        f"{source}: (category_main, category_type, subtype) -> times nominated, expected once each "
        f"(scopes {scopes}): {dict(sorted(wrong.items(), key=str))}")
