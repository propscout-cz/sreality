"""Append-on-change writer for the content-addressed payload archive (02 §2.3.2 P1/P4).

`portal_raw_payloads` (migration 382, columns completed by 403) is the store
`location_claims` is mined from: without it the hourly lane has no substrate but
`listings.raw_json`, which is latest-wins, so a re-read of "the page" answers a
different question every time it is asked. This module is the only sanctioned way
to put a body in it.

Four properties define the write, and each one is load-bearing:

  * **THE BODY LIVES IN R2; POSTGRES HOLDS THE METADATA ROW.** Identity, both hashes,
    sizes, version, pin state and the content-addressed key stay in the database; the
    bytes go to the bucket above `DEFAULT_R2_THRESHOLD_BYTES`, which is set at
    Postgres's own TOAST boundary so that only what Postgres stores for free stays
    inline. That is what makes the archive affordable at all: a metadata row is 713 B
    against ~20 KB for the same row with its body, and object storage is ~1/100th the
    price of database storage (W2a-7's measurement). It is not a latency trade: nothing on a user-facing path reads a body.
    An unconfigured store REFUSES the payload write (loudly, per fetch) rather than
    silently rebuilding the database-resident archive; see `append_payload`.

  * **Identity is the NORMALISED hash.** `payload_sha256` is taken over the body with
    the source's volatile paths stripped and key order / whitespace canonicalised
    (`payload_norm.normalise`, reused verbatim — the same function the W2a-0 churn
    instrument is measuring live). `body_sha256` carries the raw bytes' hash for
    forensics and is never the uniqueness key. This is the difference between an
    archive bounded by real content change and one that appends a row every time a
    CSRF token rerolls.
  * **An unchanged refetch appends nothing.** The INSERT collides on
    `(source, source_id_native, page_kind, payload_sha256)` and only bumps
    `last_observed_at`; a changed body appends a new `version_seq`. That pair is
    `06` W2a gate (b), tested in both directions.
  * **Retention runs in the same transaction as the append** (`02 §2.3.2 P4`), not in
    a job that may never be scheduled: re-pin first (first version, latest version,
    any body a claim references, and any body a disputed claim points at), then
    delete unpinned rows beyond the version cap — ranked newest-first, with
    unsuccessful fetches behind successful ones so a portal outage evicts itself
    rather than the listing's real history. Growth is bounded by row count rather
    than by operator diligence.

There is NO time floor: every changed body is appended, because the claim lane mines the
LATEST body (rule 25). A 7-day per-listing floor (W2a-7, 2026-08-14 to 2026-10-22) discarded
a changed body inside its window, so a refetch that found a location the first fetch lacked
never reached the lane (≥ 1,133 a day on the HTML portals, measured 2026-10-22). The cap
bounds the stock.

LIVE on all nine portals (826,948 rows as of 2026-09-21, oldest 2026-05-28). Scrapers
never call `append_payload` directly: the one sanctioned form is
`scraper.db.append_payload_if_enabled`, a never-raising wrapper called from the
`portal_raw_pages` chokepoint and from the two scrapers that hold a body outside it
(`scraper/main.py`, `scraper/bezrealitky_main.py`). It archives `detail` bodies and
nothing else — the three W2a flags went away on 2026-09-11 when rule 25 made the stored
detail body the claim lane's second substrate.

`portal_raw_pages` — the existing latest-wins staging table — is NOT this store and is
never written or deleted here; it is the migration source W2a-4 read.
"""

from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple, Protocol

import psycopg

from location_data import loader_db
from location_data.payload_norm import (
    VolatileProfile,
    normalise,
    resolve_normalisation,
)

LOG = logging.getLogger("location_data.payloads")

# One bounded transaction per append (INSERT + re-pin + prune). Short on purpose:
# this runs inside the drain's batch write, where a stuck statement stalls a lane
# rather than one listing.
WRITE_TIMEOUT_ENV = "LOCATION_PAYLOAD_WRITE_TIMEOUT_S"
DEFAULT_WRITE_TIMEOUT_S = 60

# `persistence.version_cap` (02 §2.3.2 P4) until W2a-3b puts it in the contracts.
#
# THE CAP IS THE CEILING; the churn rate only sets how fast the ceiling is reached. So
# this number, not the quality of any volatile profile, is what the archive's worst case
# costs, in the two currencies the archive actually spends.
#
# 2, not the 20 this shipped with. 20 was inherited from the design document and never
# chosen against a number. What the number means changed once bodies moved to R2, so it
# was re-derived rather than kept by inertia:
#
#   * A unit of cap USED to cost 6.1 GB of Postgres — a third of the whole subsystem's
#     envelope per slot — which made the cap a budget instrument and made 2 the largest
#     value that fit at all. With bodies in the bucket a unit of cap costs 0.48 GB of
#     metadata rows and ~$0.14/month of object storage, and the archive fits the
#     allowance up to cap 7. The budget no longer picks the number.
#   * What still picks it is READERS, and there are two: the claim lane, which mines
#     the LATEST body, and a verifier, which reads whatever body it is handed. Cap 2 is
#     "first, previous, current", and no reader is named for a body older than that. A
#     claim no longer pins anything (W1-b: the claim spine carries no pointer back into
#     the archive), so the cap governs the whole group except its first version. Cheap
#     storage is a reason not to PANIC about depth; it is not a reader.
#
# So 2 survives its own re-derivation. Going deeper is a one-line change; the frozen
# measurement corpus that priced it (`location_data/payload_budget.py`) went with the rest
# of the storage-gate machinery under rule 25 — re-measure before raising it.
VERSION_CAP_ENV = "LOCATION_PAYLOAD_VERSION_CAP"
DEFAULT_VERSION_CAP = 2

