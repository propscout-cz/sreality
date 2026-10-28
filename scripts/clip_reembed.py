"""The re-embed proof (#2032 AC2): on this image, at the pinned revision, 100 stored CLIP vectors are re-computed
within fp32 tolerance, and so are their tags.

Run as `python -m scraper.job_runner request clip --by <who> -- --verify-reembed` (scripts/clip_tag_backfill.py
dispatches here). It reads a frozen sample in one READ ONLY transaction and writes nothing. Pass rules, all must hold:
P0 the sample is the frozen one, every object exists in R2, and it downloads within ROUNDS; P1 every vector within
L2_MAX of its stored one (float64); P2a the fresh and the stored vector give the same tags here; P2b the fresh tags
equal the stored rows (only while the taxonomy is the one that tagged them); P3 the loaded weights, the CPU build and
the image processor are production's. One CLIP_VERIFY line, after at most MAX_FAILURE_LINES failure lines; exit 0 on a
pass, else 1. `why=download` alone is inconclusive: re-request it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import logging
import math
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scraper import image_storage
from scraper.clip_tagger import load_taxonomy
from scraper.cpus import job_cpus
from scripts import clip_tag_backfill as ctb
from scripts import retag_from_embeddings as rte

if TYPE_CHECKING:
    import psycopg

    from scraper.clip_tagger import Tagger, TagResult

LOG = logging.getLogger("clip_reembed")

# Drawn read-only on 2026-10-27 (#2032 plan § 5), 12 per portal (maxima 4) from a pool of 123,445:
#   with c as (select i.id, l.source,
#     row_number() over (partition by l.source order by md5('w5-2032:'||i.id)) k
#    from images i join listings l on l.id=i.listing_id
#    join image_clip_embeddings e on e.image_id=i.id and e.model='openai/clip-vit-base-patch32'
#    where i.id between 96176548 and 309173217
#      and i.storage_path='img/'||i.listing_id||'/'||i.id||'.jpg'
#      and i.clip_tagged_at >= '2026-10-19' and i.clip_tagged_at < '2026-10-27 06:00+00'
#      and e.revision='3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268')
#   select id from c where k<=12 order by md5('w5-2032:'||id) limit 100;
# The rows cannot drift: the window is closed, the stamp is set once, a stamped vector is never rewritten and an
# `img/{listing_id}/{id}.jpg` key is never recomputed. A repair that clears one fails P0, which is right.
SAMPLE_IDS: tuple[int, ...] = (
    308879722, 308884962, 308891850, 308892870, 308894284, 308894315, 308894319, 308895784, 308906053, 308914858,
    308918092, 308919297, 308919453, 308921746, 308921933, 308922418, 308934340, 308935381, 308941169, 308942073,
    308943360, 308944233, 308944245, 308952711, 308973624, 308976506, 308978628, 308981383, 308981393, 308983092,
    308984122, 308985866, 308986849, 308987116, 308987344, 308987374, 308988079, 308988945, 308989414, 308990096,
    308990647, 308995448, 309002817, 309010814, 309014325, 309014341, 309015011, 309015620, 309015697, 309015938,
    309016584, 309016854, 309017124, 309023365, 309024371, 309025348, 309025407, 309027662, 309028139, 309031172,
    309033867, 309033911, 309036532, 309038593, 309038617, 309041031, 309041220, 309044653, 309050131, 309050260,
    309057707, 309057950, 309059282, 309062250, 309070919, 309073606, 309076355, 309079904, 309080632, 309083410,
    309083415, 309085774, 309088906, 309091751, 309103273, 309104949, 309107048, 309110810, 309118578, 309121107,
    309132901, 309134281, 309135065, 309135094, 309137453, 309139890, 309145155, 309148035, 309152699, 309168834,
)
IDS_MD5 = "a037de1cf09ec23fc5baa610de55508a"  # Postgres's md5(string_agg(id::text, ',' order by id))
WINDOW = (dt.datetime(2026, 10, 19, tzinfo=dt.UTC), dt.datetime(2026, 10, 27, 6, tzinfo=dt.UTC))
PIN = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
# The hub's LFS oid of pytorch_model.bin at PIN (605,247,071 B); the revision ships no safetensors.
WEIGHTS_OID = "a63082132ba4f97a80bea76823f544493bffa8082296d62d71581a4feff1576f"
# The taxonomy that tagged the sample (prompts last changed 2026-06-26, ab2691d87); see _taxonomy_digest.
# The render anchors left the digest with #2151: they never touched the room softmax the sample's tags come from.
TAXONOMY_KEYS = ("model", "revision", "prompts", "collapse")
TAXONOMY_SHA256 = "fd5cd3c6cb30357f5497702c55cce7976a22e8452120c8f1590ec25f58e194ad"
# Production against itself (7,332 same-photo pairs on one stack): non-zero L2 max 6.41e-6, never 1e-5.
L2_MAX = 1e-5
# Stored confidence is rounded to 4 decimals, and stored as real.
TAG_TOL = 1e-4 + 1e-6
ROUNDS = 3
MAX_FAILURE_LINES = 10
WHY_ORDER = ("sample", "identity", "vector", "tags", "stored_tags", "download")

_READ_SQL = """
    SELECT i.id, i.listing_id, i.storage_path, i.clip_tagged_at, e.revision, e.embedding::text,
           t.fine_tag, t.logical_tag, t.confidence
    FROM images i
    LEFT JOIN image_clip_embeddings e ON e.image_id = i.id AND e.model = %(model)s
    LEFT JOIN image_clip_tags t ON t.image_id = i.id AND t.model = %(model)s
    WHERE i.id = ANY(%(ids)s)
    ORDER BY i.id
