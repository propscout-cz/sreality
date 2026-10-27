"""The job runner's `clip` job (#2032): CLIP room/plot tags and the 512-d embedding of every
stored image, then a taxonomy re-tag campaign, in one unsharded process with one model load.

Selects images not yet CLIP-tagged via the in-table marker images.clip_tagged_at (migration
232): those stored in the last 45 min first, then the priority kraje
(app_settings.clip_tagging_priority_region_ids, or a one-off --region-id), then newest first.
Downloads bytes from R2 on a worker pool (the next chunk's while this one encodes, each chunk
waiting at most --download-deadline for its GETs), tags and embeds them, and per chunk writes
the tags, then the vectors, then the stamp. Inside --max-seconds it selects again every
--reselect-seconds and whenever its snapshot drains (never offering again a GET that failed in
this run: the next slot retries it), and its tag phase ends on a select under one chunk; an
open campaign is then re-tagged from the stored vectors (scripts/retag_from_embeddings.py)
under the same deadline.

Exit codes, in the runner's terms: 0 done, nothing pending, downloads failing with something
marked or under a chunk of them, --dry-run; 75 more remains (the budget cut a run that was
making progress, or a campaign is still open); 143 a stop signal, after the chunk in hand; 2
SUPABASE_DB_URL or R2_* missing (R2 is required: a run that cannot download tags nothing); 1 a
whole chunk's downloads failed and nothing was marked, a decode or embed failure that is not one
image's, a crash. `--verify-reembed` runs only the re-embed proof (scripts/clip_reembed.py),
which writes nothing: 0 on a pass, else 1.

Usage:  python -m scripts.clip_tag_backfill --max-seconds 1500 --limit 5000 --workers 16
Required: SUPABASE_DB_URL, R2_*, and the `clip` extra.
"""

from __future__ import annotations

import argparse
import io
import logging
import math
import os
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any

from scraper import image_storage, stop
from scraper.clip_tagger import load_taxonomy
from scraper.cpus import job_cpus
from scripts import retag_from_embeddings

if TYPE_CHECKING:
    import psycopg
    import torch
    from PIL import Image

    from scraper.clip_tagger import Tagger, TagResult

LOG = logging.getLogger("clip_tag_backfill")

EXIT_MORE = 75
# Why a run ended -> its exit code (the module docstring; `late` is a budget spent before the first chunk).
EXITS = {"done": 0, "nothing": 0, "dry_run": 0, "errors": 0, "late": 0, "budget": EXIT_MORE, "retag": EXIT_MORE,
         "r2": 1, "decode": 1, "embed": 1, "stop": stop.EXIT_CODE, "refused": 2}
PROGRESS_EVERY_S = 60
MAX_WARNINGS = 10
# An image that fails to decode, or to embed alone, while a sibling of its chunk does is marked terminal: at most this
# many of each kind.
TERMINAL_PER_CHUNK, TERMINAL_PER_RUN = 2, 5
# How often a wait on a chunk's GETs looks at the stop signal.
_POLL_S = 0.5
# The least a chunk waits for its GETs once it is current: a prefetched chunk's deadline runs from its submission, and
# can pass while the chunk before it encodes.
MIN_WAIT_S = 5.0
# This job's R2 only: a GET that stalls before its response costs at most 2 × (5 + 20) s and the backoff; botocore's
# defaults (60 s, legacy retries) fit the 130–430 s chunks GitHub's shards waited on (#2032).
R2_TIMEOUTS: dict[str, int] = {"connect_timeout_s": 5, "read_timeout_s": 20, "max_attempts": 2}

