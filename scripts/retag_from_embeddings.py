"""Re-run the CLIP zero-shot tagging over ALREADY-tagged images, from their STORED
embeddings (image_clip_embeddings) — NO R2 download, NO image re-inference, just the
taxonomy's text anchors dotted with each stored vector. The way to apply a TAXONOMY change
(new logical tags, sharpened anchors) to the back catalogue cheaply.

Campaign-driven + resumable: re-tags every row whose `image_clip_tags.tagged_at` is older
than `app_settings.clip_taxonomy_retag_after` (the campaign cutoff — set it to now() to start
a re-tag after a taxonomy edit), and stamps `tagged_at = now()` on each, so a re-tagged row
drops out of the scan. No cutoff set → nothing to do; a cutoff later than now() waits for it.
A library: the runner's clip job (scripts/clip_tag_backfill.py) calls `campaign_cutoff` before
it loads the model and `retag` after tagging, with the same model load, for a slice of its
deadline (#2032). New / not-yet-tagged images go through that job's tag phase, which loads the
same live taxonomy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from scraper import stop

if TYPE_CHECKING:
    import psycopg
    import torch

    from scraper.clip_tagger import Tagger

LOG = logging.getLogger("retag_from_embeddings")

BATCH_SIZE = 512
PROGRESS_EVERY_S = 60

_CUTOFF_SQL = "SELECT value #>> '{}' FROM app_settings WHERE key = 'clip_taxonomy_retag_after'"

# The database's clock judges a future cutoff: it is the one that stamps tagged_at.
_PENDING_SQL = """
    SELECT %(cutoff)s::timestamptz > now() AS future,
           EXISTS (SELECT 1
                     FROM image_clip_tags t
                     JOIN image_clip_embeddings e ON e.image_id = t.image_id AND e.model = t.model
                    WHERE t.tagged_at < %(cutoff)s::timestamptz) AS pending
"""
_SELECT = """
    SELECT t.image_id, t.model, e.embedding
    FROM image_clip_tags t
    JOIN image_clip_embeddings e ON e.image_id = t.image_id AND e.model = t.model
    WHERE t.tagged_at < %(cutoff)s::timestamptz
    LIMIT %(limit)s
"""
_UPDATE = (
    "UPDATE image_clip_tags SET fine_tag = %s, logical_tag = %s, confidence = %s, "
    "render_score = %s, tagged_at = now() WHERE image_id = %s AND model = %s"
)


def _parse_vec(v: Any) -> list[float]:
    if isinstance(v, str):
        return [float(x) for x in v.strip("[]").split(",") if x]
    return list(v)


def _as_tensor(vectors: list[list[float]]) -> torch.Tensor:
    import numpy as np
    import torch

    return torch.from_numpy(np.array(vectors, dtype=np.float32))


def campaign_cutoff(conn: psycopg.Connection) -> str | None:
    """The open campaign's cutoff; None for an unset key, a JSON null, a drained campaign (no row older than it), or a
    cutoff still in the future (a re-tagged row, stamped now(), would match it again: it would never drain). A
    malformed value surfaces as the cast error of the pending check, before any model load."""
    with conn.cursor() as cur:
        cur.execute(_CUTOFF_SQL)
        row = cur.fetchone()
        cutoff = row[0] if row and row[0] else None
        if not cutoff:
            return None
        cur.execute(_PENDING_SQL, {"cutoff": cutoff})
        future, pending = cur.fetchone()
    if future:
        LOG.warning("RETAG cutoff=%s is later than the database's now(): no campaign until it passes", cutoff)
        return None
    return cutoff if pending else None


def retag(conn: psycopg.Connection, tagger: Tagger, cutoff: str, *, deadline: float,
          batch_size: int = BATCH_SIZE, clock: Callable[[], float] = time.monotonic) -> tuple[int, bool]:
    """Re-tags batches until none is left; a deadline or a stop between batches ends it first. (written, drained)."""
    written = batches = 0
    started = last_line = clock()
    while not stop.requested() and clock() < deadline:
        with conn.cursor() as cur:
            cur.execute(_SELECT, {"cutoff": cutoff, "limit": batch_size})
            rows = cur.fetchall()
        if not rows:
            return written, True
        results = tagger.tags_from_emb(_as_tensor([_parse_vec(r[2]) for r in rows]))
        with conn.cursor() as cur:
            cur.executemany(_UPDATE, [(tr.fine_tag, tr.logical_tag, tr.confidence, tr.render_score, r[0], r[1])
                                      for r, tr in zip(rows, results)])
        written += len(rows)
        batches += 1
        now = clock()
        if now - last_line >= PROGRESS_EVERY_S:
            last_line = now
            LOG.info("RETAG progress written=%d batches=%d secs=%.0f", written, batches, now - started)
    return written, False
