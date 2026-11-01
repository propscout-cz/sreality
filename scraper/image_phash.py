"""Perceptual image hashing for cross-source dedup (multi-portal PR5).

dHash (difference hash): resize to 9x8 grayscale, compare horizontally adjacent
pixels -> 64 bits. Pillow-only (no numpy/scipy). Robust to scale / compression /
minor colour shifts — the near-duplicate case when the same listing's photos are
reused across portals. The hash is stored in `images.phash` as a signed bigint;
Hamming distance is `bit_count(a # b)` in Postgres.

Also the bounded retry of stored images the download could not hash (the images lane, `retry_unhashed`).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    import psycopg

    from scraper.image_storage import R2Client

LOG = logging.getLogger(__name__)

_W = 9
_H = 8
_MASK64 = (1 << 64) - 1

# A row at this count stays NULL for good. migrations/703's partial index names the same number (a test pins it).
PHASH_MAX_ATTEMPTS = 5
# About 5 s of the lane's 60 s interval at the measured ~0.1 s per UPDATE round trip while R2 answers; a hung R2 holds
# the lane for minutes at the timeouts below, and the downloads are stalled on that same R2 anyway.
PHASH_RETRY_BATCH = 50
PHASH_RETRY_WORKERS = 4

# The cap is a literal, not a parameter: the planner proves the partial index only from a constant.
# Active-first costs a sort over the index's rows, which the cap keeps to the handful not yet given up on.
_SELECT_UNHASHED_SQL = f"""
    SELECT i.id, i.storage_path, i.phash_attempts
    FROM images i
    LEFT JOIN listings l ON l.id = i.listing_id
    WHERE i.phash IS NULL AND i.storage_path IS NOT NULL AND i.phash_attempts < {PHASH_MAX_ATTEMPTS}
    ORDER BY (l.is_active IS TRUE) DESC, i.id DESC
    LIMIT %(limit)s
"""


def compute_dhash(image_bytes: bytes) -> int:
    """64-bit unsigned dHash of one image."""
    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize(
        (_W, _H), Image.Resampling.LANCZOS,
    )
    px = list(img.getdata())
    bits = 0
    for row in range(_H):
        base = row * _W
        for col in range(_W - 1):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def to_signed64(value: int) -> int:
    """Map a 64-bit unsigned hash into Postgres bigint's signed range.

    The bit pattern is preserved, so `bit_count(a # b)` over two stored values
    still returns the Hamming distance regardless of sign.
    """
    value &= _MASK64
    return value - (1 << 64) if value >= (1 << 63) else value


def hamming(a: int, b: int) -> int:
    """Hamming distance between two 64-bit hashes (sign-agnostic)."""
    return bin((a ^ b) & _MASK64).count("1")


def select_unhashed(conn: psycopg.Connection, limit: int) -> list[tuple[int, str, int]]:
    """Up to `limit` stored, unhashed rows still under the cap: (id, storage_path, phash_attempts)."""
    with conn.cursor() as cur:
        cur.execute(_SELECT_UNHASHED_SQL, {"limit": limit})
        return cur.fetchall()


def _hash_one(r2: R2Client, image_id: int, key: str) -> tuple[int, int | None, str | None]:
    """Download + hash one image (worker thread; no DB I/O here)."""
    try:
        return image_id, to_signed64(compute_dhash(r2.download_bytes(key))), None
    except Exception as exc:  # noqa: BLE001 - one bad image must not kill the pass
        return image_id, None, str(exc)


def _retry_r2() -> R2Client:
    # Lazy like the rest of the image path: boto3 and the egress stack stay off this module's importers.
    from scraper import image_storage

    # Tight, unlike the downloads' defaults: a hung R2 must not hold the images lane for the whole batch.
    return image_storage.R2Client.from_env(max_pool_connections=PHASH_RETRY_WORKERS,
                                           connect_timeout_s=5, read_timeout_s=15, max_attempts=2)


def retry_unhashed(
    conn: psycopg.Connection, make_r2: Callable[[], R2Client] = _retry_r2, *, limit: int = PHASH_RETRY_BATCH,
) -> dict[str, int]:
    """One bounded pass: hash what it can, count a failure on the rest. No work builds no R2 client."""
    rows = select_unhashed(conn, limit)
    counts = {"phash_retried": len(rows), "phash_hashed": 0, "phash_given_up": 0}
    if not rows:
        return counts
    r2 = make_r2()
    attempts = {image_id: done for image_id, _key, done in rows}
    with ThreadPoolExecutor(max_workers=PHASH_RETRY_WORKERS) as pool:
        for image_id, value, error in pool.map(lambda row: _hash_one(r2, row[0], row[1]), rows):
            with conn.cursor() as cur:
                if error is None:
                    cur.execute("UPDATE images SET phash = %s WHERE id = %s", (value, image_id))
                    counts["phash_hashed"] += 1
                    continue
                cur.execute("UPDATE images SET phash_attempts = phash_attempts + 1 WHERE id = %s", (image_id,))
            if attempts[image_id] + 1 >= PHASH_MAX_ATTEMPTS:
                counts["phash_given_up"] += 1
                LOG.warning("PHASH gave up id=%s after %d attempts, phash stays NULL: %s",
                            image_id, PHASH_MAX_ATTEMPTS, error)
    return counts
