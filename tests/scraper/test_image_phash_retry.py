"""The bounded retry of stored images the download could not hash: which rows a pass takes, what each
outcome writes, and the cap that keeps a poison image from being fetched forever. SQLite plays images +
listings over the real statements; only R2 is faked.
"""

from __future__ import annotations

import io
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

PIL_Image = pytest.importorskip("PIL.Image")

from scraper import image_phash  # noqa: E402
from scraper.image_phash import (  # noqa: E402
    PHASH_MAX_ATTEMPTS,
    PHASH_RETRY_BATCH,
    compute_dhash,
    retry_unhashed,
    select_unhashed,
    to_signed64,
)

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "703_images_phash_attempts.sql"


class _Cur:
    def __init__(self, conn: "_Conn") -> None:
        self._conn = conn
        self._cursor = conn.db.cursor()

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: object) -> None:
        self._cursor.close()

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.threads.add(threading.get_ident())
        self._cursor.execute(re.sub(r"%\((\w+)\)s", r":\1", sql).replace("%s", "?"), params or ())

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()


class _Conn:
    """A psycopg-shaped autocommit connection over SQLite, noting which threads wrote."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.threads: set[int] = set()
        self.db.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, is_active BOOLEAN)")
        self.db.execute(
            "CREATE TABLE images (id INTEGER PRIMARY KEY, listing_id INTEGER, storage_path TEXT, "
            "phash INTEGER, phash_attempts INTEGER NOT NULL DEFAULT 0)"
        )

    def cursor(self) -> _Cur:
        return _Cur(self)

    def row(self, image_id: int) -> tuple[int | None, int]:
        return self.db.execute(
            "SELECT phash, phash_attempts FROM images WHERE id = ?", (image_id,)).fetchone()


class _R2:
    """Serves `objects`; a missing key raises, as R2 does."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.fetched: list[str] = []

    def download_bytes(self, key: str) -> bytes:
        self.fetched.append(key)
        return self.objects[key]


def _jpeg() -> bytes:
    img = PIL_Image.new("RGB", (48, 36))
    img.putdata([((x * 5) % 256, (y * 7) % 256, (x + y) % 256) for y in range(36) for x in range(48)])
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _no_r2() -> _R2:
    raise AssertionError("no work must not build an R2 client")


# --- the selection -----------------------------------------------------------------------


def test_active_listing_images_come_first_then_newest() -> None:
    conn = _Conn()
    conn.db.execute("INSERT INTO listings VALUES (1, TRUE), (2, FALSE), (3, TRUE)")
    conn.db.execute(
        "INSERT INTO images (id, listing_id, storage_path) VALUES "
        "(10, 1, 'a'), (11, 3, 'b'), (90, 2, 'c'), (91, 2, 'd')"
    )
    assert [r[0] for r in select_unhashed(conn, 100)] == [11, 10, 91, 90]


def test_an_image_without_a_listing_row_is_still_selected_last() -> None:
    conn = _Conn()
    conn.db.execute("INSERT INTO listings VALUES (1, TRUE)")
    conn.db.execute(
        "INSERT INTO images (id, listing_id, storage_path) VALUES (10, 1, 'a'), (50, 999, 'b')")
    assert [r[0] for r in select_unhashed(conn, 100)] == [10, 50]


def test_hashed_and_unstored_images_are_not_selected() -> None:
    conn = _Conn()
    conn.db.execute(
        "INSERT INTO images (id, listing_id, storage_path, phash) VALUES "
        "(10, 1, 'a', NULL), (11, 1, 'b', 42), (12, 1, NULL, NULL)"
    )
    assert [r[0] for r in select_unhashed(conn, 100)] == [10]


def test_the_limit_keeps_the_active_priority() -> None:
    conn = _Conn()
    conn.db.execute("INSERT INTO listings VALUES (1, TRUE), (2, FALSE)")
    conn.db.execute(
        "INSERT INTO images (id, listing_id, storage_path) VALUES (10, 1, 'a'), (90, 2, 'b'), (91, 2, 'c')")
    assert [r[0] for r in select_unhashed(conn, 2)] == [10, 91]


