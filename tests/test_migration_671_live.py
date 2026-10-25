"""Migration 671, EXECUTED over seeded hits and misses (the 573 pattern).

The replay runs 671 on an empty schema, which proves only that it compiles. Here one row
per shape the file must clear and the misses it must keep are seeded, the whole file is
run, and each row is read back: the NULL, the backup row carrying the old plot, the rail
and the untouched basis, the property queued for maintenance -- and nothing for a miss. A
second run changes nothing. Runs in CI's migrations job (`TEST_DATABASE_URL`). The file
brackets itself in BEGIN / COMMIT; the test runs its body inside the test's own transaction
instead, so every test rolls back.
"""

from __future__ import annotations

import itertools
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scraper.area import stated_plot

_DB_URL = os.environ.get("TEST_DATABASE_URL")

_needs_db = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set -- schema-replay test runs only in the CI DB job",
)

_SQL = (Path(__file__).resolve().parent.parent / "migrations"
        / "671_plot_column_echo_heal.sql").read_text(encoding="utf-8")
# The file's own transaction control, lifted so the test's transaction holds the whole run.
_RUN = _SQL.replace("\nbegin;\n", "\n", 1).replace("\ncommit;\n", "\n", 1)
_SREALITY_IDS = itertools.count(9_671_000_001)
_NBSP = chr(0x00A0)

# (label, source, category_main, estate_area, usable_area, area_m2, area_basis, is_active,
#  description, rail). rail None = a miss the file must keep.
_ROWS: tuple[tuple[Any, ...], ...] = (
    # P1: a flat never carries a plot -- whatever the cell holds.
    ("p1 flat echo", "ceskereality", "byt", 34.0, 34.0, 34.0, "usable", True, None, "P1"),
    ("p1 flat near-floor (b1)", "ceskereality", "byt", 64.0, 59.0, 59.0, "usable", True, None,
     "P1"),
    ("p1 flat placeholder", "ceskereality", "byt", 1.0, 45.0, 45.0, "usable", True, None, "P1"),
    ("p1 flat building parcel, inactive", "ceskereality", "byt", 3470.0, 71.0, 71.0,
     "usable", False, None, "P1"),
    ("p1 flat idnes", "idnes", "byt", 50.0, 50.0, 50.0, "usable", True, None, "P1"),
    ("p1 flat no basis, inactive", "bezrealitky", "byt", 40.0, None, None, None, False, None,
     "P1"),
    # P2: a komerční plot that is the floor figure.
    ("p2 usable echo", "idnes", "komercni", 10.0, 10.0, 10.0, "usable", True, None, "P2"),
    ("p2 total echo", "realitymix", "komercni", 852.0, None, 852.0, "total", True, None, "P2"),
    ("p2 floor echo, inactive", "remax", "komercni", 72.0, None, 72.0, "floor", False, None, "P2"),
    ("p2 pre-stamp usable echo, inactive", "idnes", "komercni", 781.0, 781.0, 781.0, None,
     False, None, "P2"),
    ("p2 echo at column scale", "idnes", "komercni", 100.04, 100.0, 100.0, "usable", True, None,
     "P2"),
    # misses: the ruling's own exclusions
    ("miss title fallback", "ceskereality", "komercni", 3400.0, None, 3400.0, "unknown",
     True, None, None),
    ("miss real parcel", "ceskereality", "komercni", 3400.0, 1200.0, 1200.0, "usable",
     True, None, None),
    ("miss parcel = built-up, not usable (945419)", "mmreality", "komercni", 327.0, 960.0,
     960.0, "usable", True, None, None),
    ("miss near-echo", "idnes", "komercni", 12.6, 10.0, 10.0, "usable", True, None, None),
    # the named under-heal: a pre-stamp realitymix "Plocha" shape (usable NULL, basis NULL)
    ("miss pre-stamp total shape, inactive", "realitymix", "komercni", 40.0, None, 40.0,
     None, False, None, None),
    ("miss house echo (dum rule unchanged)", "idnes", "dum", 92.0, 92.0, 92.0, "usable",
     True, None, None),
    ("miss house footprint", "idnes", "dum", 80.0, 210.0, 210.0, "usable", True, None, None),
    ("miss land", "sreality", "pozemek", 1400.0, None, 1400.0, "plot", True, None, None),
    ("miss ostatni", "bazos", "ostatni", 30.0, 30.0, 30.0, "unknown", True, None, None),
    ("miss komerční without a plot", "idnes", "komercni", None, 120.0, 120.0, "usable",
     True, None, None),
    # The advert's own text (W22, 2026-10-25): equality alone is not evidence.
    ("p2 text names a different parcel (c)", "idnes", "komercni", 495.0, 495.0, 495.0,
     "usable", True,
     "Komerční objekt s užitnou plochou 495 m² na pozemku o výměře 1 484 m².", "P2"),
    ("p2 no parcel text (d)", "realitymix", "komercni", 18.0, 18.0, 18.0, "usable", True,
     "Pronájem skladu 18 m² v přízemí, vlastní vchod.", "P2"),
    ("p2 a different parcel outranks a smaller built-up area", "ceskereality", "komercni",
     250.0, 250.0, 250.0, "usable", False,
     "Zastavěná plocha 166 m², pozemek 865 m². Dvě podlaží.", "P2"),
    ("p2 a parcel past a sentence end is not read", "idnes", "komercni", 200.0, 200.0, 200.0,
     "usable", True, "Dům stojí na pozemku. Za domem je dvorek cca 200 m².", "P2"),
    ("miss text states the same parcel (a, LPG)", "idnes", "komercni", 994.0, 994.0, 994.0,
     "usable", True, "Čerpací stanice LPG. Celková výměra pozemku činí 994m².", None),
    ("miss text states the parcel in ha (a, farm)", "ceskereality", "komercni", 164000.0,
     None, 164000.0, "total", True,
     "Jezdecký areál se stájemi na pozemcích o výměře 16,4 ha.", None),
    ("miss dotted thousands, NBSP and caps (a)", "realitymix", "komercni", 3123.0, 3123.0,
     3123.0, "usable", False, "Pozemek pod stavbou 410 m2; POZEMEK celkem 3.123" + _NBSP
     + "m2", None),
    ("miss built-up smaller, no parcel (b, areál)", "mmreality", "komercni", 30000.0,
     30000.0, 30000.0, "usable", True,
     "Areál o rozloze 30 000 m². Zastavěná plocha činí 5" + _NBSP + "012 m².", None),
)