# "Pending" = a stored image not yet CLIP-tagged. The source of truth is the in-table
# marker images.clip_tagged_at (migration 232), set in the same write that upserts the
# tag — NOT a cross-table anti-join against image_clip_tags, which forced a full seq-scan
# + sort of the ~5.2M-row images table every run and intermittently blew the pooler's
# 2-min statement timeout (the failed shards). The marker is backed by partial indexes
# (images_needs_clip_{id,lid}_idx, migration 232 + 359) that become selective as coverage
# rises, so the SELECT trends to sub-second. During a bulk backlog the partial index is NOT
# selective and a scan is unavoidable, so the SELECT runs under a generous batch
# statement_timeout (the 2-min pooler default is an OLTP limit, wrong for a backfill).
# The fresh arm comes first: at a 15-min cadence the priority kraje matter only in a
# backlog, and there they would starve the 45-min line for every other kraj (#2032). Region
# scope then lets the operator make one kraj's dedup ready first
# (app_settings.clip_tagging_priority_region_ids, ordered) — drained before the global
# newest-first fallback that covers every remaining region + the region-NULL (foreign) tail.
#
# Every arm keys the images<->listings join on the surrogate listing_id/id, NOT
# sreality_id: images.listing_id is fully populated + NOT NULL-enforced (migration 350,
# every portal's writer resolves it), while sreality_id goes NULL for every non-sreality
# row once the Gate-2 flip lands. A sreality_id-keyed INNER join (the region arm's old
# form) would then silently exclude those rows forever — not merely lag, a permanent
# region-priority blind spot for every non-sreality portal, with no error anywhere.
_SELECT_FRESH = """
    SELECT i.id, i.storage_path, (l.is_active IS TRUE) AS is_active
    FROM images i
    LEFT JOIN listings l ON l.id = i.listing_id
    WHERE i.clip_tagged_at IS NULL AND i.storage_path IS NOT NULL
      AND i.last_download_attempt_at >= now() - interval '45 minutes'
    ORDER BY i.id DESC
    LIMIT %(limit)s
"""
_SELECT_REGION = """
    SELECT i.id, i.storage_path, (l.is_active IS TRUE) AS is_active
    FROM listings l
    JOIN listing_location ll ON ll.listing_id = l.id
    JOIN images i ON i.listing_id = l.id
    WHERE ll.kraj_kod = %(region)s
      AND i.clip_tagged_at IS NULL AND i.storage_path IS NOT NULL
    ORDER BY (l.is_active IS TRUE) DESC, i.id DESC
    LIMIT %(limit)s
"""
_SELECT_GLOBAL = """
    SELECT i.id, i.storage_path, (l.is_active IS TRUE) AS is_active
    FROM images i
    LEFT JOIN listings l ON l.id = i.listing_id
    WHERE i.clip_tagged_at IS NULL AND i.storage_path IS NOT NULL
    ORDER BY i.id DESC
    LIMIT %(limit)s
"""

# Bridges a bulk-backlog scan; once coverage is high the marker partial index makes the
# SELECT sub-second and this margin is never approached. Well under the 55-min job.
SELECT_TIMEOUT_MS = 300_000

# Set the marker in the SAME write that records the tags, so a tagged image drops out of
# the partial index immediately (idempotent; only flips NULL -> now()).
_MARK_SQL = "UPDATE images SET clip_tagged_at = now() WHERE id = ANY(%s) AND clip_tagged_at IS NULL"

_UPSERT_SQL = """
    INSERT INTO image_clip_tags (image_id, model, fine_tag, logical_tag, confidence, render_score)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (image_id, model) DO UPDATE
      SET fine_tag = EXCLUDED.fine_tag, logical_tag = EXCLUDED.logical_tag,
          confidence = EXCLUDED.confidence, render_score = EXCLUDED.render_score,
          tagged_at = now()
"""

# Embeddings (for the cosine recall tier) stored for EVERY tagged image. They were
# active-listing-only until 2026-07: a listing tagged while delisted got tags but no
# vector, and on reactivation (touch_listings self-heal) its pairs carried no cosine —
# which routes the forensic compare to Sonnet BY DESIGN (route_by_cosine: None ->
# sonnet). Measured: ~19% of tagged images lacked vectors, and >=22% of Sonnet routes
# were these no-cosine defaults (~$30-45/mo overpay). Storing every tagged image costs
# ~2KB/row (~+4GB total) and closes the hole for good; nothing deletes vectors, so a
# vector outlives delist/reactivate cycles. pgvector parses the text '[f,f,...]' form.
_UPSERT_EMB_SQL = """
    INSERT INTO image_clip_embeddings (image_id, model, revision, embedding)
    VALUES (%s, %s, %s, %s::vector)
    ON CONFLICT (image_id, model) DO UPDATE
      SET embedding = EXCLUDED.embedding,
          revision  = EXCLUDED.revision
"""