# How often the process-local counters below are rolled up into one log line. Per-event
# logging is not an option: every detail fetch decides once, so one line each would double
# the drain's log volume.
STATS_EVERY_ENV = "LOCATION_PAYLOAD_STATS_EVERY"
DEFAULT_STATS_EVERY = 200

# Below this, gzip's ~20-byte header and the CPU cost outweigh the saving, and TOAST
# already compresses inline bytea anyway. Above it the portals' bodies (41-245 KB of
# HTML) compress 5-10x, which is the whole storage projection.
GZIP_MIN_BYTES_ENV = "LOCATION_PAYLOAD_GZIP_MIN_BYTES"
DEFAULT_GZIP_MIN_BYTES = 4096

# P4.3: "if genuine cold storage is wanted, it is R2" — and it is, by default, for
# essentially every body. A compressed body over this size goes to the bucket and
# Postgres keeps only the metadata row (identity, both hashes, sizes, version, pin
# state, the key). THIS IS THE ROUTINE PATH, not the pathological one; it shipped at
# 256 KB, where nothing spilled at all and the whole archive was database-resident.
#
# 2048 is Postgres's own boundary, which is why it is not a round number chosen for
# looks. `TOAST_TUPLE_THRESHOLD` is ~2 KB: under it a value rides in the main heap
# tuple and costs nothing beyond the bytes; over it Postgres compresses it and moves it
# out of line into a TOAST relation with its own index and its own I/O. So this
# threshold is exactly "the archive keeps what Postgres stores for free, and puts
# everything that would go out-of-line into the store where out-of-line is 100x
# cheaper". On today's corpus only bezrealitky's JSON (1.3 KB gzipped, 0.2 % of the
# bytes) stays inline.
#
# WHY THIS IS NOT A LATENCY TRADE. Nothing on a user-facing path reads a payload body:
# the readers are the W2 re-mine, one-off backfills and the round-trip verifier, all
# batch, so the cost is batch wall-clock and not request latency — a full 445k-page
# sweep parallelises to roughly ten minutes at 32-64 concurrent GETs. What
# database-resident bodies WOULD cost is the whole instance's shared buffer cache, which
# this platform has been burned by twice (the Browse statement-timeout saga; the
# 2026-08-10 multi-lane incident).
#
# A HOT-WINDOW HYBRID WAS CONSIDERED AND REJECTED, and it is worth recording why so it
# is not re-argued: its eviction predicate cannot be written. "Evict once processed" is
# undefinable when the archive's entire purpose is re-mining with extractors that have
# not been authored yet. If a specific batch job later proves slow, the sanctioned
# remedy is a per-run local disk cache inside the worker, NOT a second
# database-resident tier.
R2_THRESHOLD_ENV = "LOCATION_PAYLOAD_R2_THRESHOLD_BYTES"
DEFAULT_R2_THRESHOLD_BYTES = 2048

R2_PREFIX = "payloads"

# The key is derived from the hash of the bytes the object HOLDS — `body_sha256`, the
# raw body, never `payload_sha256` — so no listing is ever needed to find a body and no
# list_objects is ever needed to enumerate one. Keying on the normalised hash instead
# would hand one key to two rows whose normalised bodies coincide while their raw bytes
# differ (the same interstitial page under two listings, once the per-request nonce is
# stripped): the second row's own bytes would never be uploaded and its `body_sha256`
# would not match the object it points at. `source` is the only free-form component,
# and it comes from our own portal registry — validated anyway, because a key built
# from unvalidated input is how a path escape gets written (the images lane's _KEY_RE
# is the same boundary).
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

_CONTENT_ENCODINGS = ("identity", "gzip")


class PayloadError(RuntimeError):
    """The body could not be archived; the caller decides whether that is fatal."""


@dataclass(slots=True)
class ArchiveStats:
    """What the write path decided, process-local — the cap made visible. Counted, not
    sampled; only the LOG LINE is rate-limited (`STATS_EVERY_ENV`)."""

    appended: int = 0
    unchanged: int = 0
    evicted_rows: int = 0
    evicted_bytes: int = 0

    @property
    def decisions(self) -> int:
        return self.appended + self.unchanged

    def as_dict(self) -> dict[str, int]:
        return {
            "appended": self.appended, "unchanged": self.unchanged,
            "evicted_rows": self.evicted_rows, "evicted_bytes": self.evicted_bytes,
            "decisions": self.decisions,
        }