@pytest.fixture()
def cur() -> Iterator[Any]:
    import psycopg

    conn = psycopg.connect(
        _DB_URL,
        options="-c statement_timeout=60000 -c lock_timeout=5000"
        " -c idle_in_transaction_session_timeout=60000",
    )
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def _seed(cur: Any, row: tuple[Any, ...]) -> tuple[int, int]:
    _, source, category, plot, usable, headline, basis, active, description, _ = row
    cur.execute("INSERT INTO properties DEFAULT VALUES RETURNING id")
    pid = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO listings (sreality_id, source, source_id_native, raw_json, category_main, "
        "category_type, price_czk, area_m2, area_basis, usable_area, estate_area, is_active, "
        "description, property_id) VALUES (%s, %s, %s, '{}'::jsonb, %s, 'prodej', 5000000, %s, "
        "%s, %s, %s, %s, %s, %s) RETURNING id",
        (next(_SREALITY_IDS) if source == "sreality" else None, source,
         f"m671-{uuid.uuid4()}", category, headline, basis, usable, plot, active, description,
         pid),
    )
    return int(cur.fetchone()[0]), pid


def _python_declines(row: tuple[Any, ...]) -> bool:
    """The parser rule's verdict on the same stored cells -- the equivalence 671 claims. A
    stored row is read as the rule would read a fresh parse of it: the plot, the usable
    measure, the headline and its basis stamp (NULL where the row predates the stamp), and
    the description it reads as evidence."""
    _, _, category, plot, usable, headline, basis, _, description, _ = row
    return plot is not None and stated_plot(
        category, plot, usable=usable, headline=headline, headline_basis=basis,
        text=description) is None