def _vec_str(row: torch.Tensor) -> str:
    """A normalized embedding row -> pgvector text form '[f,f,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in row.tolist()) + "]"


def _decode(data: bytes) -> Image.Image:
    from PIL import Image  # base dep

    return Image.open(io.BytesIO(data)).convert("RGB")


def _download_decode(
    r2: image_storage.R2Client, rows: list, workers: int,
) -> tuple[list[tuple[int, Image.Image]], list[int]]:
    """Returns (decoded, terminal): decoded = [(image_id, RGB image)] that tagged
    successfully; terminal = [image_id] whose STORED bytes won't ever decode (corrupt /
    non-image — e.g. a video cover that slipped past the download reject-list). The
    caller marks BOTH so a permanently-undecodable image leaves the needs-clip partial
    index instead of being re-fetched + re-failed every run (the image-download pipeline
    terminal-marks the same way). A DOWNLOAD exception is treated as TRANSIENT (R2 blip)
    and left unmarked to retry — mirroring how the download pipeline distinguishes a gone
    object from a transient miss."""

    def _one(row: tuple[int, str, bool]) -> tuple[int, Image.Image | None, bool]:
        image_id, key = row[0], row[1]
        try:
            data = r2.download_bytes(key)
        except Exception:  # noqa: BLE001 - transient R2 error: retry on the next run
            return image_id, None, False
        try:
            return image_id, _decode(data), False
        except Exception:  # noqa: BLE001 - stored bytes won't decode: terminal, mark it
            return image_id, None, True

    decoded: list = []
    terminal: list = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for image_id, img, is_terminal in pool.map(_one, rows):
            if img is not None:
                decoded.append((image_id, img))
            elif is_terminal:
                terminal.append(image_id)
    return decoded, terminal


def _priority_region_ids(conn: psycopg.Connection, cli_region: int | None) -> list[int]:
    """Ordered kraj ids to drain before the global fallback. A CLI --region-id wins (ad-hoc
    dispatch); otherwise the operator's app_settings.clip_tagging_priority_region_ids list
    (empty = no priority, straight to the global newest-first sweep)."""
    if cli_region is not None:
        return [cli_region]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM app_settings WHERE key = 'clip_tagging_priority_region_ids'"
        )
        row = cur.fetchone()
    val = row[0] if row and row[0] is not None else None
    if isinstance(val, list):
        return [int(x) for x in val]
    return []


def _select_pending(conn: psycopg.Connection, *, limit: int,
                    priority_regions: list[int]) -> tuple[list, str]:
    """The snapshot of pending images: the last 45 min's first, then the priority kraje (in
    order), then the global newest-first fallback, up to `limit`. One read transaction with a
    batch statement_timeout (SET LOCAL) so a backlog scan can't be killed by the OLTP pooler
    default. Returns (rows, phase_label)."""
    arms = [("fresh", _SELECT_FRESH, {}), *((f"r{region}", _SELECT_REGION, {"region": region})
                                            for region in priority_regions), ("global", _SELECT_GLOBAL, {})]
    rows: list[tuple[int, str, bool]] = []
    phases: list[str] = []
    with conn.transaction(), conn.cursor() as cur:
        # SET is a utility statement — it can't take a bound parameter ($1), so the
        # (module-constant int) value is interpolated, not passed as %s.
        cur.execute(f"SET LOCAL statement_timeout = {int(SELECT_TIMEOUT_MS)}")
        for label, sql, params in arms:
            if len(rows) >= limit:
                break
            cur.execute(sql, {**params, "limit": limit - len(rows)})
            got = [(r[0], r[1], r[2]) for r in cur.fetchall()]
            if got:
                rows += got
                phases.append(f"{label}:{len(got)}")
    # The later arms have no exclusion of the earlier ones, so an image can reappear —
    # de-dup by image_id (keep first) so it isn't downloaded + CLIP-encoded twice.
    seen: set[int] = set()
    deduped = [r for r in rows if not (r[0] in seen or seen.add(r[0]))]
    return deduped, ("+".join(phases) or "empty")


def _r2_client(workers: int) -> image_storage.R2Client:
    return image_storage.R2Client.from_env(max_pool_connections=workers + 4, **R2_TIMEOUTS)


def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "none"


@dataclass
class _Tally:
    written: int = 0   # tag rows
    embedded: int = 0  # vector rows
    marked: int = 0    # stamps
    attempts: int = 0  # GETs started (one cancelled before it started is none)
    errors: int = 0    # of those, failed or past the download deadline
    terminal: int = 0  # stamped with no tag: undecodable bytes, or one image's embed failure
    decode_terminal: int = 0
    embed_terminal: int = 0
    selects: int = 0


class _Lines:
    """The run's log, sized for the runner's 64-line log_tail: progress and re-selects at most once a minute, at
    most MAX_WARNINGS warnings."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._last = -math.inf
        self._warnings = 0

    def every_minute(self, msg: str, *args: object) -> None:
        now = self._clock()
        if now - self._last >= PROGRESS_EVERY_S:
            self._last = now
            LOG.info(msg, *args)

    def warn(self, msg: str, *args: object) -> None:
        if self._warnings < MAX_WARNINGS:
            self._warnings += 1
            LOG.warning(msg, *args)


