"""Rails for the image provenance write path (migration 496), hermetic (a
hand-rolled fake connection, no DB): `mark_image_stored` writes provenance only
when it was measured and is byte-identical to its pre-496 form otherwise."""

from __future__ import annotations

import re
from typing import Any

from scraper import db as scraper_db


class _RecordingCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _RecordingConn:
    """Records every statement; `conn.transaction()` is a no-op context."""

    def __init__(self) -> None:
        self.cursor_obj = _RecordingCursor()

    def cursor(self) -> _RecordingCursor:
        return self.cursor_obj

    def transaction(self) -> "_RecordingConn":
        return self

    def __enter__(self) -> "_RecordingConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


_SET_COL_RE = re.compile(r"^\s*([a-z_]+)\s*=", re.MULTILINE)


def _set_columns(sql: str) -> set[str]:
    """Column names on the left of `=` inside an UPDATE's SET list."""
    body = sql.split("SET", 1)[1].split("WHERE", 1)[0]
    return set(_SET_COL_RE.findall(body))


# ---- mark_image_stored backwards-compat -------------------------------------


_PRE_496_SQL = """
            UPDATE images
            SET storage_path = %s,
                phash = COALESCE(%s, phash),
                last_download_attempt_at = now(),
                download_attempts = download_attempts + 1
            WHERE id = %s
            """


def test_mark_image_stored_without_provenance_is_byte_identical():
    conn = _RecordingConn()
    scraper_db.mark_image_stored(conn, 42, "sreality/1/2.jpg", 123)
    sql, params = conn.cursor_obj.executed[0]
    assert sql == _PRE_496_SQL
    assert params == ("sreality/1/2.jpg", 123, 42)


def test_mark_image_stored_writes_provenance_only_when_measured():
    conn = _RecordingConn()
    scraper_db.mark_image_stored(
        conn, 42, "sreality/1/2.jpg", 123,
        rendition="sreality-1800-fit", width=1800,
    )
    sql, params = conn.cursor_obj.executed[0]
    assert _set_columns(sql) == {
        "storage_path", "phash", "rendition", "stored_width",
        "last_download_attempt_at", "download_attempts",
    }
    assert "stored_height" not in sql  # not measured -> not written
    assert params == ("sreality/1/2.jpg", 123, "sreality-1800-fit", 1800, 42)