def _read(cur: Any, listing_id: int) -> tuple[Any, Any]:
    cur.execute("SELECT estate_area, area_basis FROM listings WHERE id = %s", (listing_id,))
    return cur.fetchone()


def _backup(cur: Any, listing_id: int) -> tuple[Any, ...] | None:
    cur.execute("SELECT rail, estate_area, area_basis FROM backup_671.listing_plots "
                "WHERE listing_id = %s", (listing_id,))
    return cur.fetchone()


def _queued(cur: Any, pid: int) -> bool:
    cur.execute("SELECT 1 FROM dirty_properties WHERE property_id = %s", (pid,))
    return cur.fetchone() is not None


def test_the_file_brackets_one_transaction_the_test_can_lift() -> None:
    assert _SQL.count("\nbegin;\n") == 1 and _SQL.count("\ncommit;\n") == 1
    assert "begin;" not in _RUN and "commit;" not in _RUN


def test_every_seeded_row_matches_the_parser_rule() -> None:
    for row in _ROWS:
        assert _python_declines(row) == (row[-1] is not None), row[0]


@_needs_db
def test_the_file_clears_exactly_the_hits_backs_them_up_and_queues_them(cur: Any) -> None:
    seeded = [(row, *_seed(cur, row)) for row in _ROWS]
    cur.execute("DELETE FROM dirty_properties WHERE property_id = ANY(%s)",
                ([pid for _, _, pid in seeded],))

    cur.execute(_RUN)

    for row, listing_id, pid in seeded:
        label, _, _, plot, _, _, basis, _, _, rail = row
        stored, stored_basis = _read(cur, listing_id)
        backup = _backup(cur, listing_id)
        assert stored_basis == basis, label          # area_basis is never touched
        if rail is None:
            assert (None if stored is None else float(stored)) == (
                None if plot is None else round(plot, 1)), label
            assert backup is None, label
            assert not _queued(cur, pid), label
            continue
        assert stored is None, label
        assert backup is not None, label
        assert backup[0] == rail, label
        assert float(backup[1]) == round(plot, 1), label   # the column's own scale
        assert backup[2] == basis, label
        assert _queued(cur, pid), label


@_needs_db
def test_a_second_run_changes_nothing(cur: Any) -> None:
    seeded = [(row, *_seed(cur, row)) for row in _ROWS]
    ids = [listing_id for _, listing_id, _ in seeded]
    cur.execute(_RUN)
    cur.execute("SELECT count(*) FROM backup_671.listing_plots WHERE listing_id = ANY(%s)", (ids,))
    first = cur.fetchone()[0]
    assert first == sum(1 for row in _ROWS if row[-1] is not None)

    cur.execute(_RUN)
    cur.execute("SELECT count(*) FROM backup_671.listing_plots WHERE listing_id = ANY(%s)", (ids,))
    assert cur.fetchone()[0] == first
    for row, listing_id, _ in seeded:
        if row[-1] is None and row[3] is not None:
            assert float(_read(cur, listing_id)[0]) == round(row[3], 1), row[0]


@_needs_db
def test_the_restore_in_the_header_puts_the_plot_back_where_it_is_still_null(cur: Any) -> None:
    row = _ROWS[0]
    listing_id, _ = _seed(cur, row)
    cur.execute(_RUN)
    assert _read(cur, listing_id)[0] is None
    header = _SQL[:_SQL.index("set lock_timeout")]
    restore = header[header.index("--   with restored as ("):header.index("-- The backup goes")]
    statement = "\n".join(line.removeprefix("--").strip() for line in restore.splitlines())
    cur.execute(statement)
    assert float(_read(cur, listing_id)[0]) == row[3]