@dataclass
class _Fetch:
    rows: list
    futures: list[Future]
    submitted_at: float

    def cancel(self) -> None:
        for future in self.futures:
            future.cancel()


def _submit(pool: ThreadPoolExecutor, r2: image_storage.R2Client, rows: list,
            clock: Callable[[], float]) -> _Fetch:
    return _Fetch(rows, [pool.submit(r2.download_bytes, row[1]) for row in rows], clock())


def _await(fetch: _Fetch, deadline_s: float, clock: Callable[[], float]) -> tuple[list[tuple[int, bytes]], list[int]]:
    """(the chunk's downloaded bytes, the ids whose GET failed or was abandoned in flight), waiting until `deadline_s`
    after submission, at least MIN_WAIT_S once current, and never past a stop. A GET still queued is cancelled and was
    no attempt. The futures are dropped, so the bytes live only in the list returned."""
    until = max(fetch.submitted_at + deadline_s, clock() + MIN_WAIT_S)
    pending = set(fetch.futures)
    while pending and not stop.requested():
        left = until - clock()
        if left <= 0:
            break
        _done, pending = wait(pending, timeout=min(left, _POLL_S))
    got: list[tuple[int, bytes]] = []
    failed: list[int] = []
    for row, future in zip(fetch.rows, fetch.futures):
        if future.cancel():
            continue
        if future.done() and future.exception() is None:
            got.append((row[0], future.result()))
        else:
            failed.append(row[0])
    fetch.futures.clear()
    return got, failed


def _decode_all(pool: ThreadPoolExecutor,
                got: list[tuple[int, bytes]]) -> tuple[list[tuple[int, Any]], list[tuple[int, str]]]:
    """(decoded, [(image_id, exception name)] whose bytes do not decode). A MemoryError propagates: it is the
    process's, not the file's."""

    def one(item: tuple[int, bytes]) -> tuple[int, Any, str]:
        try:
            return item[0], _decode(item[1]), ""
        except MemoryError:
            raise
        except Exception as exc:  # noqa: BLE001 - judged by the caller, with its siblings
            return item[0], None, type(exc).__name__

    decoded: list[tuple[int, Any]] = []
    undecodable: list[tuple[int, str]] = []
    for image_id, img, name in pool.map(one, got):
        if img is None:
            undecodable.append((image_id, name))
        else:
            decoded.append((image_id, img))
    return decoded, undecodable


def _undecodable_alone(decoded: list, undecodable: list, tally: _Tally) -> bool:
    """Bytes that decode to no image beside a sibling that does are the file's: terminal, within the caps. Anything
    else (nothing of the chunk decodes, a cap passed) is the environment's, and marks nothing."""
    return bool(decoded) and len(undecodable) <= TERMINAL_PER_CHUNK and (
        tally.decode_terminal + len(undecodable) <= TERMINAL_PER_RUN)