_STATS = ArchiveStats()


def archive_stats() -> ArchiveStats:
    """The live counters. Callers must not mutate them; `reset_archive_stats` does."""
    return _STATS


def reset_archive_stats() -> None:
    """Zero the counters (a test, or a long-lived worker starting a fresh window)."""
    global _STATS
    _STATS = ArchiveStats()


def log_archive_stats() -> None:
    """Emit the rollup unconditionally — for a lane that wants it at its own boundary."""
    LOG.info(
        "PAYLOAD stats appended=%d unchanged=%d evicted_rows=%d evicted_bytes=%d",
        _STATS.appended, _STATS.unchanged, _STATS.evicted_rows, _STATS.evicted_bytes,
    )


def _record_decision(*, inserted: bool, evicted_rows: int, evicted_bytes: int) -> None:
    """Count one write decision and roll the counters up every `STATS_EVERY` of them.

    Emitting from HERE rather than from a lane's end-of-run hook is deliberate: the
    archive is written from the index walk, seven detail drains, two bespoke call sites
    and the always-on worker, and the worker never reaches an end of run at all. A
    counter that only reports at process exit would be silent on exactly the lane that
    runs the most fetches.
    """
    if inserted:
        _STATS.appended += 1
    else:
        _STATS.unchanged += 1
    _STATS.evicted_rows += evicted_rows
    _STATS.evicted_bytes += evicted_bytes

    every = loader_db.env_positive_int(STATS_EVERY_ENV, DEFAULT_STATS_EVERY)
    if _STATS.decisions % every == 0:
        log_archive_stats()


class EvictedBody(NamedTuple):
    """One row a retention statement removed, and what it was holding.

    * `byte_size` is the body as fetched; `stored_bytes` is the ENCODED size, wherever
      the bytes lived — `stored_byte_size` (migration 406) for a spilled body, and
      `octet_length(body)` for an inline one or a row written before 406.
    * The retention statement RETURNS this column order, so the reclaimed-bytes figure
      is one set of numbers wherever it is read. The cap
      used to return only (id, key), which silently left every capped row out of the
      bytes-reclaimed number the storage sign-off is read from; reading only
      `octet_length(body)` would have reintroduced exactly that hole the moment bodies
      became R2-resident by default, since that column is then NULL on every row.
    """

    id: int
    r2_key: str | None
    byte_size: int
    stored_bytes: int | None


class ObjectStore(Protocol):
    """The ONE R2 operation this module needs.

    Declared locally rather than imported from `location_data.archive`: that module
    pulls in the RÚIAN CSV reader for one dataclass, and this one is on the scraper's
    import path from W2a-2 onward. `scraper.image_storage.R2Client` satisfies it.

    A HEAD used to precede every upload, to skip re-writing an object some other row had
    already put there under the same content address. It was removed with the R2
    default: the upload runs INSIDE the write transaction, so a round trip saved there is
    a round trip the database is not holding a row lock through, and the PUT it avoided
    is ~20 KB. A repeat write is a no-op in effect anyway — the key is the hash of the
    bytes, so re-uploading writes the same object.
    """

    def upload_bytes(self, key: str, data: bytes, content_type: str = ...) -> None: ...


@dataclass(frozen=True, slots=True)
class PayloadRef:
    """What the caller needs to know about the row that is now stored.

    EVERY FIELD DESCRIBES THE ROW, NOT THE FETCH. On a collision (`inserted` False)
    the stored body is the one an earlier fetch wrote, under its own raw bytes and R2 key.

    `stored_bytes` is the ENCODED size wherever the bytes live — `stored_byte_size`
    (migration 406) for a spilled body, `octet_length(body)` for an inline one. None
    only for a row written before 406 whose body was already in the bucket.

    `body_r2_key` set means Postgres holds no bytes for this payload; read them with
    `decode_body(store.download_bytes(ref.body_r2_key), ref.content_encoding)`.
    """

    id: int
    payload_sha256: bytes
    body_sha256: bytes
    version_seq: int
    inserted: bool
    byte_size: int
    stored_bytes: int | None
    content_encoding: str
    body_r2_key: str | None
    evicted_ids: tuple[int, ...]
    evicted_r2_keys: tuple[str, ...]


def r2_key(source: str, body_sha256: bytes) -> str:
    """`payloads/<source>/<sha[:2]>/<sha>.gz` — derivable from the row alone.

    `body_sha256`, because that is the hash of what the object holds (the RAW body,
    gzipped); see the note on `_SOURCE_RE`.
    """
    if not _SOURCE_RE.match(source):
        raise PayloadError(f"refusing to build an R2 key for source={source!r}")
    digest = body_sha256.hex()
    return f"{R2_PREFIX}/{source}/{digest[:2]}/{digest}.gz"