def test_a_row_at_the_cap_is_never_selected_again() -> None:
    conn = _Conn()
    conn.db.execute(
        "INSERT INTO images (id, listing_id, storage_path, phash_attempts) VALUES (?, 1, 'a', ?), (?, 1, 'b', ?)",
        (10, PHASH_MAX_ATTEMPTS - 1, 11, PHASH_MAX_ATTEMPTS),
    )
    assert [r[0] for r in select_unhashed(conn, 100)] == [10]


def test_the_migration_builds_the_partial_index_over_the_same_cap() -> None:
    """The planner uses a partial index only when the query's predicate implies the index's, so the
    constant, the statement and the index must name one number."""
    predicate = f"phash is null and storage_path is not null and phash_attempts < {PHASH_MAX_ATTEMPTS}"
    index = " ".join(MIGRATION.read_text().lower().split())
    assert f"on public.images (id) where {predicate};" in index
    assert predicate in " ".join(image_phash._SELECT_UNHASHED_SQL.lower().replace("i.", "").split())


# --- one pass ----------------------------------------------------------------------------


def _stored(conn: _Conn, *ids: int, attempts: int = 0) -> None:
    conn.db.executemany(
        "INSERT INTO images (id, listing_id, storage_path, phash_attempts) VALUES (?, 1, ?, ?)",
        [(i, f"k/{i}", attempts) for i in ids],
    )


def test_no_work_builds_no_r2_client_and_counts_nothing() -> None:
    assert retry_unhashed(_Conn(), _no_r2) == {"phash_retried": 0, "phash_hashed": 0, "phash_given_up": 0}


def test_a_hash_is_written_and_the_row_leaves_the_batch() -> None:
    conn = _Conn()
    _stored(conn, 10)
    data = _jpeg()

    out = retry_unhashed(conn, lambda: _R2({"k/10": data}))

    assert out == {"phash_retried": 1, "phash_hashed": 1, "phash_given_up": 0}
    assert conn.row(10) == (to_signed64(compute_dhash(data)), 0)
    assert select_unhashed(conn, 100) == []


@pytest.mark.parametrize("objects", [{}, {"k/10": b"\xff\xd8\xff\xe0" + b"\x00" * 64}],
                         ids=["download_fails", "bytes_undecodable"])
def test_a_failure_counts_one_attempt_and_leaves_the_hash_null(objects: dict[str, bytes]) -> None:
    conn = _Conn()
    _stored(conn, 10)

    out = retry_unhashed(conn, lambda: _R2(objects))

    assert out == {"phash_retried": 1, "phash_hashed": 0, "phash_given_up": 0}
    assert conn.row(10) == (None, 1)


def test_a_poison_row_is_fetched_exactly_the_cap_times_then_never_again(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="scraper.image_phash")
    conn = _Conn()
    _stored(conn, 10)
    r2 = _R2()

    totals = [retry_unhashed(conn, lambda: r2) for _ in range(PHASH_MAX_ATTEMPTS + 2)]

    assert r2.fetched == ["k/10"] * PHASH_MAX_ATTEMPTS
    assert conn.row(10) == (None, PHASH_MAX_ATTEMPTS)
    assert [t["phash_given_up"] for t in totals] == [0] * (PHASH_MAX_ATTEMPTS - 1) + [1, 0, 0]
    gave_up = [r.getMessage() for r in caplog.records]
    assert len(gave_up) == 1 and "id=10" in gave_up[0]


def test_one_bad_image_does_not_stop_the_rest_of_the_batch() -> None:
    conn = _Conn()
    _stored(conn, 10, 11, 12)
    data = _jpeg()

    out = retry_unhashed(conn, lambda: _R2({"k/10": data, "k/12": data}))

    assert out == {"phash_retried": 3, "phash_hashed": 2, "phash_given_up": 0}
    assert conn.row(11) == (None, 1)


def test_a_pass_takes_at_most_one_batch() -> None:
    conn = _Conn()
    _stored(conn, *range(1, PHASH_RETRY_BATCH + 6))
    r2 = _R2()

    out = retry_unhashed(conn, lambda: r2)

    assert out["phash_retried"] == len(r2.fetched) == PHASH_RETRY_BATCH


def test_every_database_statement_stays_on_the_calling_thread() -> None:
    conn = _Conn()
    _stored(conn, *range(1, 21))
    retry_unhashed(conn, lambda: _R2())
    assert conn.threads == {threading.get_ident()}