def _embed(tagger: Tagger, decoded: list[tuple[int, Any]], batch_size: int, tally: _Tally,
           lines: _Lines) -> tuple[list[tuple[int, TagResult, str]], list[int]] | None:
    """([(image_id, tags, vector text)], terminal ids), or None when the failure is not one image's: a MemoryError,
    no image of the chunk embedding alone, or more than the caps. Then nothing of the chunk may be marked: a terminal
    stamp is a photo with no CLIP evidence for ever, and autodedup reads it as moved for ever."""
    try:
        emb = tagger.embed([d[1] for d in decoded], batch_size)
        results = tagger.tags_from_emb(emb)
        return [(image_id, r, _vec_str(emb[k])) for k, (image_id, r) in
                enumerate(zip([d[0] for d in decoded], results))], []
    except MemoryError:
        lines.warn("CLIP_TAG embed failed chunk n=%d: MemoryError", len(decoded))
        return None
    except Exception as exc:  # noqa: BLE001 - one image may be the cause: try them alone
        lines.warn("CLIP_TAG embed failed batch n=%d: %s; one image at a time", len(decoded), type(exc).__name__)
    encoded: list[tuple[int, TagResult, str]] = []
    failed: list[tuple[int, str]] = []
    for image_id, img in decoded:
        try:
            emb = tagger.embed([img], 1)
            (result,) = tagger.tags_from_emb(emb)
            encoded.append((image_id, result, _vec_str(emb[0])))
        except MemoryError:
            lines.warn("CLIP_TAG embed failed image=%d: MemoryError", image_id)
            return None
        except Exception as exc:  # noqa: BLE001 - judged below, with its siblings
            failed.append((image_id, type(exc).__name__))
    if not encoded or len(failed) > TERMINAL_PER_CHUNK or tally.embed_terminal + len(failed) > TERMINAL_PER_RUN:
        lines.warn("CLIP_TAG embed failed chunk n=%d alone=%d: not one image's", len(decoded), len(failed))
        return None
    for image_id, name in failed:
        lines.warn("CLIP_TAG embed failed image=%d: %s", image_id, name)
    return encoded, [image_id for image_id, _ in failed]


def _write(conn: psycopg.Connection, model: str, revision: str | None,
           encoded: list[tuple[int, TagResult, str]], mark_ids: list[int]) -> None:
    """Tags, then vectors, then the stamp: autodedup reads clip_tagged_at as written last, after the tags and the
    vector (autodedup/incremental.py decision_digest, autodedup/incremental_sql.py's CLIP probe)."""
    with conn.cursor() as cur:
        if encoded:
            cur.executemany(_UPSERT_SQL, [(image_id, model, r.fine_tag, r.logical_tag, r.confidence, r.render_score)
                                          for image_id, r, _ in encoded])
            # tagger.revision, not the file read: stamp what the loaded weights actually were.
            cur.executemany(_UPSERT_EMB_SQL, [(image_id, model, revision, vec) for image_id, _, vec in encoded])
        cur.execute(_MARK_SQL, (mark_ids,))  # drop from the needs-clip partial index


def _verdict(tally: _Tally, *, cut: bool, all_failed: bool = False, whole_chunk: bool = True) -> str:
    """Why a tag phase that stopped short ended. Progress decides, not the cut: more remains (75) only when the
    deadline stopped a run whose downloads mostly worked; an R2 outage never chains a run every minute. A failed
    chunk smaller than a whole one with nothing marked (a few dead objects alone in a quiet hour) is no outage."""
    if cut and not all_failed and tally.errors * 2 < tally.attempts:
        return "budget"
    if tally.attempts and not tally.marked and whole_chunk:
        return "r2"
    return "errors" if tally.attempts else "late"


def _take(snapshot: deque, n: int) -> list:
    return [snapshot.popleft() for _ in range(min(n, len(snapshot)))]