def encode_body(body: bytes, *, gzip_min_bytes: int | None = None) -> tuple[bytes, str]:
    """(stored bytes, content_encoding) — deterministic, so the same body always
    encodes to the same bytes.

    `mtime=0`: gzip stamps the current time into its header by default, which would
    make two encodings of one body differ and turn every re-encode into a spurious
    object rewrite. It has no effect on `payload_sha256` (that is taken over the
    normalised body, before encoding), only on reproducibility.
    """
    threshold = gzip_min_bytes if gzip_min_bytes is not None else (
        loader_db.env_positive_int(GZIP_MIN_BYTES_ENV, DEFAULT_GZIP_MIN_BYTES))
    if len(body) <= threshold:
        return body, "identity"
    return gzip.compress(body, mtime=0), "gzip"


def decode_body(stored: bytes, content_encoding: str) -> bytes:
    """Inverse of `encode_body`. The round-trip verifier's half of W2a gate (a)."""
    if content_encoding == "identity":
        return stored
    if content_encoding == "gzip":
        return gzip.decompress(stored)
    raise PayloadError(f"unknown content_encoding {content_encoding!r}")


@dataclass(frozen=True, slots=True)
class BodyPlacement:
    """Where one body goes and in what form — the R2-vs-inline decision, made once.

    `r2_key` set means the bucket holds the bytes and the row holds NULL in `body`;
    `r2_key` None means the row carries them. Exactly one of the two, always, which is
    what `prp_body_present` (382) enforces on the other side.

    Shared by the live writer and W2a-4's bulk backfill so the two cannot place the same
    body differently — a backfill that wrote inline while the live path spilled would
    make the footprint the operator signed wrong by the size of the whole legacy corpus.
    """

    stored: bytes
    content_encoding: str
    r2_key: str | None

    @property
    def spills(self) -> bool:
        return self.r2_key is not None


def plan_placement(
    source: str,
    body: bytes,
    body_sha256: bytes,
    *,
    r2_threshold: int | None = None,
    gzip_min_bytes: int | None = None,
) -> BodyPlacement:
    """Compress, then decide where the result lives. Pure — no network, no DB.

    Compression comes FIRST because the threshold is about what Postgres would have to
    store, not about what the portal served: a 40 KB page that gzips to 6 KB is a 6 KB
    decision. The re-compress arm below catches the one case the two thresholds can
    disagree on — a body under `gzip_min_bytes` but over `r2_threshold`, reachable
    whenever the R2 threshold is set lower than the gzip one, as it is by default.
    """
    threshold = r2_threshold if r2_threshold is not None else (
        loader_db.env_positive_int(R2_THRESHOLD_ENV, DEFAULT_R2_THRESHOLD_BYTES))
    stored, encoding = encode_body(body, gzip_min_bytes=gzip_min_bytes)
    if len(stored) > threshold and encoding != "gzip":
        stored, encoding = gzip.compress(body, mtime=0), "gzip"
    if len(stored) <= threshold:
        return BodyPlacement(stored, encoding, None)
    return BodyPlacement(stored, encoding, r2_key(source, body_sha256))


# `version_seq` is computed in the INSERT itself so an append is ONE round trip and
# cannot interleave with a read-then-write from another worker. Concurrent appends
# for the same key can still land on the same number (the identity constraint is on
# payload_sha256, not on version_seq), which is cosmetic: the cap orders by
# version_seq with `id` as the tiebreaker.
#
# `first_observed_at` / `fetched_at` use least(), not the insert value: bodies do not
# always arrive in observation order (W2a-4 backfills `portal_raw_pages.fetched_at`
# into a store the live path may already have written), and "first observed" must mean
# the earliest observation, not the earliest write.
#
# `snapshot_id` fills in but never overwrites: a body first archived without an anchor
# (the unanchored live path, or W2a-4's backfill of a page that predates its snapshot)
# gains one the moment an anchored fetch of the SAME body arrives, and an anchored row
# is never re-anchored by a later unanchored sighting.
#
# The RETURNING list is the stored row, not the fetch: see PayloadRef.
_APPEND_SQL = """
INSERT INTO portal_raw_payloads
    (source, source_id_native, listing_id, page_kind, payload_sha256, body_sha256,
     content_type, content_encoding, body, body_r2_key, byte_size, stored_byte_size,
     http_status, contract_version, normalizer_version, snapshot_id, pinned,
     version_seq, first_observed_at, last_observed_at, fetched_at)
VALUES
    (%(source)s::text, %(source_id_native)s::text, %(listing_id)s::bigint,
     %(page_kind)s::location_page_kind, %(payload_sha256)s::bytea,
     %(body_sha256)s::bytea, %(content_type)s::text, %(content_encoding)s::text,
     %(body)s::bytea, %(body_r2_key)s::text, %(byte_size)s::integer,
     %(stored_byte_size)s::integer,
     %(http_status)s::integer, %(contract_version)s::integer,
     %(normalizer_version)s::text, %(snapshot_id)s::bigint, true,
     (SELECT coalesce(max(prior.version_seq), 0) + 1
        FROM portal_raw_payloads prior
       WHERE prior.source = %(source)s
         AND prior.source_id_native = %(source_id_native)s
         AND prior.page_kind = %(page_kind)s::location_page_kind),
     %(observed_at)s::timestamptz, %(observed_at)s::timestamptz,
     %(observed_at)s::timestamptz)
ON CONFLICT (source, source_id_native, page_kind, payload_sha256) DO UPDATE
   SET last_observed_at  = greatest(EXCLUDED.last_observed_at,
                                    portal_raw_payloads.last_observed_at),
       first_observed_at = least(EXCLUDED.first_observed_at,
                                 portal_raw_payloads.first_observed_at),
       fetched_at        = least(EXCLUDED.fetched_at,
                                 portal_raw_payloads.fetched_at),
       snapshot_id       = coalesce(portal_raw_payloads.snapshot_id,
                                    EXCLUDED.snapshot_id)
RETURNING id, version_seq, (xmax = 0) AS inserted, payload_sha256, body_sha256,
          byte_size, content_encoding, body_r2_key,
          coalesce(stored_byte_size, octet_length(body)) AS stored_bytes
"""