"""


def r2_client(workers: int) -> image_storage.R2Client:
    """botocore's default timeouts and retries, not the job's short ones: a slow GET is no verdict here."""
    return image_storage.R2Client.from_env(max_pool_connections=workers + 4)


def _md5(ids: list[int] | tuple[int, ...]) -> str:
    return hashlib.md5(",".join(map(str, ids)).encode()).hexdigest()


def _taxonomy_digest(tax: dict) -> str:
    return hashlib.sha256(json.dumps({k: tax.get(k) for k in TAXONOMY_KEYS}, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _metrics(stored: list[float], fresh: list[float]) -> tuple[float, float, float]:
    """(L2, max |delta|, normalized cosine) in float64. Stored vectors are not unit at this precision (|norm - 1| up
    to 1e-6), so a raw dot product would read about 5e-7 low."""
    deltas = [f - s for s, f in zip(stored, fresh)]
    dot = sum(s * f for s, f in zip(stored, fresh))
    norms = math.sqrt(sum(s * s for s in stored)) * math.sqrt(sum(f * f for f in fresh))
    return (math.sqrt(sum(d * d for d in deltas)), max((abs(d) for d in deltas), default=0.0),
            dot / norms if norms else 0.0)


def _cache_path(model: str, filename: str, revision: str) -> str | None:
    from huggingface_hub import try_to_load_from_cache  # the clip extra's dependency

    found = try_to_load_from_cache(model, filename, revision=revision)
    return found if isinstance(found, str) else None


def _torch_facts() -> dict[str, Any]:
    import torch

    return {"cuda": torch.version.cuda, "isa": torch.backends.cpu.get_cpu_capability()}


def _importable(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


class _Failures:
    """Every failure by kind; at most MAX_FAILURE_LINES of them logged, so the summary stays in the log_tail."""

    def __init__(self) -> None:
        self.kinds: dict[str, int] = {}
        self._logged = 0

    def add(self, kind: str, detail: str) -> None:
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if self._logged < MAX_FAILURE_LINES:
            self._logged += 1
            # Not "CLIP_VERIFY ": the gate's regex takes the first such line, which must be the summary.
            LOG.warning("CLIP_VERIFY_FAIL %s %s", kind, detail)


def _read(conn: psycopg.Connection, model: str) -> dict[int, tuple[Any, ...]]:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(_READ_SQL, {"model": model, "ids": list(SAMPLE_IDS)})
        return {row[0]: row for row in cur.fetchall()}


def _check_sample(rows: dict[int, tuple[Any, ...]], failures: _Failures) -> None:
    if _md5(sorted(rows)) != IDS_MD5:
        failures.add("sample", f"ids_md5={_md5(sorted(rows))} not {IDS_MD5}")
    for image_id in SAMPLE_IDS:
        row = rows.get(image_id)
        if row is None:
            failures.add("sample", f"image={image_id} no row")
            continue
        _id, listing_id, key, stamp, revision, embedding, fine = row[:7]
        if key != f"img/{listing_id}/{image_id}.jpg":
            failures.add("sample", f"image={image_id} key={key}")
        elif stamp is None or not WINDOW[0] <= stamp < WINDOW[1]:
            failures.add("sample", f"image={image_id} stamp={stamp}")
        elif revision != PIN or embedding is None:
            failures.add("sample", f"image={image_id} revision={revision}")
        elif fine is None:
            failures.add("sample", f"image={image_id} no tag row")


def _download(r2: image_storage.R2Client, keys: dict[int, str], workers: int) -> tuple[dict[int, bytes], list[int]]:
    """Up to ROUNDS rounds, each retrying the GETs the round before failed. (bytes by id, still missing)."""
    got: dict[int, bytes] = {}
    left = sorted(keys)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for _ in range(ROUNDS):
            if not left:
                break
            futures = {image_id: pool.submit(r2.download_bytes, keys[image_id]) for image_id in left}
            left = []
            for image_id, future in futures.items():
                try:
                    got[image_id] = future.result()
                except Exception:  # noqa: BLE001 - a transient GET: the next round retries it
                    left.append(image_id)
    return got, left


def _gone(r2: image_storage.R2Client, key: str) -> bool:
    """True only when R2 says the object does not exist: no re-request can fetch it."""
    try:
        return r2.object_size(key) is None
    except Exception:  # noqa: BLE001 - R2 failing the HEAD too is a shortfall, not a gone object
        return False


def _identity(tagger: Tagger, model: str, failures: _Failures) -> dict[str, str]:
    """P3: the revision that actually loaded (config._commit_hash; tagger.revision is only the file's string), the
    cached files it came from, a CPU torch and the slow PIL image processor (no torchvision: the fast one changes
    every vector)."""
    commit = str(getattr(getattr(tagger._model, "config", None), "_commit_hash", None))
    processor = type(getattr(tagger._processor, "image_processor", tagger._processor)).__name__
    config = _cache_path(model, "config.json", PIN)
    weights_path = _cache_path(model, "pytorch_model.bin", PIN)
    blob = Path(os.path.realpath(weights_path)) if weights_path else None
    facts = _torch_facts()
    if commit != PIN:
        failures.add("identity", f"commit={commit} pin={PIN}")
    if not config or Path(config).parent.name != PIN or Path(config).parent.parent.name != "snapshots":
        failures.add("identity", f"config.json={config} not under snapshots/{PIN}/")
    if blob is None or blob.parent.name != "blobs" or blob.name != WEIGHTS_OID:
        failures.add("identity", f"weights={blob}")
    if facts["cuda"] is not None:
        failures.add("identity", f"cuda={facts['cuda']}")
    if processor != "CLIPImageProcessor":
        failures.add("identity", f"processor={processor}")
    if _importable("torchvision"):
        failures.add("identity", "torchvision importable")
    return {"commit": commit[:12], "weights": blob.name[:12] if blob else "none", "processor": processor,
            "isa": str(facts["isa"])}


def _stored_tags_differ(fresh: TagResult, row: tuple[Any, ...]) -> bool:
    fine, logical, confidence = row[6:9]
    if (fresh.fine_tag, fresh.logical_tag) != (fine, logical):
        return True
    return confidence is None or abs(fresh.confidence - float(confidence)) > TAG_TOL


def _summary(passed: bool, why: str, facts: dict[str, Any]) -> int:
    LOG.info("CLIP_VERIFY " + " ".join(f"{k}={v}" for k, v in {"pass": str(passed).lower(), "why": why,
                                                                 **facts}.items()))
    return 0 if passed else 1


def verify(conn: psycopg.Connection, args: argparse.Namespace, *, r2_factory: Callable[[], image_storage.R2Client],
           tagger_factory: Callable[[int], Tagger], clock: Callable[[], float] = time.monotonic) -> int:
    """The whole check; 0 on a pass, else 1. Reads in one READ ONLY transaction and writes nothing."""
    if not image_storage.is_configured():
        return _summary(False, "r2", {"n": 0, "ids_md5": _md5(SAMPLE_IDS)})
    taxonomy = load_taxonomy()
    model = taxonomy["model"]
    failures = _Failures()
    rows = _read(conn, model)
    _check_sample(rows, failures)

    started = clock()
    # A row with no stored vector already failed P0 and has nothing to be compared with.
    keys = {i: r[2] for i, r in rows.items() if r[2] and r[5] is not None}
    r2 = r2_factory()
    got, missing = _download(r2, keys, args.workers)
    secs_download = clock() - started
    for image_id in missing:
        if _gone(r2, keys[image_id]):
            failures.add("sample", f"image={image_id} object gone")
        else:
            failures.add("download", f"image={image_id} rounds={ROUNDS}")
    decoded: list[tuple[int, Any]] = []
    for image_id in sorted(got):
        try:
            decoded.append((image_id, ctb._decode(got.pop(image_id))))
        except Exception as exc:  # noqa: BLE001 - it decoded when it was tagged: the decoder differs
            failures.add("vector", f"image={image_id} undecodable: {type(exc).__name__}")

    threads = args.threads or job_cpus()
    tagger = tagger_factory(threads)
    identity = _identity(tagger, model, failures)
    started = clock()
    emb = tagger.embed([img for _, img in decoded], args.batch_size) if decoded else []
    secs_embed = clock() - started
    ids = [image_id for image_id, _ in decoded]
    del decoded

    stored = {i: rte._parse_vec(rows[i][5]) for i in ids}
    fresh_tags = tagger.tags_from_emb(emb) if ids else []
    stored_emb_tags = tagger.tags_from_emb(rte._as_tensor([stored[i] for i in ids])) if ids else []
    check_stored = _taxonomy_digest(taxonomy) == TAXONOMY_SHA256
    max_abs = max_l2 = 0.0
    min_cos = 1.0
    exact = tags_equal = stored_equal = 0
    for k, image_id in enumerate(ids):
        fresh = rte._parse_vec(ctb._vec_str(emb[k]))  # the 6-decimal text production stores: like with like
        l2, worst, cos = _metrics(stored[image_id], fresh)
        max_l2, max_abs, min_cos = max(max_l2, l2), max(max_abs, worst), min(min_cos, cos)
        exact += l2 == 0.0
        if l2 > L2_MAX:
            failures.add("vector", f"image={image_id} l2={l2:.3g} max_abs={worst:.3g}")
        a, b = fresh_tags[k], stored_emb_tags[k]
        if (a.fine_tag, a.logical_tag) == (b.fine_tag, b.logical_tag):
            tags_equal += 1
        else:
            failures.add("tags", f"image={image_id} fresh={a.fine_tag}:{a.confidence} "
                                 f"stored={b.fine_tag}:{b.confidence}")
        if check_stored:
            if _stored_tags_differ(a, rows[image_id]):
                row = rows[image_id]
                failures.add("stored_tags", f"image={image_id} fresh={a.fine_tag}/{a.logical_tag}/{a.confidence} "
                                            f"stored={row[6]}/{row[7]}/{row[8]}")
            else:
                stored_equal += 1

    why = next((kind for kind in WHY_ORDER if failures.kinds.get(kind)), "ok")
    return _summary(why == "ok", why, {
        "n": len(rows), "ids_md5": _md5(sorted(rows)), "missing": len(missing), "max_abs": f"{max_abs:.3g}",
        "max_l2": f"{max_l2:.3g}", "min_cos64": f"{min_cos:.15g}", "exact_rows": exact, "tags_equal": tags_equal,
        "stored_tags_equal": stored_equal if check_stored else "skipped", "secs_download": f"{secs_download:.1f}",
        "secs_embed": f"{secs_embed:.1f}", "img_per_s": f"{len(ids) / secs_embed:.2f}" if secs_embed > 0 else "0",
        "torch": ctb._version("torch"), "transformers": ctb._version("transformers"),
        "pillow": ctb._version("Pillow"), "numpy": ctb._version("numpy"), "isa": identity["isa"],
        "cpu": f'"{_cpu_model()}"', "threads": threads, "processor": identity["processor"],
        "commit": identity["commit"], "weights": identity["weights"],
        "replica": os.environ.get("RAILWAY_REPLICA_ID", "")[:8] or "none",
        "version": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:9] or "none",
    })