def _tag(conn: psycopg.Connection, r2: image_storage.R2Client, tagger: Tagger, rows: list, args: argparse.Namespace,
         *, priority: list[int], model: str, deadline: float, clock: Callable[[], float], tally: _Tally,
         lines: _Lines, decoders: int) -> str:
    """The tag phase; returns why it ended (EXITS). A chunk's GETs are submitted when the chunk before it has
    decoded, so they download while it encodes; at most one chunk is ever decoded in memory, on its own pool, so a
    GET abandoned in flight never delays it. A GET that failed in this run is not offered again before the next."""
    size = args.chunk
    snapshot, final = deque(rows), len(rows) < size
    last_select = clock()
    if last_select >= deadline:
        return _verdict(tally, cut=True)
    if stop.requested():
        return "stop"
    pool = ThreadPoolExecutor(max_workers=max(1, args.workers))
    decode_pool = ThreadPoolExecutor(max_workers=max(1, decoders))
    failed_ids: set[int] = set()
    try:
        fetch = _submit(pool, r2, _take(snapshot, size), clock)
        while True:
            got, failed = _await(fetch, args.download_deadline, clock)
            tally.attempts += len(got) + len(failed)
            tally.errors += len(failed)
            failed_ids.update(failed)
            if not got:
                return "stop" if stop.requested() else _verdict(tally, cut=clock() >= deadline, all_failed=True,
                                                               whole_chunk=len(fetch.rows) >= size)
            in_hand = {row[0] for row in fetch.rows}
            try:
                decoded, undecodable = _decode_all(decode_pool, got)
            except MemoryError:
                lines.warn("CLIP_TAG decode failed chunk n=%d: MemoryError", len(got))
                return "decode"
            del got
            if undecodable and not _undecodable_alone(decoded, undecodable, tally):
                lines.warn("CLIP_TAG decode failed chunk n=%d undecodable=%d: not one image's",
                           len(decoded) + len(undecodable), len(undecodable))
                return "decode"
            for image_id, name in undecodable:
                lines.warn("CLIP_TAG undecodable image=%d: %s", image_id, name)
            nxt: _Fetch | None = None
            if not stop.requested() and clock() < deadline and (snapshot or not final):
                if not snapshot or clock() - last_select >= args.reselect_seconds:
                    fresh, phase = _select_pending(conn, limit=args.limit, priority_regions=priority)
                    tally.selects += 1
                    last_select = clock()
                    fresh = [row for row in fresh if row[0] not in in_hand and row[0] not in failed_ids]
                    snapshot, final = deque(fresh), len(fresh) < size
                    lines.every_minute("CLIP_TAG reselect pending=%d phase=%s", len(fresh), phase)
                if snapshot and not stop.requested():
                    nxt = _submit(pool, r2, _take(snapshot, size), clock)
            encoded: list[tuple[int, TagResult, str]] = []
            terminal = [image_id for image_id, _ in undecodable]
            tally.decode_terminal += len(undecodable)
            if decoded:
                result = _embed(tagger, decoded, args.batch_size, tally, lines)
                if result is None:
                    if nxt is not None:
                        nxt.cancel()
                    return "embed"
                encoded, failed = result
                terminal += failed
                tally.embed_terminal += len(failed)
            del decoded
            _write(conn, model, tagger.revision, encoded, [image_id for image_id, _, _ in encoded] + terminal)
            tally.written += len(encoded)
            tally.embedded += len(encoded)
            tally.marked += len(encoded) + len(terminal)
            tally.terminal += len(terminal)
            del encoded
            lines.every_minute("CLIP_TAG progress=%d embedded=%d errors=%d attempts=%d terminal=%d left=%d",
                               tally.written, tally.embedded, tally.errors, tally.attempts, tally.terminal,
                               len(snapshot))
            if stop.requested():
                if nxt is not None:
                    nxt.cancel()
                return "stop"
            if nxt is None:
                return "done" if not snapshot and final else _verdict(tally, cut=True)
            if clock() >= deadline:  # no chunk starts past the deadline: the prefetched one is discarded
                nxt.cancel()
                return _verdict(tally, cut=True)
            fetch = nxt
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        decode_pool.shutdown(wait=False, cancel_futures=True)


def _exit(why: str) -> int:
    LOG.info("CLIP_JOB exit=%d why=%s", EXITS[why], why)
    return EXITS[why]