# Two sessions appending to ONE group at once would put two `_REPIN_SQL` UPDATEs and two
# `_PRUNE_SQL` DELETEs on overlapping rows of that group, which is a deadlock class.
#
# A transaction-scoped advisory lock on the group serialises them. It is xact-scoped, not
# session-scoped, so it is safe behind a transaction-mode pooler (a session lock would
# leak onto whichever backend the pooler handed out next — this project's standing rule).
# Collisions between unrelated groups just serialise two appends that would not have
# contended; a 64-bit hash makes that vanishingly rare and harmless when it happens.
_GROUP_LOCK_SQL = """
SELECT pg_advisory_xact_lock(
    hashtextextended(%(source)s || '\x1f' || %(source_id_native)s || '\x1f'
                     || %(page_kind)s, 0))
"""

# P4's pin predicate, recomputed AUTHORITATIVELY over the whole group rather than
# only setting new pins: the row that was the latest before this append has to LOSE
# its pin, or the cap never bites and the archive grows without bound.
#
# TWO EDGES, NOTHING ELSE (W1-b, migration 498). The predicate used to have two more arms,
# both reaching into the claim store: a body a claim's `payload_id` FK pointed at, and a
# body whose content address a DISPUTED claim named through `location_contradictions_open`.
# Both columns are gone — the claim spine is 19 columns of value and provenance now, with
# no pointer back into the archive — so the version cap alone decides what stays, and this
# statement (which runs on EVERY scraper payload append) stops joining a 5 M-row table
# twice. `pinned` itself stays: `_PRUNE_SQL` reads it, and the first-version exemption is
# what keeps a portal outage from evicting a listing's entire real history.
_REPIN_SQL = """
WITH grp AS (
    SELECT id, version_seq
      FROM portal_raw_payloads
     WHERE source = %(source)s
       AND source_id_native = %(source_id_native)s
       AND page_kind = %(page_kind)s::location_page_kind
),
edges AS (
    SELECT min(version_seq) AS first_seq, max(version_seq) AS latest_seq FROM grp
),
want AS (
    SELECT g.id,
           (coalesce(g.version_seq = e.first_seq, false)
            OR coalesce(g.version_seq = e.latest_seq, false)) AS pinned
      FROM grp g CROSS JOIN edges e
)
UPDATE portal_raw_payloads p
   SET pinned = w.pinned
  FROM want w
 WHERE p.id = w.id
   AND p.pinned IS DISTINCT FROM w.pinned
"""

# Pinned rows occupy ranks: with a cap of 20 and two pins inside it, eighteen
# ordinary versions survive. A pin OUTSIDE the cap (the first version, once the group
# is deeper than the cap) survives regardless — that is what "exempt" means.
#
# UNSUCCESSFUL FETCHES RANK LAST. `http_status` was written and never read, which made
# an error body cost a version: a portal outage lasting `version_cap` refetches (idnes
# serving a 503 interstitial whose request id is not in the volatile profile appends
# one row per 6-hourly fetch) evicted the listing's ENTIRE real history except the
# first-version pin, irreversibly — `portal_raw_pages` is latest-wins, so there is
# nothing to restore from. Ranking non-2xx bodies behind 2xx ones makes the outage
# evict itself instead. A NULL status ranks WITH the successes on purpose: W2a-4's
# backfill from `portal_raw_pages` has no status to carry, and those bodies are the
# oldest real history in the store.
#
# `NULLS LAST` and the `id` tiebreaker: DESC sorts NULLs first in Postgres, which
# would let a version_seq-less row masquerade as the newest and shield the real
# newest from the cap; and ties in version_seq must not reshuffle between runs.
_PRUNE_SQL = """
WITH ranked AS (
    SELECT id, pinned,
           row_number() OVER (
               ORDER BY (http_status IS NULL
                         OR http_status BETWEEN 200 AND 299) DESC,
                        version_seq DESC NULLS LAST, id DESC) AS rn
      FROM portal_raw_payloads
     WHERE source = %(source)s
       AND source_id_native = %(source_id_native)s
       AND page_kind = %(page_kind)s::location_page_kind
)
DELETE FROM portal_raw_payloads p
 USING ranked r
 WHERE p.id = r.id
   AND NOT r.pinned
   AND r.rn > %(version_cap)s::integer
RETURNING p.id, p.body_r2_key, p.byte_size,
          coalesce(p.stored_byte_size, octet_length(p.body))
"""

