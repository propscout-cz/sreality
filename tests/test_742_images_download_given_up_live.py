"""A given-up photo download is the terminal reason 'download_given_up' (#2510; migration 742): the fifth failed
attempt writes it, the images claim stops reading the row, and 742 labels the rows given up before the writer did.
Live over the replayed schema (`TEST_DATABASE_URL`); the refetch re-arm is pinned in tests/scraper/test_image_phase.py,
since the replay lacks the (listing_id, sequence) key its upsert names."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from scraper import db as scraper_db
from tests._live_property import REQUIRED_DB, _advert, _property

pytestmark = REQUIRED_DB

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "742_images_download_given_up.sql"


def _image(cur: Any, listing_id: int, sequence: int, *, attempts: int = 0, stored: bool = False,
           reason: str | None = None) -> int:
    cur.execute("INSERT INTO images (listing_id, sequence, sreality_url, storage_path, download_attempts, "
                "unavailable_reason) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (listing_id, sequence, f"https://cdn.test/{uuid.uuid4().hex}.jpg",
                 f"img/{listing_id}/{uuid.uuid4().hex}.jpg" if stored else None, attempts, reason))
    return int(cur.fetchone()[0])


def _state(cur: Any, image_id: int) -> tuple[int, str | None]:
    cur.execute("SELECT download_attempts, unavailable_reason FROM images WHERE id = %s", (image_id,))
    return cur.fetchone()


def _claimed(cur: Any) -> set[int]:
    return {row[0] for row in scraper_db.pending_image_downloads(cur.connection, limit=100_000)}


def test_the_fifth_failed_attempt_gives_the_download_up_and_the_claim_stops_reading_it(cur: Any) -> None:
    ad = _advert(cur, _property(cur), source="bazos")
    image = _image(cur, ad, 1)
    for attempt in range(1, scraper_db.IMAGE_GIVE_UP_ATTEMPTS):
        scraper_db.mark_image_attempt(cur.connection, image, error="ReadTimeout")
        assert _state(cur, image) == (attempt, None)
        assert image in _claimed(cur)
    scraper_db.mark_image_attempt(cur.connection, image, error="ReadTimeout")
    assert _state(cur, image) == (scraper_db.IMAGE_GIVE_UP_ATTEMPTS, "download_given_up")
    assert image not in _claimed(cur)


def test_742_labels_only_unstored_unclassified_rows_at_the_limit_and_runs_twice(cur: Any) -> None:
    update = re.search(r"^update images\b.*?;", MIGRATION.read_text(encoding="utf-8"), re.M | re.S)
    assert update, "742 holds one UPDATE of images"
    ad = _advert(cur, _property(cur), source="idnes", active=False)
    given_up = _image(cur, ad, 1, attempts=5)
    long_given_up = _image(cur, ad, 2, attempts=6)
    still_trying = _image(cur, ad, 3, attempts=4)
    stored = _image(cur, ad, 4, attempts=5, stored=True)
    classified = _image(cur, ad, 5, attempts=5, reason="source_unavailable")
    for _ in range(2):
        cur.execute(update.group(0))
    assert {i: _state(cur, i)[1] for i in (given_up, long_given_up, still_trying, stored, classified)} == {
        given_up: "download_given_up", long_given_up: "download_given_up", still_trying: None, stored: None,
        classified: "source_unavailable",
    }
    assert _claimed(cur) & {given_up, long_given_up, stored, classified} == set()
    assert still_trying in _claimed(cur)