def run(conn: psycopg.Connection, args: argparse.Namespace, *, r2_factory: Callable[[], image_storage.R2Client],
        tagger_factory: Callable[[int], Tagger], clock: Callable[[], float] = time.monotonic) -> int:
    """One run: select and check the campaign; load the model only when either has work; tag, then retag with the
    same model and deadline. The budget's clock starts here, so the select and the load are spent inside it."""
    started = clock()
    deadline = started + args.max_seconds if args.max_seconds else math.inf
    taxonomy = load_taxonomy()
    model, revision = taxonomy["model"], taxonomy["revision"]  # KeyError beats embedding against unknown weights
    priority = _priority_region_ids(conn, args.region_id)
    rows, phase = _select_pending(conn, limit=args.limit, priority_regions=priority)
    cutoff = retag_from_embeddings.campaign_cutoff(conn)
    threads = args.threads or job_cpus()
    LOG.info("CLIP_TAG start pending=%d phase=%s budget_s=%s cpus=%d threads=%d torch=%s transformers=%s pillow=%s "
             "numpy=%s model=%s rev=%s retag=%s", len(rows), phase, args.max_seconds or "none", job_cpus(), threads,
             _version("torch"), _version("transformers"), _version("Pillow"), _version("numpy"), model,
             revision[:8], cutoff or "none")
    if args.dry_run:
        return _exit("dry_run")
    if not rows and cutoff is None:
        return _exit("nothing")
    if stop.requested():
        return _exit("stop")
    tagger = tagger_factory(threads)
    tally = _Tally(selects=1)
    lines = _Lines(clock)
    tag_started = clock()
    why = "done"
    if rows:
        why = _tag(conn, r2_factory(), tagger, rows, args, priority=priority, model=model, deadline=deadline,
                   clock=clock, tally=tally, lines=lines, decoders=threads)
    tag_secs = clock() - tag_started
    retagged, drained, retag_secs = 0, cutoff is None, 0.0
    if cutoff is not None and why == "done":
        retag_started = clock()
        retagged, drained = retag_from_embeddings.retag(conn, tagger, cutoff, deadline=deadline, clock=clock)
        retag_secs = clock() - retag_started
        if not drained:
            why = "stop" if stop.requested() else "retag"
    LOG.info("CLIP_TAG done written=%d embedded=%d errors=%d attempts=%d terminal=%d selects=%d secs=%.1f rate=%.2f",
             tally.written, tally.embedded, tally.errors, tally.attempts, tally.terminal, tally.selects, tag_secs,
             tally.written / tag_secs if tag_secs > 0 else 0.0)
    LOG.info("RETAG done written=%d drained=%s secs=%.1f", retagged, drained, retag_secs)
    return _exit(why)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-seconds", type=int, default=None,
                   help="The run's budget (unset or 0 = none); exit 75 when it cut a run making progress.")
    p.add_argument("--reselect-seconds", type=int, default=300,
                   help="Select again after this long, so a backlog drain stays newest first.")
    p.add_argument("--download-deadline", type=float, default=45.0,
                   help="Seconds a chunk waits for its GETs; stragglers stay unmarked for the next select.")
    p.add_argument("--limit", type=int, default=5000, help="Max images per select (a re-select refills it).")
    p.add_argument("--workers", type=int, default=16, help="Parallel R2 downloads.")
    p.add_argument("--chunk", type=int, default=128,
                   help="Images per download+tag+commit cycle (bounds memory).")
    p.add_argument("--batch-size", type=int, default=32, help="CLIP encode batch.")
    p.add_argument("--threads", type=int, default=0, help="torch threads (0=JOB_CPUS or the container's CPUs).")
    p.add_argument("--region-id", type=int, default=None,
                   help="One-off: drain this kraj after the last 45 min, then the global fallback. "
                        "Overrides app_settings.clip_tagging_priority_region_ids. "
                        "Unset = use that operator priority list (empty = global only).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report the pending count and exit without tagging.")
    p.add_argument("--verify-reembed", action="store_true",
                   help="Only the re-embed proof (scripts/clip_reembed.py): re-compute the frozen 100-image sample "
                        "and compare; writes nothing.")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = _parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop.install()
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        LOG.error("CLIP_TAG refused: SUPABASE_DB_URL missing")
        return _exit("refused")
    if not image_storage.is_configured():
        LOG.error("CLIP_TAG refused: R2_* missing")
        return _exit("refused")

    import psycopg

    from scraper.clip_tagger import Tagger

    with psycopg.connect(db_url, autocommit=True, prepare_threshold=None) as conn:
        if args.verify_reembed:
            from scripts import clip_reembed

            return clip_reembed.verify(conn, args, r2_factory=lambda: clip_reembed.r2_client(args.workers),
                                       tagger_factory=Tagger.load)
        return run(conn, args, r2_factory=lambda: _r2_client(args.workers), tagger_factory=Tagger.load)


if __name__ == "__main__":
    raise SystemExit(main())