# An R2 key is content-addressed, so two rows in DIFFERENT groups that fetched
# byte-identical bodies (one portal's "listing removed" page under two listings) share
# one object. Handing an evicted row's key to W2a-5's deleter unfiltered would then
# destroy the body of a live row. Only keys no surviving row still points at are
# reported as reclaimable.
_ORPHANED_KEYS_SQL = """
SELECT DISTINCT k.key
  FROM unnest(%(keys)s::text[]) AS k(key)
 WHERE NOT EXISTS (SELECT 1 FROM portal_raw_payloads p
                    WHERE p.body_r2_key = k.key)
"""

def repin_group(
    cur: psycopg.Cursor,
    *,
    source: str,
    source_id_native: str,
    page_kind: str,
) -> None:
    """Recompute `pinned` authoritatively across one (listing, page_kind) group.

    * The ONE definition of pinned: first version, latest version.
    * Re-asserted on every append, which is the only moment the answer can move now
      that the scheduled prune sweep is gone.
    """
    cur.execute(_REPIN_SQL, {
        "source": source,
        "source_id_native": source_id_native,
        "page_kind": page_kind,
    })


def prune_group(
    cur: psycopg.Cursor,
    *,
    source: str,
    source_id_native: str,
    page_kind: str,
    version_cap: int,
) -> list[EvictedBody]:
    """Evict unpinned bodies ranked beyond the cap; returns what was removed.

    * Reads `pinned`, never recomputes it: call `repin_group` FIRST, in the same
      transaction, or the cap ranks against a stale pin set.
    """
    cur.execute(_PRUNE_SQL, {
        "source": source,
        "source_id_native": source_id_native,
        "page_kind": page_kind,
        "version_cap": version_cap,
    })
    return [EvictedBody(*row) for row in cur.fetchall()]


def orphaned_r2_keys(cur: psycopg.Cursor, keys: Sequence[str]) -> tuple[str, ...]:
    """Of `keys`, the ones no surviving payload row still points at."""
    if not keys:
        return ()
    cur.execute(_ORPHANED_KEYS_SQL, {"keys": list(keys)})
    return tuple(str(row[0]) for row in cur.fetchall())


# ------------------------------------------------------------------ body fetch width

# A sweep over the archived corpus is ~99 % fetching: measured 2026-09-08, one 2,000-page
# remax run spent 844 s wall-clock of which the DATABASE accounted for 1.4 s per 500-row
# batch — 0.7 % — and the rest was one R2 GET per page, in series. The readers therefore
# fetch a batch's bodies CONCURRENTLY, and the client's connection pool must be at least as
# wide or urllib3 discards connections and the pool becomes the new serialiser (the "pool is
# full" warnings the W2a backfill logged on all eleven of its runs). One knob sizes both, so
# they cannot drift apart.
BODY_FETCH_WORKERS_ENV = "LOCATION_BODY_FETCH_WORKERS"
DEFAULT_BODY_FETCH_WORKERS = 16
MAX_BODY_FETCH_WORKERS = 32


def body_fetch_workers() -> int:
    """How many archived bodies a lane fetches at once. Clamped to 1..32: 0 or less is a
    typo and takes the default, and the ceiling keeps one runner from opening a socket per
    row of a 5,000-body batch."""
    value = loader_db.env_positive_int(BODY_FETCH_WORKERS_ENV, DEFAULT_BODY_FETCH_WORKERS)
    return min(MAX_BODY_FETCH_WORKERS, value)


_STORE: ObjectStore | None = None


def open_store() -> ObjectStore | None:
    """The platform's R2 client, or None when R2 is not configured.

    None used to mean "keep the body inline instead", which was a safe default while
    spilling was the pathological path. With R2 as the BODIES' HOME that fallback would
    silently rebuild the database-resident archive the budget gate exists to refuse — one
    missing env var and ~29x the projected footprint lands in Postgres, invisibly. So the
    caller now REFUSES the write instead; see `append_payload`.
    """
    global _STORE
    if _STORE is None:
        from scraper import image_storage

        if not image_storage.is_configured():
            return None
        # Sized to the fetch width, never below it — see `body_fetch_workers`.
        _STORE = image_storage.R2Client.from_env(
            max_pool_connections=body_fetch_workers())
    return _STORE


def reset_store_cache() -> None:
    """Drop the cached R2 client (tests; a process that must re-read the env)."""
    global _STORE
    _STORE = None


def append_payload(
    conn: psycopg.Connection,
    *,
    source: str,
    source_id_native: str,
    page_kind: str,
    listing_id: int | None,
    body: bytes,
    content_type: str,
    http_status: int | None,
    contract_version: int | None,
    observed_at: datetime,
    snapshot_id: int | None = None,
    volatile: VolatileProfile | None = None,
    normalizer_version: str | None = None,
    version_cap: int | None = None,
    store: ObjectStore | None = None,
    statement_timeout_s: int | None = None,
) -> PayloadRef:
    """Archive one fetched body, appending only if its normalised content changed.

    `observed_at` is the PAYLOAD's own observation time and lands in
    `first_observed_at` AND `fetched_at` (06 Rule 1) — never `now()`, so a backfilled
    body keeps the time it was actually fetched instead of reading as having appeared
    on migration day.

    `snapshot_id` anchors the body to the `listing_snapshots` row it belongs to. None
    is the honest value for a fetch with no snapshot yet; a later anchored fetch of the
    same body fills it in.

    `volatile` None resolves the profile this portal's CONTRACT declares for this
    (source, page_kind) SURFACE — never for the source alone: `payload_sha256` is the
    archive's identity, so a detail profile mis-applied to an index body would bake
    a hash taken over the wrong projection into every claim re-mined from it.
    A surface no contract declares gets `payload_norm.BASE_PROFILE` and stamps
    `normalizer_version` with the `+base` suffix; a declared one stamps
    `+profile@<digest>` — a digest of the rules themselves, not the `contract_version`
    carrying them — so which instrument produced a row's content address is readable
    off the row, and an extraction-only bump does not pretend one moved.

    `normalizer_version` OVERRIDES the cohort stamp, and an explicit `volatile`
    REQUIRES one — the pair is refused otherwise. `normalizer_version` is a permanent
    column whose whole job is to say which projection produced this row's content
    address; derived from the resolver while the body was normalised under a
    caller-supplied profile, it would assert "this contract version's declaration
    was applied" about a row hashed under something else, and no reader could tell.
    The requirement is what makes a caller profile SAFE to offer at all. Overriding
    the stamp ALONE is allowed, and was the churn instrument's established shape:
    same profile, a caller-stated cohort (the confirmation probe's `+probe`).

    Retention (re-pin + cap) runs only when a row was actually appended: an unchanged
    refetch cannot have changed the group's membership, and paying two extra
    statements per fetch on the common path is exactly the cost this store exists to
    avoid. An append is now the ONLY event that can move a pin: the predicate is two
    version edges, and nothing outside this group can change either.

    `store` None resolves the platform's R2 client. WHERE A BODY NEEDS THE BUCKET AND
    THERE IS NONE, THIS RAISES. That is the whole degradation contract, and it is a
    refusal rather than a fallback on purpose: keeping the body inline instead — what
    this did while spilling was the rare path — would rebuild the database-resident
    archive the storage arithmetic says cannot fit, silently, one missing env var at a
    time. Failing is scoped to this one payload: `scraper.db.append_payload_if_enabled`
    catches everything, warns, and returns, so the walk and the drain are untouched and
    the listing is still scraped, snapshotted and served. On a fresh deploy and in CI the
    branch is unreachable rather than tolerated, because `payload_dual_write` is OFF per
    portal and nothing calls this at all; and a body small enough to stay inline archives
    normally with no R2 configured.
    """
    if not content_type:
        raise PayloadError("content_type is required — it decides how the body normalises")
    if volatile is not None and not normalizer_version:
        raise PayloadError(
            "an explicit volatile profile must be passed with the normalizer_version "
            "that names it: payload_sha256 is permanent and the stamp is the only "
            "record of which projection produced it")
    # Resolved as a pair, so the stamp always describes the profile that was actually
    # applied — including on the override path, where it is the caller's own.
    resolved = resolve_normalisation(source, page_kind)
    profile = volatile if volatile is not None else resolved.profile
    cohort = normalizer_version or resolved.normalizer_version
    norm = normalise(body, content_type=content_type, volatile=profile)

    cap = version_cap if version_cap is not None else loader_db.env_positive_int(
        VERSION_CAP_ENV, DEFAULT_VERSION_CAP)
    timeout_s = statement_timeout_s if statement_timeout_s is not None else (
        loader_db.env_timeout_s(WRITE_TIMEOUT_ENV, DEFAULT_WRITE_TIMEOUT_S))

    placement = plan_placement(source, body, norm.raw_sha256)
    if placement.spills:
        store = store if store is not None else open_store()
        if store is None:
            # THE UNCONFIGURED-R2 CONTRACT, and the one place it is decided. Refusing
            # here fails this ONE payload write and nothing else: every scraper reaches
            # this module through `scraper.db.append_payload_if_enabled`, which catches
            # everything, warns, and returns — so the walk and the drain are untouched
            # and the listing is still scraped, snapshotted and served. What the operator
            # gets instead of a silent 10 GB of TOASTed HTML is a warning per fetch.
            #
            # On a fresh deploy and in CI this branch is unreachable rather than
            # tolerated: `payload_dual_write` is OFF per portal by default, so with no R2
            # env vars nothing calls this at all. It becomes reachable only by enabling
            # the archive without configuring the store, which is a misconfiguration that
            # should be loud.
            raise PayloadError(
                f"payload archive needs R2 for {source}/{source_id_native} "
                f"({len(placement.stored)} stored bytes > threshold) but R2 is not "
                f"configured — set R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
                f"R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME, or raise "
                f"{R2_THRESHOLD_ENV} to keep bodies in Postgres deliberately")

    params: dict[str, Any] = {
        "source": source,
        "source_id_native": source_id_native,
        "listing_id": listing_id,
        "page_kind": page_kind,
        "payload_sha256": norm.norm_sha256,
        "body_sha256": norm.raw_sha256,
        "content_type": content_type,
        "content_encoding": placement.content_encoding,
        "body": None if placement.spills else placement.stored,
        "body_r2_key": placement.r2_key,
        "byte_size": norm.byte_size,
        "stored_byte_size": len(placement.stored),
        "http_status": http_status,
        "contract_version": contract_version,
        "normalizer_version": cohort,
        "snapshot_id": snapshot_id,
        "observed_at": observed_at,
    }
    group = {
        "source": source,
        "source_id_native": source_id_native,
        "page_kind": page_kind,
    }

    with loader_db.bounded(conn, timeout_s) as cur:
        cur.execute(_GROUP_LOCK_SQL, group)
        cur.execute(_APPEND_SQL, params)
        row = cur.fetchone()
        if row is None:
            # Unreachable: ON CONFLICT DO UPDATE always returns its row. Kept as an
            # assertion rather than an `int(None)` three lines down.
            raise PayloadError(
                f"payload append for {source}/{source_id_native} returned no row")
        payload_id, inserted = int(row[0]), bool(row[2])
        # NULL version_seq is W2a-4 substrate only (the append always computes one), and
        # 0 is the honest reading of "this body carries no version number".
        version_seq = 0 if row[1] is None else int(row[1])
        # The row as stored: on a collision, what an EARLIER fetch wrote. `stored_bytes`
        # is NULL only for a pre-406 spilled row, which carried neither the column nor an
        # inline body to measure.
        row_payload_sha256, row_body_sha256 = bytes(row[3]), bytes(row[4])
        row_byte_size = int(row[5])
        row_encoding, row_key = str(row[6]), row[7]
        row_stored_bytes = None if row[8] is None else int(row[8])

        # INSIDE the transaction and only for a genuinely new body. The ordering is the
        # asymmetry that makes the two failure modes safe:
        #
        #   * upload fails  -> the exception leaves `loader_db.bounded`'s
        #     `conn.transaction()`, which ROLLS BACK, so no metadata row can be committed
        #     pointing at an object that was never written. That direction matters: a
        #     metadata row pointing at a body that does not exist is exactly the
        #     unverifiability this store exists to end.
        #   * upload succeeds, transaction later fails -> an object nothing references.
        #     HARMLESS, and self-healing: the key is the hash of the bytes, so the next
        #     append of the same body writes the same key and adopts it.
        #
        # Doing it before the INSERT instead would swap those, trading a fatal orphan for
        # a harmless one — and it would PUT on every unchanged refetch too.
        if placement.spills and inserted and store is not None:
            store.upload_bytes(placement.r2_key or "", placement.stored,
                               "application/gzip")

        evicted_ids: tuple[int, ...] = ()
        evicted_keys: tuple[str, ...] = ()
        evicted_bytes = 0
        if inserted:
            repin_group(cur, **group)
            evicted = prune_group(cur, **group, version_cap=cap)
            evicted_ids = tuple(e.id for e in evicted)
            evicted_bytes = sum(
                e.stored_bytes for e in evicted if e.stored_bytes is not None)
            evicted_keys = orphaned_r2_keys(
                cur, [e.r2_key for e in evicted if e.r2_key])

    _record_decision(
        inserted=inserted, evicted_rows=len(evicted_ids), evicted_bytes=evicted_bytes)

    if evicted_ids:
        # The orphaned R2 objects are handed to W2a-5's pruner, which owns "report
        # bytes reclaimed"; deleting them here would need a delete verb this store
        # protocol deliberately does not have.
        LOG.info(
            "PAYLOAD evicted source=%s key=%s rows=%d r2=%d cap=%d",
            source, source_id_native, len(evicted_ids), len(evicted_keys), cap,
        )

    return PayloadRef(
        id=payload_id,
        # The five below come from the ROW, which on the insert path is what this
        # call wrote and on a collision is what an EARLIER fetch wrote.
        payload_sha256=row_payload_sha256,
        body_sha256=row_body_sha256,
        version_seq=version_seq,
        inserted=inserted,
        byte_size=row_byte_size,
        # Always the ROW's figure now that 406 records it: on the insert path the row
        # carries the size this call just wrote, so reading it back is not a second
        # source of truth but the same one, and it stops being None for a spilled body.
        stored_bytes=row_stored_bytes,
        content_encoding=row_encoding,
        body_r2_key=row_key,
        evicted_ids=evicted_ids,
        evicted_r2_keys=evicted_keys,
    )
