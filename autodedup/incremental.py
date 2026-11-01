"""The real-time SHADOW lane: the batch engine's decisions, made one arrival at a time.

The batch lane proves accuracy over a whole cohort; production has to reach the SAME state
incrementally. Nothing here re-implements a decision: fingerprints come from
`fingerprint.build_fingerprint`, features from `features.pair_features`, the zone from
`decide.decide_pair` and the groups from `cluster.cluster_pairs`. What this module adds is
the three mechanisms a cohort pass gets for free and an arrival does not.

**E70 — the calibration is FROZEN per generation.** Six of the engine's inputs are read off
the cohort rather than off the pair: the price deciles K3 blocks on, the exploded-key set,
the block and corpus document frequencies behind `tfidf`/`rare_token_overlap`, the in-block
attribute frequencies behind `attr_rarity`, the exact-pin population, and E60's certifying
code set. Left live they would make a pair's score a function of WHEN it was scored, so a
re-run would silently re-decide yesterday's pairs. They are computed once, stored, and a
generation runs under them until an operator cuts a new one — which is a new generation.

**E71 — an arrival re-probes its whole probe-key NEIGHBOURHOOD, so the final state is a pure
function of the corpus.** `BlockIndex.candidates` caps fan-out at `max_candidates_per_listing`
(297 of g6's 5,687 listings sit at the cap), and a cap makes retrieval depend on what was
present when it ran. The fix is not a bigger cap: when a listing's keys change, every listing
that PROBES one of those keys has its retrieval recomputed too, and a pair is kept when either
side retrieves the other (`from_lo` / `from_hi`). The neighbourhood is the ±1 band/decile
relation, which is symmetric, so it is exactly the set `probe_keys` reaches.

**E72 — clustering is recomputed per CONNECTED COMPONENT, not per edge.** Union-find over an
edge stream is order-dependent; `cluster_pairs` over a component's whole edge set is not,
because every rule it applies — the certificate-first order, `cluster_invariants_ok` on the
merged member set, E37's bridge refusal and E57's second offer — reads only members of the two
clusters being joined, and those never leave the component. So the component is the unit of
work, and its result is identical to the cohort pass's.

Everything else is what W9's verification found missing, and each of those is a rule too:
a watermark over FOUR bounded feeds, none of which is monotone on its own (E73); a pass bounded
in statements and not only in listings (E74); a pass that REFUSES a claim it cannot fit rather
than truncating it, and writes nothing until it has agreed to it (E75); a generation seeded at
the present (E76); a store that reads a decision back exactly as it was taken (E77); and an
operator refusal reconciled on every pass, idle ones included (E78). Nothing written leaves
schema `autodedup` (D4).
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Container, Iterable, Mapping, Protocol, Sequence

from autodedup.blocking import _DECILES as DECILES
from autodedup.blocking import QUARTER_PROBE, TOWN_PROBE, BlockIndex
from autodedup.cluster import cluster_pairs, cluster_rows
from autodedup.dataset import Image, Listing
from autodedup.decide import Decision, decide_pair
from autodedup.features import (
    FEATURE_VERSION,
    FeatureContext,
    Feats,
    _block_of,
    _tfidf_vector,
    MIN_RARE_BLOCK_DOCS,
    pair_features,
    vocabulary_attr_keys,
)
from autodedup.features import _attr_map as _feature_attr_map
from autodedup.fingerprint import Fingerprint, build_fingerprint
from autodedup.d43 import relation_for
from autodedup.guards import (
    UNIT_DESIGNATOR_VETO, GuardSide, exemption_window_quota, in_exemption_window, pair_veto,
)
from autodedup.hazard_context import BlockCell, ContextIndex, ContextStamp, rail_plan
from autodedup.hazard_context import address_block_key, category_group
from autodedup.model import LogisticModel
from autodedup.indistinguishable import FEATURE_SLOTS
from autodedup.settings import Settings
from autodedup.store_score import storable
from autodedup.text_facts import reference_codes, states_from_price

GENERATION: str = "rt"

# THE LIVE STREAM (W5, E914). `rt` is what production reads — the reconcile's input and the
# review pages' unnamed generation — only once a seed of THIS design built it and its build
# phase ended. `rt_seed` writes `rt_seed_version:<generation>` = SEED_VERSION next to
# `rt_bootstrap:<generation>` = true; the pass that empties the backlog writes the latter
# false. The 09-21 `rt` was seeded before F2 and carries no version row, so it is never
# reconciled from and never shown as the default. A key in `autodedup.settings`, not a column
# of the calibration row, because the calibration re-cuts itself (A10) and the version must
# survive that. Bump it when a change makes an existing `rt` unfit to merge from: E936's
# quarter probe moved the keys, and a store seeded before it holds no quarter posting and no
# quarter explosion set.
SEED_VERSION: str = "e936"
SEED_VERSION_SETTING: str = "rt_seed_version"
BOOTSTRAP_SETTING: str = "rt_bootstrap"


def seed_version_key(generation: str = GENERATION) -> str:
    return f"{SEED_VERSION_SETTING}:{generation}"


def bootstrap_key(generation: str = GENERATION) -> str:
    return f"{BOOTSTRAP_SETTING}:{generation}"


def setting_flag(value: Any) -> bool:
    """A settings row read as a boolean: `true`, `True` and `"true"` all mean on; anything
    else — a missing row included — means off."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def seed_current(control: Mapping[str, Any], generation: str = GENERATION) -> bool:
    """A seed of this design (SEED_VERSION) built the generation."""
    return str(control.get(seed_version_key(generation)) or "") == SEED_VERSION


def stream_live(control: Mapping[str, Any], generation: str = GENERATION) -> bool:
    """The generation is THE live stream: seeded at SEED_VERSION and out of its build."""
    return (seed_current(control, generation)
            and not setting_flag(control.get(bootstrap_key(generation))))


# A `(probe, key)` tuple flattened to one text token, so the posting list is a two-column
# index lookup in SQL and the same string in the in-memory twin. Unit separator, because a
# street key may hold anything a portal prints but never a control character.
_SEP: str = "\x1f"
# E960: the dirty listings whose posted keys a pass holds at once while it retrieves them.
KEY_CHUNK: int = 5000
# The probes whose tokens carry a band number (`simhash_bands`): text, and phash per anchor.
_BANDED: frozenset[str] = frozenset({"text", "phash"})


def key_token(key: Sequence[Any]) -> str:
    return _SEP.join("" if part is None else str(part) for part in key)


def band_of(token: str) -> int:
    """The band number of a text or phash token: its second-last part, whether or not
    `content_key_town_grain` put the town before it."""
    return int(token.rsplit(_SEP, 2)[-2])


def _cut_key(cat_group: str | None, category_type: str | None) -> str:
    return key_token((cat_group, category_type))


@dataclass(slots=True, frozen=True)
class GuardRow:
    """The columns `pair_veto` reads, served from `rt_fp` without a gallery fetch.

    Satisfies `guards.GuardSide` structurally, which is why the rule floor can refuse a
    candidate before the fan-out cap spends a slot on it (E17) without building its
    fingerprint — the whole point of keeping retrieval cheap."""

    listing_id: int
    category_main: str | None
    category_type: str | None
    area_m2: float | None
    disposition: str | None
    floor: int | None
    # NULL on a row written before migration 677: the basis exemption does not fire for it.
    source: str | None = None
    area_basis: str | None = None


def guard_row(fp: Fingerprint) -> GuardRow:
    return GuardRow(fp.listing_id, fp.category_main, fp.category_type, fp.area_m2,
                    fp.disposition, fp.floor, fp.source, fp.area_basis)


@dataclass(slots=True, frozen=True)
class Evidence:
    """The PHOTOGRAPHIC evidence a stored decision rested on, as four counts (E92).

    A real-time arrival is claimed 5–15 minutes after `first_seen_at`; its images land about a
    minute in, but `phash` is written by an hourly job and the CLIP vector and tags by another
    (measured p50 2.47 h, p90 4.51 h to a first tag). So ~80% of arrivals are decided with
    every `phash` NULL, and nothing in W9's six feeds was keyed on a hash or a vector
    ARRIVING — the listing was decided once, on photographs that were not evidence yet, and
    never looked at again. These counts are what the evidence sweep compares a bounded probe
    of `public.images` against."""

    n_images: int = 0
    n_phash: int = 0
    n_clip: int = 0
    n_tags: int = 0

    @property
    def complete(self) -> bool:
        """Every photograph carries every signal the decision reads: the pHash, the CLIP vector
        and its tags (D901: the tags stay while their contribution is not negligible). This is
        what E93 holds a merge for (F3, E908) — a gallery hashed but not yet embedded is still
        evidence the engine does not have."""
        return self.n_images <= 0 or (self.n_phash >= self.n_images
                                      and self.n_clip >= self.n_images
                                      and self.n_tags >= self.n_images)

    def to_json(self) -> dict[str, int]:
        return {"n_images": self.n_images, "n_phash": self.n_phash,
                "n_clip": self.n_clip, "n_tags": self.n_tags}


def evidence_of(images: Sequence[Image]) -> Evidence:
    """What a gallery's producers have delivered, counted off the facts the pass just read."""
    return Evidence(
        n_images=len(images),
        n_phash=sum(1 for img in images if img.phash is not None),
        n_clip=sum(1 for img in images if img.clip is not None),
        n_tags=sum(1 for img in images if img.tags),
    )


# The reason a MERGE wears while it waits for photographs, and the certificates that need
# none: K-B is the re-post (one portal, one broker, one body, disjoint live windows) and K-R
# is a shared agency order code. Both are text-and-clock facts, so a pass holds neither.
EVIDENCE_HOLD_REASON: str = "evidence_pending"
PHOTO_FREE_CERTIFICATES: frozenset[str] = frozenset({"K-B", "K-R"})


@dataclass(slots=True, frozen=True)
class EvidenceHold:
    """E93: do not act on evidence you do not have yet.

    A merge that rests on photographs is HELD in the band — reason `evidence_pending` — while
    either side's gallery is not yet COMPLETE (a photograph without its pHash, its CLIP vector
    or its tags; F3, E908), and only while that side is still inside the horizon the producers
    are expected to answer within. Rejects are never held: a
    pair the engine refuses on text, price or geometry is refused on evidence it HAS.

    The hold is a LATENCY policy and not a decision one, which is why it is an argument rather
    than a setting: the batch engine has no clock, so a pass given no hold behaves exactly as
    W9's did and the replay-equivalence proof stays exact. What the hold changes is WHEN the
    state is reached, never which state — the horizon expires, the pair is re-decided, and the
    withheld-evidence replay is what proves the final state is still the batch engine's."""

    now: float
    horizon_s: float

    def young(self, first_decided_at: float | None) -> bool:
        """A listing this generation has never decided is young by definition — this pass IS
        its first decision."""
        if first_decided_at is None:
            return True
        return (self.now - float(first_decided_at)) < self.horizon_s

    def holds(self, evidence: Evidence | None, first_decided_at: float | None) -> bool:
        return bool(evidence is not None and not evidence.complete
                    and self.young(first_decided_at))


@dataclass(slots=True, frozen=True)
class FpRow:
    """What the store keeps about ONE listing: the guard columns, the re-score digest, the
    census cell it is counted in and the activity flag.

    The cell is stored rather than re-derived because a listing that MOVES (a resolved location,
    a category correction) must unbump the cell it LEFT — derive it from today's facts and the
    old cell keeps a phantom member forever, and `n_listings` is the exact counter
    `fungible_catalogue` and E64's rail read. `is_active` is stored for the same reason in the
    other direction: it is what the revive sweep anti-joins the live flag against."""

    guard: GuardRow
    digest: str
    cell_key: str
    cell_group: str
    is_active: bool
    # What this generation's stored decision rested on photographically, and when it was FIRST
    # taken (E92). Both are written by the store, never fabricated by a pass: the SQL store
    # stamps `first_decided_at` with the server's clock and coalesces it on every later
    # refresh, and the twin does the same with its own.
    evidence: Evidence = Evidence()
    first_decided_at: float | None = None
    # The listing's `decision_digest` as the lane last read its facts (E960): the reject ledger's
    # key for a side the pass does not read. Empty on a row written before migration 604.
    dd: str = ""
    # The `block_key` its postings were keyed under: the lane's drift sweep re-claims the row
    # when today's location gives another (A). None on a row written before migration 677.
    block_key: str | None = None

    @property
    def cell(self) -> tuple[str, str]:
        return (self.cell_key, self.cell_group)


@dataclass(slots=True)
class PairRow:
    """One stored pair of the rolling generation, plus the two bookkeeping bits E71 needs."""

    lo: int
    hi: int
    probes: list[str]
    from_lo: bool
    from_hi: bool
    zone: str
    score: float
    families: list[str]
    certificate: str | None
    veto: str | None
    reason: str
    evidence: dict[str, str]
    context: dict[str, Any]
    fp_lo: str
    fp_hi: str
    # The feature vector this decision was taken on, carried only on the way OUT: the store
    # writes it in the score lane's own `{name: [value, present]}` shape (E12) so the pair
    # page reads one thing whichever lane wrote it, and a row re-read from the store carries
    # none, which is why the upsert coalesces rather than overwrites.
    feats: Feats | None = None
    # The three feature slots the D43 cluster relation reads (F2, E909): carried on the way out
    # AND read back, because the lane clusters with the relation the batch pass clusters with.
    slots: dict[str, tuple[float, bool]] | None = None
    # Each side's `decision_digest`, carried only on the way OUT: the reject ledger (E943) keys
    # a pair decided below `store_floor` on them, and a row re-read from the store carries none.
    dd_lo: str = ""
    dd_hi: str = ""

    def decision(self) -> Decision:
        return Decision(self.lo, self.hi, self.zone, self.score, set(self.families),
                        self.certificate, self.veto, self.reason, dict(self.evidence))


@dataclass(slots=True)
class CellRow:
    """The live half of the census (E64): a counter plus three capped sets.

    `n_listings` is exact — it is the only member `fungible_catalogue` and the rail read. The
    three cardinalities are REPORTED only (`is_shape_stack` is inert by W8's verification), so
    their sets are capped and the row says when a cap bound rather than pretending precision."""

    key: str
    category_group: str
    n_listings: int = 0
    shapes: list[str] = field(default_factory=list)
    brokers: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    capped: bool = False

    def cell(self) -> BlockCell:
        return BlockCell(self.key, self.category_group, self.n_listings, len(self.shapes),
                         len(self.brokers), len(self.source_ids))


SET_CAP: int = 256


# ---------------------------------------------------------------------------- calibration


@dataclass(slots=True)
class Calibration:
    """E70: every cohort-relative input of a decision, frozen for one generation."""

    generation: str
    feature_version: int
    built_at: str
    n_listings: int
    price_cuts: dict[str, list[float]] = field(default_factory=dict)
    exploded: dict[str, list[str]] = field(default_factory=dict)
    block_docs: dict[str, int] = field(default_factory=dict)
    block_token_df: dict[str, dict[str, int]] = field(default_factory=dict)
    corpus_docs: int = 0
    corpus_token_df: dict[str, int] = field(default_factory=dict)
    block_attr_df: dict[str, dict[str, int]] = field(default_factory=dict)
    pin_pop: dict[str, int] = field(default_factory=dict)
    certifying_codes: list[str] = field(default_factory=list)
    # E944: `digest()` hashed once. A pass hashes it three to seven times and the lane keeps one
    # calibration across passes; nothing writes into one after it is built (`replace` makes a
    # new one, with no digest yet).
    _digest: str | None = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def build(
        cls,
        fps: Mapping[int, Fingerprint],
        listings: Mapping[int, Listing],
        settings: Settings,
        generation: str = GENERATION,
    ) -> "Calibration":
        """Read the six cohort statistics off a seed cohort — the batch pass's own inputs."""
        index = BlockIndex(settings)
        for listing_id in sorted(fps):
            index.add(fps[listing_id])
        index.finalize()
        ctx = FeatureContext.build(fps, settings, None)
        ctx.index_reference_codes(listings)
        ctx.index_attrs(fps, listings)
        certifying: set[str] = set()
        for codes in ctx.codes.values():
            certifying |= set(codes)
        return cls(
            generation=generation,
            feature_version=FEATURE_VERSION,
            built_at=_now(),
            n_listings=len(fps),
            price_cuts={_cut_key(*key): list(cuts) for key, cuts in index.price_cuts.items()},
            exploded={probe: sorted(key_token(key) for key in keys)
                      for probe, keys in index.exploded.items()},
            block_docs=dict(ctx.block_docs),
            block_token_df={block: dict(df) for block, df in ctx.block_token_df.items()},
            corpus_docs=ctx.corpus_docs,
            corpus_token_df=dict(ctx.corpus_token_df),
            block_attr_df={
                block: {key_token(pair): count for pair, count in bucket.items()}
                for block, bucket in ctx.block_attr_df.items()
            },
            pin_pop=dict(ctx.pin_pop),
            certifying_codes=sorted(certifying),
        )

    def digest(self) -> str:
        """A CONTENT hash of the frozen inputs — a pass stamps it so a decision can never be
        read back under a calibration it was not taken under.

        `built_at` is excluded deliberately: it says WHEN the calibration was cut, not what it
        contains, and a digest that moved every time the same cohort was re-cut would certify
        nothing. Two identical calibrations must hash identically or E70's stamp is decoration."""
        if self._digest is None:
            payload = dict(self.to_json())
            payload.pop("built_at", None)
            self._digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:16]
        return self._digest

    def to_json(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "feature_version": self.feature_version,
            "built_at": self.built_at,
            "n_listings": self.n_listings,
            "price_cuts": self.price_cuts,
            "exploded": self.exploded,
            "block_docs": self.block_docs,
            "block_token_df": self.block_token_df,
            "corpus_docs": self.corpus_docs,
            "corpus_token_df": self.corpus_token_df,
            "block_attr_df": self.block_attr_df,
            "pin_pop": self.pin_pop,
            "certifying_codes": self.certifying_codes,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "Calibration":
        return cls(
            generation=str(raw.get("generation") or GENERATION),
            feature_version=int(raw.get("feature_version") or FEATURE_VERSION),
            built_at=str(raw.get("built_at") or ""),
            n_listings=int(raw.get("n_listings") or 0),
            price_cuts={k: [float(v) for v in vals]
                        for k, vals in (raw.get("price_cuts") or {}).items()},
            exploded={k: list(vals) for k, vals in (raw.get("exploded") or {}).items()},
            block_docs=dict(raw.get("block_docs") or {}),
            block_token_df={k: dict(v) for k, v in (raw.get("block_token_df") or {}).items()},
            corpus_docs=int(raw.get("corpus_docs") or 0),
            corpus_token_df=dict(raw.get("corpus_token_df") or {}),
            block_attr_df={k: dict(v) for k, v in (raw.get("block_attr_df") or {}).items()},
            pin_pop=dict(raw.get("pin_pop") or {}),
            certifying_codes=list(raw.get("certifying_codes") or []),
        )


class Keyer:
    """`index_keys` / `probe_keys` under a frozen calibration.

    It IS `BlockIndex` — the six probes are never spelled twice — with the two cohort-derived
    structures injected instead of computed: `price_cuts` (K3's decile) and `exploded`."""

    def __init__(self, settings: Settings, calibration: Calibration) -> None:
        index = BlockIndex(settings)
        cuts: dict[tuple[str | None, str | None], list[float]] = {}
        for token, values in calibration.price_cuts.items():
            left, _, right = token.partition(_SEP)
            cuts[(left or None, right or None)] = list(values)
        index.price_cuts = cuts
        index.exploded = {probe: set(calibration.exploded.get(probe, ()))
                          for probe in index.probes}
        index._keys_ready = True  # the cuts are injected, so key derivation is ready
        self.index = index
        self.settings = settings

    def index_keys(self, fp: Fingerprint) -> list[tuple[str, str]]:
        return [(probe, key_token(key)) for probe, key in self.index.index_keys(fp)]

    def probe_keys(self, fp: Fingerprint) -> list[tuple[str, str]]:
        return [(probe, key_token(key)) for probe, key in self.index.probe_keys(fp)]

    def probe_keys_of(self, posted: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        """`probe_keys` of a listing known only by the keys it is POSTED under (E960), in
        `probe_keys`' order: every probe posts one key, or one band a band number — except
        `phash` over two anchor images or more, whose bands follow the images, which no posting
        keeps (`retrieve_posted` reads that probe loose)."""
        posted = sorted(set(posted), key=lambda key: (
            key[0], band_of(key[1]) if key[0] in _BANDED else 0, key[1]))
        quarter = any(probe == QUARTER_PROBE for probe, _token in posted)
        out: list[tuple[str, str]] = []
        for probe, token in posted:
            head, _sep, last = token.rpartition(_SEP)
            if probe in ("attr_area", TOWN_PROBE, QUARTER_PROBE):
                # E936: an ad that posts its quarter does not probe its town.
                if probe == TOWN_PROBE and quarter:
                    continue
                out.extend((probe, f"{head}{_SEP}{int(last) + offset}") for offset in (-1, 0, 1))
            elif probe == "broker" and last:
                out.extend((probe, f"{head}{_SEP}{decile}")
                           for decile in range(int(last) - 1, int(last) + 2)
                           if 0 <= decile < DECILES)
            else:
                out.append((probe, token))
        return out

    def is_exploded(self, probe: str, token: str) -> bool:
        return token in self.index.exploded[probe]

    def exploded_probes(self, fp: Fingerprint) -> set[str]:
        return {probe for probe, token in self.index_keys(fp) if self.is_exploded(probe, token)}


def context_for(
    calibration: Calibration,
    settings: Settings,
    fps: Mapping[int, Fingerprint],
    listings: Mapping[int, Listing],
) -> FeatureContext:
    """A `FeatureContext` over the WORKING SET whose cohort statistics are the frozen ones.

    The per-listing halves (`tfidf`, `rare`, `codes`) are derived here because they are cheap
    and local; the cohort halves are never recomputed, which is E70."""
    ctx = FeatureContext(settings=settings)
    ctx.block_docs = dict(calibration.block_docs)
    ctx.block_token_df = {block: dict(df) for block, df in calibration.block_token_df.items()}
    ctx.corpus_docs = calibration.corpus_docs
    ctx.corpus_token_df = dict(calibration.corpus_token_df)
    ctx.pin_pop = dict(calibration.pin_pop)
    ctx.block_attr_df = {
        block: {tuple(token.split(_SEP, 1)): count for token, count in bucket.items()}  # type: ignore[misc]
        for block, bucket in calibration.block_attr_df.items()
    }
    certifying = set(calibration.certifying_codes)
    for listing_id, fp in fps.items():
        block = _block_of(fp)
        df = ctx.block_token_df.setdefault(block, {})
        n_docs = max(1, ctx.block_docs.get(block, 1))
        counts: dict[str, int] = {}
        for token in fp.desc_tokens or ():
            counts[token] = counts.get(token, 0) + 1
        ctx.tfidf[listing_id] = _tfidf_vector(counts, df, n_docs)
        ctx.tfidf_corpus[listing_id] = _tfidf_vector(
            counts, ctx.corpus_token_df, max(1, ctx.corpus_docs)
        )
        ctx.rare[listing_id] = (
            {token for token in counts if df.get(token, 0) <= settings.rare_token_df}
            if n_docs >= MIN_RARE_BLOCK_DOCS
            else set()
        )
        listing = listings.get(listing_id)
        if listing is not None:
            kept = frozenset(code for code in reference_codes(listing.description)
                             if code in certifying)
            if kept:
                ctx.codes[listing_id] = kept
    return ctx


def attr_deltas(fp: Fingerprint, listing: Listing, settings: Settings) -> list[tuple[str, str]]:
    """The `(block, attr-token)` pairs one listing contributes to `block_attr_df`."""
    block = _block_of(fp)
    return [(block, key_token(pair))
            for pair in _feature_attr_map(listing, vocabulary_attr_keys(settings))]


# ------------------------------------------------------------------------------- the store


class Store(Protocol):
    """Everything the lane persists, in the shape SQL serves it.

    The replay drives the identical interface through an in-memory twin, so a divergence
    between the two paths is a bug in one implementation, never in the proof."""

    def lookup(self, probe: str, token: str) -> list[int]: ...
    def lookup_many(self, keys: Sequence[tuple[str, str]]
                    ) -> dict[tuple[str, str], list[int]]: ...
    def put_listing(self, listing_id: int, row: FpRow,
                    keys: Sequence[tuple[str, str]]) -> None: ...
    def put_decision_digests(self, dds: Mapping[int, str]) -> None: ...
    def drop_listing(self, listing_id: int) -> None: ...
    def keys_many(self, ids: Iterable[int]) -> dict[int, list[tuple[str, str]]]: ...
    def rows(self, ids: Iterable[int]) -> dict[int, FpRow]: ...
    def known(self, ids: Iterable[int]) -> set[int]: ...
    def pairs_touching(self, ids: Iterable[int]) -> dict[tuple[int, int], PairRow]: ...
    def pairs_within(self, members: Iterable[int]) -> list[PairRow]: ...
    def merge_neighbours(self, ids: Iterable[int]) -> dict[int, set[int]]: ...
    def upsert_pairs(self, rows: Sequence[PairRow]) -> int: ...
    def delete_pairs(self, keys: Sequence[tuple[int, int]]) -> None: ...
    def rejected(self, keys: Sequence[tuple[int, int, str, str]]) -> set[tuple[int, int]]: ...
    def ledger_keyed(self) -> bool: ...
    def clusters_touching(self, members: Iterable[int]) -> dict[int, list[int]]: ...
    def write_clusters(self, drop_keys: Sequence[int], rows: Sequence[dict[str, Any]],
                       conflicts: Sequence[dict[str, Any]]) -> None: ...
    def must_not_link(self) -> set[tuple[int, int]]: ...
    def must_link(self) -> set[tuple[int, int]]: ...
    def rulings_changed(self) -> set[int]: ...
    def cells(self, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], CellRow]: ...
    def bump_cell(self, listing: Listing) -> None: ...
    def unbump_cell(self, cell: tuple[str, str]) -> None: ...
    def stamped_merges(self, blocks: Sequence[str]
                       ) -> list[tuple[int, int, ContextStamp, str, bool]]: ...
    def flush(self) -> None: ...


class FactSource(Protocol):
    """The read-only half: listing facts and galleries out of `public` (D4 — never written)."""

    # E933: `clip=False` hands every gallery over without its CLIP vectors, and `vectors`
    # reads them for a set of images afterwards.
    def facts(self, ids: Iterable[int], *, clip: bool = True
              ) -> dict[int, tuple[Listing, list[Image]]]: ...
    def vectors(self, image_ids: Iterable[int]) -> dict[int, str]: ...


@dataclass(slots=True, frozen=True)
class WorkItem:
    """One claimed listing and WHEN it arrived, per feed.

    The arrival stamp is the feed's own event time — a new row's `first_seen_at`, a content
    change's `scraped_at`, a flip's `inactive_at` — and never the listing's age, so
    "arrival to decision" measures the lane's latency rather than how old the advert is."""

    listing_id: int
    feed: str
    arrived_at: float | None = None
    # The feed's own cursor value for this row — a listing id, a snapshot id, an
    # `(inactive_at, id)` pair. The watermark is the MAXIMUM over the items a pass committed,
    # so a claim that was refused advances nothing.
    cursor: Any = None
    # The scope no longer holds this listing (E79). It is RETIRED rather than refreshed: its
    # postings, its fingerprint row and its pairs go, and the neighbours it linked re-cluster
    # without it. Half-indexed is worse than unindexed — every neighbour would go on
    # retrieving it through postings nothing maintains.
    retire: bool = False
    # The evidence sweep claimed this listing because its PHOTOGRAPHS moved, not its content
    # (E92). Its fingerprint digest may be identical — a CLIP vector reaches no fingerprint
    # field — and a hold whose horizon has passed moves no fact at all, so the idempotence
    # short-circuit has to be bypassed for exactly these rows or nothing would be re-decided.
    redecide: bool = False


class WorkSource(Protocol):
    """The watermark feeds, bounded. Five index-served cursors in production; an arrival
    schedule in the replay."""

    def claim(self, limit: int) -> list[WorkItem]: ...
    def commit(self, done: Sequence[WorkItem]) -> dict[str, Any]: ...


# ------------------------------------------------------------------------------ retrieval


def retrieve(
    fp: Fingerprint,
    keyer: Keyer,
    store: Store,
    guards: Mapping[int, GuardRow],
    settings: Settings,
    vetoed: dict[tuple[int, int], str],
) -> dict[int, set[str]]:
    """`BlockIndex.candidates` over SQL postings — the same order, the same cap, the same guards.

    Fill order is `PROBE_PRIORITY` then the listing's own `probe_keys` order then ascending
    listing id, exactly as the cohort pass fills it, so the cap can only ever discard the
    weakest evidence class (E17); a candidate only an area exemption admits is seated after
    every base candidate, in a slot none took (C4c). `tests/autodedup/test_incremental.py`
    asserts this against `BlockIndex.candidates` over the whole cohort rather than trusting the
    restatement."""
    return _fill(fp.listing_id, fp, keyer.probe_keys(fp), keyer, store, guards, settings,
                 vetoed) or {}


def retrieve_posted(
    side: GuardRow,
    posted: Iterable[tuple[str, str]],
    keyer: Keyer,
    store: Store,
    guards: Mapping[int, GuardRow],
    settings: Settings,
    vetoed: dict[tuple[int, int], str],
) -> dict[int, set[str]] | None:
    """`retrieve` for a listing whose facts the pass does not read (E960): its keys as the store
    posts them and its guard columns as its row keeps them. None when its `phash` bands came
    from two anchors or more and the fan-out cap refused, inside that probe, a candidate the
    rule floor would have let in — the one case the lost order of its bands can reach — or the
    exemption window's seats (C4c) cut through the candidates first met inside that probe, and
    the caller then reads its facts and `retrieve`s."""
    keys = keyer.probe_keys_of(posted)
    bands = [band_of(token) for probe, token in keys if probe == "phash"]
    return _fill(side.listing_id, side, keys, keyer, store, guards, settings, vetoed,
                 loose="phash" if len(bands) != len(set(bands)) else None)


def _fill(
    listing_id: int,
    side: GuardSide,
    probe_keys: Sequence[tuple[str, str]],
    keyer: Keyer,
    store: Store,
    guards: Mapping[int, GuardRow],
    settings: Settings,
    vetoed: dict[tuple[int, int], str],
    loose: str | None = None,
) -> dict[int, set[str]] | None:
    # `index.probes` is `PROBE_PRIORITY`, plus E300's town and E936's quarter probes last when
    # the row asks for them.
    probes = keyer.index.probes
    by_probe: dict[str, list[str]] = {probe: [] for probe in probes}
    wanted: list[tuple[str, str]] = []
    for probe, token in probe_keys:
        by_probe[probe].append(token)
        if not keyer.is_exploded(probe, token):
            wanted.append((probe, token))
    # ONE statement for the whole listing (E74), not one per key: the posting lists are read
    # in `PROBE_PRIORITY` order below, so batching the fetch cannot move the fill order.
    postings = store.lookup_many(wanted)
    out: dict[int, set[str]] = {}
    # C4c: the candidates only an area exemption admits, in fill order, seated after the fill.
    window: dict[int, set[str]] = {}
    cap = settings.max_candidates_per_listing
    quota = exemption_window_quota(cap)
    # The loose probe's window candidates: the count before it, after it, and whether the quota
    # turned one away inside it.
    loose_span: tuple[int, int, bool] | None = None
    for probe in probes:
        # A probe whose tokens are out of order fills the same set in any order unless the cap
        # turns away a base candidate the floor would admit while it is read, or the window's
        # seats end among the candidates it met first.
        ordered = probe != loose or len(out) >= cap
        start, overflow = len(window), False
        for token in by_probe[probe]:
            if keyer.is_exploded(probe, token):
                continue
            for other in postings.get((probe, token), ()):
                if other == listing_id:
                    continue
                hit = out.get(other)
                if hit is None:
                    hit = window.get(other)
                if hit is not None:
                    hit.add(probe)
                    continue
                pair = (listing_id, other) if listing_id < other else (other, listing_id)
                if len(out) >= cap:
                    if not ordered and pair not in vetoed:
                        row = guards.get(other)
                        if (row is not None and pair_veto(side, row, settings) is None
                                and not in_exemption_window(side, row, settings)):
                            return None
                    continue
                if pair in vetoed:
                    continue
                row = guards.get(other)
                if row is None:
                    continue
                veto = pair_veto(side, row, settings)
                if veto is not None:
                    vetoed[pair] = veto
                    continue
                if not in_exemption_window(side, row, settings):
                    out[other] = {probe}
                elif len(window) < quota:
                    window[other] = {probe}
                else:
                    overflow = True
        if not ordered:
            loose_span = (start, len(window), overflow)
    seats = min(cap - len(out), quota)
    if loose_span is not None:
        start, end, overflow = loose_span
        if start < seats and (seats < end or overflow):
            return None
    for other in list(window)[:seats]:
        out[other] = window[other]
    return out


def neighbourhood(
    keyer: Keyer, store: Store, keys: Iterable[tuple[str, str]]
) -> set[int]:
    """Every listing whose own retrieval could move because a listing under `keys` changed.

    The ±1 relation on area band and price decile is symmetric, so "the listings that PROBE
    one of these keys" is exactly "the listings these keys' probes reach" — one lookup set,
    not a second widening."""
    wanted = sorted({key for key in keys if not keyer.is_exploded(key[0], key[1])})
    out: set[int] = set()
    for ids in store.lookup_many(wanted).values():
        out.update(ids)
    return out


# -------------------------------------------------------------------------------- the pass


class PassDeadline(Exception):
    """The pass ran past its own deadline (E913). Raised between steps, never inside a write:
    the caller's one transaction rolls back, nothing is written and no cursor moves (E75), and
    the next pass claims half as much."""


class PassStopped(PassDeadline):
    """The caller is shutting down (E941): raised at the same checkpoints as the deadline, so
    a pass the deploy is about to kill rolls back on its own live connection and frees its row
    locks at once instead of holding them until the process dies."""


def _in_time(deadline: float | None, stopping: Callable[[], bool] | None = None) -> None:
    if deadline is not None and time.perf_counter() >= deadline:
        raise PassDeadline("the pass ran past its deadline")
    if stopping is not None and stopping():
        raise PassStopped("the worker is shutting down")


@dataclass(slots=True)
class Limits:
    """Everything one pass is allowed to spend, and what it does when it cannot fit.

    `max_pairs` is a SCHEDULING bound, never a correctness one: a pass that scored the first
    20,000 of a wanted set and wrote them would leave the rest of the neighbourhood's pairs
    undecided, re-cluster from a partial edge set and then advance the watermark over the
    difference — measured on g6 at a cap of 500, that manufactures 38 member sets the cohort
    pass never produces. So the pass REFUSES instead (E75) and `run_pass_bounded` re-claims a
    smaller slice, which is the same work at the same answer."""

    max_listings: int = 500
    # The pairs a pass will DECIDE, build and live alike (E943): a pair the reject ledger
    # answers costs no scoring time, and its 1-2 KB is noise beside the 56-67 KB an endpoint
    # read costs anyway; 150k scored is ~480 s beside ~450 s of fixed cost, inside 1,050 s.
    max_pairs: int = 150_000
    max_component: int = 400
    # E950: the decided pairs are scored this many at a time, each chunk's rows written before
    # the next, so the rows a pass holds follow the chunk and not `max_pairs`. A side's CLIP
    # vectors and feature context are held from its first decided pair to its last, which in a
    # dense pass is most sides. A scheduling bound as well: the rows are the same.
    score_chunk: int = 5_000


@dataclass(slots=True)
class PassResult:
    generation: str
    calibration_digest: str
    claimed: list[int] = field(default_factory=list)
    dirty: int = 0
    pairs_scored: int = 0
    pairs_written: int = 0
    pairs_deleted: int = 0
    # E943: wanted pairs the reject ledger answered instead of a re-score, the pairs the pass
    # then had to decide (what `max_pairs` is checked against) and the rejects it ledgered.
    ledger_skipped: int = 0
    pairs_to_decide: int = 0
    ledger_written: int = 0
    # E950: the process's resident memory (MiB) as each scoring chunk was written, before it gave
    # its vectors and features back: one reading a chunk, so their number is the chunks'. Beside
    # it, the listings holding their CLIP vectors at that moment: the share no chunk bounds.
    chunk_rss_mb: list[float | None] = field(default_factory=list)
    chunk_vectored: list[int] = field(default_factory=list)
    components: int = 0
    clusters_written: int = 0
    clusters_dropped: int = 0
    retired: int = 0
    # Merges this pass HELD in the band for want of photographs (E93), and listings the
    # evidence sweep re-claimed because their photographs moved (E92).
    held: int = 0
    released: int = 0
    redecided: int = 0
    # E926: the must-link closures the pass's re-clusters dissolved, each once (the E64 rail
    # re-clusters a component a second time) — re-read every pass the contradiction stands
    # (`_ruling_seeds`); a component past `max_component` is not re-clustered, so not read.
    dissolved: set[tuple[int, ...]] = field(default_factory=set)
    rail: dict[str, int] = field(default_factory=dict)
    zones: dict[str, int] = field(default_factory=dict)
    certificates: dict[str, int] = field(default_factory=dict)
    latency_s: list[float] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    spent_usd: float = 0.0
    oversized_components: list[int] = field(default_factory=list)
    cursors: dict[str, Any] = field(default_factory=dict)
    feeds: dict[str, int] = field(default_factory=dict)
    # Set when the pairs to decide did not fit `max_pairs` (E943). The pass then wrote no pair, no
    # cluster and no cursor: `aborted` is the reason a caller shrinks the claim and retries.
    aborted: str = ""
    wanted_pairs: int = 0
    attempts: int = 1
    # E952: each attempt's wanted pair set, the refused ones first (`run_pass_bounded`).
    wanted_by_attempt: list[int] = field(default_factory=list)
    # E943: each attempt's pairs to decide, the number the budget refused or accepted.
    pairs_to_decide_by_attempt: list[int] = field(default_factory=list)
    # The groups this pass (re)wrote — what the lane's reconcile (A9) looks at first.
    cluster_keys: list[int] = field(default_factory=list)
    # E960: the listings whose facts the pass read, by why: its claim, a neighbour whose phash
    # band order the cap could reach (`order`), a side whose row carries no decision digest
    # yet (`digest`), the sides of the pairs it decided, a group's members, the rail's merges.
    reads: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "calibration_digest": self.calibration_digest,
            "counts": {
                "claimed": len(self.claimed),
                "dirty": self.dirty,
                "pairs_scored": self.pairs_scored,
                "pairs_written": self.pairs_written,
                "pairs_deleted": self.pairs_deleted,
                "ledger_skipped": self.ledger_skipped,
                "pairs_to_decide": self.pairs_to_decide,
                "ledger_written": self.ledger_written,
                "chunks": len(self.chunk_rss_mb),
                "components": self.components,
                "clusters_written": self.clusters_written,
                "clusters_dropped": self.clusters_dropped,
                "retired": self.retired,
                "held": self.held,
                "released": self.released,
                "redecided": self.redecided,
                "must_link_dissolved": len(self.dissolved),
                "wanted_pairs": self.wanted_pairs,
                "attempts": self.attempts,
                "wanted_by_attempt": list(self.wanted_by_attempt),
                "pairs_to_decide_by_attempt": list(self.pairs_to_decide_by_attempt),
                "reads": dict(self.reads),
            },
            "feeds": dict(self.feeds),
            "aborted": self.aborted,
            "zones": dict(self.zones),
            "certificates": dict(self.certificates),
            "rail": dict(self.rail),
            "latency_s": _latency(self.latency_s),
            "timings": {k: round(v, 4) for k, v in self.timings.items()},
            "chunk_rss_mb": list(self.chunk_rss_mb),
            "chunk_vectored": list(self.chunk_vectored),
            # D19: no LLM judge has merge authority on the band, and this lane runs none.
            "spent_usd": self.spent_usd,
            "oversized_components": self.oversized_components,
            "cursors": self.cursors,
        }


def _latency(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": round(_percentile(ordered, 0.50), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "max": round(ordered[-1], 3),
        "mean": round(sum(ordered) / len(ordered), 3),
    }


def _percentile(ordered: Sequence[float], q: float) -> float:
    rank = max(1, min(len(ordered), math.ceil(q * len(ordered))))
    return float(ordered[rank - 1])


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def rss_mb(statm: str = "/proc/self/statm") -> float | None:
    """The process's resident memory now in MiB: resident pages x the page size (E948)."""
    try:
        with open(statm, encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1_048_576.0, 1)
    except Exception:  # noqa: BLE001 — observability only: None, never a failed pass
        return None


def fp_digest(fp: Fingerprint, images: Sequence[Image]) -> str:
    """What a re-score depends on: if this moves, every pair touching the listing is re-decided.

    Cheap and total — the whole fingerprint plus the gallery's hashes — because a digest that
    misses a field is a stale decision nobody can see."""
    payload = json.dumps(
        [
            fp.source, fp.block_key, fp.cat_group, fp.category_main, fp.category_type,
            fp.area_m2, fp.area_band, fp.disposition, fp.floor, fp.total_floors,
            fp.broker_key, fp.street_key, fp.house_number, fp.ruian_adm_kod, fp.psc,
            fp.lat, fp.lon, fp.pin_key, fp.price, sorted(fp.price_events),
            fp.desc_simhash, len(fp.desc_tokens), sorted(fp.all_hashes),
            sorted(fp.image_hashes), fp.n_images, fp.country_status,
            fp.first_seen_at, fp.last_seen_at, fp.inactive_at, fp.is_active,
            sorted((img.image_id, img.phash, img.pop, img.seq) for img in images),
        ],
        sort_keys=True, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def decision_digest(listing: Listing, images: Sequence[Image]) -> str:
    """What a below-floor decision depends on of ONE side (E943): the whole listing and its
    gallery as the engine reads them, CLIP tags included. Not the CLIP vectors, which an
    unscored endpoint is read without: `clip_tagged_at` stands for them, the stamp the CLIP job
    writes last, after the tags and the vector, and a re-master nulls
    (`invalidate_derived_signals`).

    Not `fp_digest`: that hashes the body as its SimHash and token count, which a one-token edit
    keeps, and it misses broker identity, granularity, attrs, subtype, the URL and the CLIP job."""
    payload = json.dumps(
        [asdict(listing),
         sorted((img.image_id, img.seq, img.phash, img.pop, img.tags, img.clip_tagged_at)
                for img in images)],
        sort_keys=True, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# The modules a below-floor decision is computed by (E943): the import closure, inside
# `autodedup`, of `decide`, `features`, `fingerprint`, `guards`, `model`, `store_score` and
# `dataset` (a test holds it to this list), plus this module, whose `context_for` builds the
# features' context. A deploy that changes any of them moves `engine_digest`, so the reject
# ledger re-decides every wanted reject once rather than standing in for code that is gone.
DECISION_MODULES: tuple[str, ...] = (
    "body_align", "d43", "dataset", "decide", "demonstrate", "development", "family",
    "features", "fingerprint", "floor_convention", "guards", "hazard_context", "incremental",
    "indistinguishable", "model", "normalize", "settings", "stock", "store_score", "text_facts",
)


def source_digest(root: Path, modules: Sequence[str]) -> str:
    """The named modules' source bytes, hashed in order."""
    digest = hashlib.sha1()
    for name in modules:
        digest.update(name.encode("utf-8"))
        digest.update((root / f"{name}.py").read_bytes())
    return digest.hexdigest()[:16]


# Read once, at import: the code a running process decides with cannot change under it.
DECISION_SOURCE_DIGEST: str = source_digest(Path(__file__).resolve().parent, DECISION_MODULES)


def engine_digest(settings: Settings, model: LogisticModel) -> str:
    """The decision code, the model and the settings a reject is decided under (E943): the
    reject ledger's fifth key. The model is hashed as loaded (`to_json`), so the hand-set prior,
    which has no file, is covered as `models/<version>.json` is."""
    payload = json.dumps([DECISION_SOURCE_DIGEST, model.to_json(), settings.to_dict()],
                         sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


class _Working:
    """Fingerprints, listings and galleries of one pass's working set, fetched once."""

    def __init__(self, facts: FactSource, settings: Settings) -> None:
        self.facts = facts
        self.settings = settings
        self.listings: dict[int, Listing] = {}
        self.images: dict[int, list[Image]] = {}
        self.fps: dict[int, Fingerprint] = {}
        # E933: who carries CLIP vectors. A gallery is fetched without them unless asked: no
        # fingerprint, key, guard, digest or census reads one. The listings a pass refreshed
        # (their row records the vectors' PRESENCE, E92; read once the pair budget agreed, E952)
        # and the scored endpoints ask.
        self.vectored: set[int] = set()
        # E960: the listings whose facts were read, by why (the heartbeat's `reads`).
        self.read: dict[str, int] = {}

    def ensure(self, ids: Iterable[int], *, clip: bool = False, reason: str = "other") -> None:
        ids = list(ids)
        missing = [i for i in ids if i not in self.fps]
        if missing:
            fetched = self.facts.facts(missing, clip=clip)
            if fetched:
                self.read[reason] = self.read.get(reason, 0) + len(fetched)
            for listing_id, (listing, images) in fetched.items():
                self.listings[listing_id] = listing
                self.images[listing_id] = list(images)
                self.fps[listing_id] = build_fingerprint(listing, images, self.settings)
                if clip:
                    self.vectored.add(listing_id)
        if not clip:
            return
        # The top-up reads the vectors and nothing else: a second gallery read would count the
        # population readout twice, and the fingerprint already built reads no vector.
        bare = [i for i in ids if i in self.fps and i not in self.vectored]
        if not bare:
            return
        vectors = self.facts.vectors(
            [image.image_id for i in bare for image in self.images.get(i, ())])
        for listing_id in bare:
            self.images[listing_id] = [
                replace(image, clip=vectors[image.image_id])
                if image.image_id in vectors else image
                for image in self.images.get(listing_id, ())]
            self.vectored.add(listing_id)

    def release(self, ids: Iterable[int]) -> None:
        """Give back these listings' CLIP vectors, decoded ones included (E950): their galleries
        as the bare read holds them, so a later `ensure(clip=True)` reads the vectors again."""
        for listing_id in ids:
            if listing_id in self.vectored:
                self.vectored.discard(listing_id)
                self.images[listing_id] = [
                    replace(image, clip=None) if image.clip is not None else image
                    for image in self.images.get(listing_id, ())]


class _Overlay:
    """The pass's OWN writes, held back until the budget has agreed to them (E75).

    A pass refuses a claim it cannot fit, and the refusal has to leave nothing behind: a store
    that already held the new postings would tell the next, smaller attempt that those listings
    are unchanged, and their pairs would never be scored at all. So everything up to the budget
    check reads and writes through this view — postings, fingerprint rows and census cells —
    and `apply` is the single moment it all lands. Everything else delegates, because nothing
    else is written before the budget check."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.fp: dict[int, FpRow] = {}
        self.keys: dict[int, list[tuple[str, str]]] = {}
        # Buffered postings, indexed BY KEY rather than by listing: retrieval asks for a
        # listing's keys thousands of times a pass, and a scan of the whole buffer per ask
        # would make the overlay cost more than the pass it protects.
        self.added: dict[tuple[str, str], set[int]] = {}
        self.dropped: dict[tuple[str, str], set[int]] = {}
        self.cell_rows: dict[tuple[str, str], CellRow] = {}
        self.ops: list[tuple[str, Any]] = []
        # Retired listings (E79), held back like everything else until the budget agrees.
        self.gone: set[int] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)

    # -------------------------------------------------------------------- buffered writes
    def put_listing(self, listing_id: int, row: FpRow, keys: Sequence[tuple[str, str]],
                    old_keys: Sequence[tuple[str, str]] = ()) -> None:
        self.fp[listing_id] = row
        self.keys[listing_id] = list(keys)
        fresh = set(keys)
        for key in old_keys:
            if key not in fresh:
                self.dropped.setdefault(key, set()).add(listing_id)
            self.added.get(key, set()).discard(listing_id)
        for key in fresh:
            self.added.setdefault(key, set()).add(listing_id)
            self.dropped.get(key, set()).discard(listing_id)

    def drop_listing(self, listing_id: int, old_keys: Sequence[tuple[str, str]] = ()) -> None:
        """Retire a listing the scope no longer holds. The caller has already read its keys,
        so they are passed in rather than re-read."""
        listing_id = int(listing_id)
        for key in self.keys.pop(listing_id, ()):
            self.added.get(key, set()).discard(listing_id)
        self.fp.pop(listing_id, None)
        for key in old_keys:
            self.dropped.setdefault(key, set()).add(listing_id)
            self.added.get(key, set()).discard(listing_id)
        self.gone.add(listing_id)

    def bump_cell(self, listing: Listing) -> None:
        cell = (address_block_key(listing), category_group(listing))
        row = self._cell(cell)
        row.n_listings += 1
        self.ops.append(("bump", listing))

    def unbump_cell(self, cell: tuple[str, str]) -> None:
        row = self._cell((str(cell[0]), str(cell[1])))
        row.n_listings = max(0, row.n_listings - 1)
        self.ops.append(("unbump", (str(cell[0]), str(cell[1]))))

    def _cell(self, cell: tuple[str, str]) -> CellRow:
        if cell not in self.cell_rows:
            live = self.store.cells([cell]).get(cell)
            self.cell_rows[cell] = CellRow(
                cell[0], cell[1], live.n_listings if live else 0,
                list(live.shapes) if live else [], list(live.brokers) if live else [],
                list(live.source_ids) if live else [], bool(live.capped) if live else False)
        return self.cell_rows[cell]

    # --------------------------------------------------------------------- buffered reads
    def lookup_many(self, keys: Sequence[tuple[str, str]]
                    ) -> dict[tuple[str, str], list[int]]:
        out = {key: list(ids) for key, ids in self.store.lookup_many(keys).items()}
        for key in keys:
            for listing_id in self.dropped.get(key, ()):
                if listing_id in out.get(key, ()):
                    out[key].remove(listing_id)
            for listing_id in self.added.get(key, ()):
                bucket = out.setdefault(key, [])
                if listing_id not in bucket:
                    # Postings stay ascending: the cap fills in listing-id order (E17).
                    bisect.insort(bucket, listing_id)
        return {key: ids for key, ids in out.items() if ids}

    def rows(self, ids: Iterable[int]) -> dict[int, FpRow]:
        wanted = list(ids)
        out = dict(self.store.rows([i for i in wanted if i not in self.fp]))
        out.update({i: self.fp[i] for i in wanted if i in self.fp})
        return {i: row for i, row in out.items() if i not in self.gone}

    def known(self, ids: Iterable[int]) -> set[int]:
        wanted = set(ids)
        return (self.store.known(wanted) | (wanted & set(self.fp))) - self.gone

    def keys_many(self, ids: Iterable[int]) -> dict[int, list[tuple[str, str]]]:
        wanted = list(ids)
        out = dict(self.store.keys_many(wanted))
        out.update({i: list(self.keys[i]) for i in wanted if i in self.keys})
        return {i: keys for i, keys in out.items() if i not in self.gone}

    def cells(self, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], CellRow]:
        wanted = list(keys)
        out = dict(self.store.cells(wanted))
        out.update({key: self.cell_rows[key] for key in wanted if key in self.cell_rows})
        return out

    # --------------------------------------------------------------------------- the gate
    def apply(self) -> None:
        # Retirements first: a store that dropped a listing AFTER re-writing its postings
        # would have written rows it then has to find again.
        for listing_id in sorted(self.gone):
            self.store.drop_listing(listing_id)
        for listing_id in sorted(self.keys):
            self.store.put_listing(listing_id, self.fp[listing_id], self.keys[listing_id])
        for verb, payload in self.ops:
            if verb == "bump":
                self.store.bump_cell(payload)
            else:
                self.store.unbump_cell(payload)
        self.fp.clear()
        self.keys.clear()
        self.added.clear()
        self.dropped.clear()
        self.cell_rows.clear()
        self.ops.clear()
        self.gone.clear()


def run_pass(
    store: Store,
    facts: FactSource,
    work: WorkSource,
    settings: Settings,
    model: LogisticModel,
    calibration: Calibration,
    limits: Limits | None = None,
    generation: str = GENERATION,
    now: float | None = None,
    hold: EvidenceHold | None = None,
    deadline: float | None = None,
    stopping: Callable[[], bool] | None = None,
) -> PassResult:
    """One bounded, idempotent incremental pass. Re-running it on an unchanged corpus is a no-op.

    `deadline` (a `time.perf_counter()` instant) bounds the pass's own time (E913): it is read
    between steps, every few hundred pair decisions and — in the lane, whose `SqlFacts` holds
    the same instant — between the slices of every fact read (E931), and past it the pass
    raises `PassDeadline` for its caller's transaction to roll back; `stopping` (the worker's
    shutdown, E941) is read at the same checkpoints and raises `PassStopped`.

    The order is the cohort pass's order, restricted: refresh the fingerprints that moved,
    widen to the probe-key neighbourhood (E71), retrieve, score what is new or stale, write the
    pair rows, re-cluster the touched components (E72), then run E64's rail over the census.

    Every write lands before the watermark moves, and the watermark moves only when the whole
    claim was decided — a pass that cannot fit its pair set writes nothing at all (E75)."""
    # E83 is a COHORT-wide index (who else carries each frame) and this pass sees one claim at
    # a time, so it would silently subtract what the batch pass keeps and break E70's replay
    # equivalence. An incremental path that wants it owes the index first.
    if settings.catalog_carrier_aware:
        raise NotImplementedError(
            "catalog_carrier_aware (E83) has no incremental carrier index yet: a pass that "
            "cannot see a frame's other carriers would decide it differently from the cohort "
            "pass, and E70's replay equivalence is what says the two agree"
        )
    # E85 is decided over the whole K-B FAMILY, and a family is a property of the pair set this
    # pass sees only a slice of. The batch side is built (`family.refusals`); the incremental
    # side owes a stored family index plus E64's re-evaluation rail extended to K-B — a pair
    # certified yesterday has to fall back to the BAND when tomorrow's arrival makes its family
    # impure. Until that exists a pass would certify what the cohort pass refuses, which is
    # exactly the divergence E70's replay equivalence is there to catch, so it fails loudly.
    # E88 is decided over a K-B FAMILY too, and the same slice problem applies — with one
    # difference worth stating, because it is what makes the rail designable: the hold is
    # MONOTONE. A family only gains members, vocabulary only arrives, a size bar is only
    # cleared upwards and a block census only fills up, so a family can only ever turn
    # development-like and a hold can only ever be ADDED. The rail owed is therefore E64's,
    # triggered by a family gaining a member or a marker, demoting that family's earlier K-B
    # merges to the BAND and never unmerging, capped per family the way E64 caps per block.
    if settings.development_hold_mode != "off":
        raise NotImplementedError(
            "development_hold_mode (E88) has no incremental family index or re-evaluation "
            "rail yet: a pass that sees one claim cannot read the whole K-B family or its "
            "block census, and the hold is monotone — the rail owed is E64's, triggered by a "
            "family gaining a member or a marker, with K-B the one certificate E64's "
            "certificate exemption must not cover"
        )
    if settings.family_guard_mode != "off":
        raise NotImplementedError(
            "family_guard_mode (E85) has no incremental family index or re-evaluation rail "
            "yet: a pass that sees one claim cannot read the whole K-B family, and a family "
            "only ever grows — the rail owed is E64's, triggered by a family gaining a member "
            "rather than by a block census growing, with K-B the one certificate E64's "
            "certificate exemption must not cover"
        )
    caps = limits or Limits()
    result = PassResult(generation=generation, calibration_digest=calibration.digest())
    clock = time.perf_counter()

    items = list(work.claim(caps.max_listings))
    seen_ids: set[int] = set()
    # A listing claimed for RETIREMENT is never also refreshed, whichever feed saw it: the
    # scope no longer holds it, so there is nothing to refresh it into.
    retire_ids = {item.listing_id for item in items if item.retire}
    # The evidence sweep's rows (E92): re-decided even when nothing a digest can see moved.
    redecide = {item.listing_id for item in items
                if item.redecide and item.listing_id not in retire_ids}
    claimed = [item.listing_id for item in items
               if item.listing_id not in retire_ids
               and not (item.listing_id in seen_ids or seen_ids.add(item.listing_id))]
    result.claimed = list(claimed)
    for item in items:
        result.feeds[item.feed] = result.feeds.get(item.feed, 0) + 1
    if not claimed and not retire_ids:
        # An idle pass is still the only moment an operator's ruling can be honoured: a
        # must-not-link or a `same` moves no pair, so nothing else would ever seed its
        # component (E78, E910).
        rulings = read_rulings(store)
        seeds = _ruling_seeds(store, rulings)
        if seeds:
            idle = _Working(facts, settings)
            _recluster(store, facts, settings, idle, seeds, caps, result, rulings)
            result.reads = dict(idle.read)
        store.flush()
        result.cursors = work.commit(items)
        result.timings["total_s"] = time.perf_counter() - clock
        return result

    keyer = Keyer(settings, calibration)
    working = _Working(facts, settings)
    # Without vectors: nothing up to the budget check reads one, so a refused attempt never
    # pays for them (E952); the refreshed listings' are read once the budget has agreed.
    working.ensure(claimed, reason="claim")
    # Everything this pass writes before the budget check goes through the overlay, so a
    # refusal leaves the store exactly as it found it (E75).
    view = _Overlay(store)

    # --- 1. refresh the changed listings' fingerprints and postings ------------------------
    # Two batched reads for the whole claim, not two statements per listing (E74).
    stored_rows = view.rows(claimed)
    stored_keys = view.keys_many(claimed)
    changed: set[int] = set()
    touched_keys: list[tuple[str, str]] = []
    touched_blocks: set[str] = set()
    # E960: the decision digests of the listings the pass read whose row does not carry them
    # yet, written once the budget has agreed.
    stamps: dict[int, str] = {}

    # --- 1a. E79: retire what the scope no longer holds ------------------------------------
    # A retired listing's keys are TOUCHED keys, so step 2 widens to everything that could
    # retrieve it; its stored pairs are then absent from the wanted set, which is what deletes
    # them, and step 6 re-clusters the components those edges held together.
    retired: set[int] = set()
    if retire_ids:
        departed = sorted(view.known(retire_ids))
        gone_rows = view.rows(departed)
        gone_keys = view.keys_many(departed)
        for listing_id in departed:
            old = gone_rows.get(listing_id)
            old_keys = sorted(set(gone_keys.get(listing_id, ())))
            if old is not None:
                view.unbump_cell(old.cell)
                touched_blocks.add(old.cell_key)
            touched_keys.extend(old_keys)
            view.drop_listing(listing_id, old_keys)
            retired.add(listing_id)
    result.retired = len(retired)

    for listing_id in claimed:
        fp = working.fps.get(listing_id)
        if fp is None:
            continue
        listing = working.listings[listing_id]
        digest = fp_digest(fp, working.images.get(listing_id, ()))
        old = stored_rows.get(listing_id)
        old_keys = sorted(set(stored_keys.get(listing_id, ())))
        # DISTINCT and sorted on both sides: `index_keys` repeats a key whenever two images
        # share a pHash band, and the posting list is a set — a pass that read that difference
        # as a change would re-score the whole corpus on every drain.
        new_keys = sorted(set(keyer.index_keys(fp)))
        dd = decision_digest(listing, working.images.get(listing_id, ()))
        # E960: a claimed advert whose decision digest moved under an unmoved fingerprint (a
        # body edit the SimHash keeps, a broker identity, tags) is refreshed too, so its stored
        # pairs are re-decided, not only its ledgered rejects; a row with no digest yet (written
        # before migration 604) is stamped.
        if (old is not None and old.digest == digest and old_keys == new_keys
                and listing_id not in redecide and (not old.dd or old.dd == dd)):
            if old.dd != dd:
                stamps[listing_id] = dd
            continue  # idempotence: an unchanged listing costs a digest, not a pass
        cell = (address_block_key(listing), category_group(listing))
        if old is not None:
            # The cell it LEFT, read off the stored row — never re-derived from today's facts,
            # which would leave a phantom member in the old cell for ever (E64 reads that count).
            view.unbump_cell(old.cell)
            touched_blocks.add(old.cell_key)
        view.put_listing(
            listing_id,
            # The evidence (E92) is filled in once the budget has agreed (E952, step 5).
            FpRow(guard_row(fp), digest, cell[0], cell[1], bool(listing.is_active),
                  # The FIRST decision's stamp is carried, never restamped: the evidence
                  # horizon is measured from it, and a refresh that reset it would hold a
                  # merge for ever (E92). The store coalesces it on the way down too.
                  first_decided_at=old.first_decided_at if old is not None else None, dd=dd,
                  block_key=fp.block_key),
            new_keys, old_keys,
        )
        view.bump_cell(listing)
        touched_keys.extend(old_keys)
        touched_keys.extend(new_keys)
        touched_blocks.add(cell[0])
        changed.add(listing_id)
    result.timings["refresh_s"] = time.perf_counter() - clock
    _in_time(deadline, stopping)

    # --- 2. E71: the dirty set is the probe-key neighbourhood ------------------------------
    # Its facts are NOT read (E960): a neighbour's retrieval needs only its posted keys and its
    # guard columns, both of which the store keeps.
    clock = time.perf_counter()
    dirty = set(changed) | neighbourhood(keyer, view, set(touched_keys))
    dirty &= view.known(dirty)
    result.dirty = len(dirty)

    # --- 3. retrieval for every dirty listing, against the CURRENT corpus ------------------
    # A slice of `KEY_CHUNK` dirty listings at a time (E960): its posted keys, postings and
    # guard rows are prefetched in three statements, so the retrieval loop is pure computation
    # over what is already in hand, and only one slice's posted keys are held at once.
    local_guards = {i: guard_row(working.fps[i]) for i in dirty if i in working.fps}
    lookup = _GuardLookup(view, local_guards)
    vetoed: dict[tuple[int, int], str] = {}
    cand: dict[int, dict[int, set[str]]] = {}
    unordered: list[int] = []
    order = sorted(dirty)
    for start in range(0, len(order), KEY_CHUNK):
        _in_time(deadline, stopping)
        chunk = order[start:start + KEY_CHUNK]
        posted = view.keys_many([i for i in chunk if i not in local_guards])
        probe_keys: set[tuple[str, str]] = set()
        for listing_id in chunk:
            fp = working.fps.get(listing_id)
            probe_keys.update(keyer.probe_keys(fp) if fp is not None
                              else keyer.probe_keys_of(posted.get(listing_id, ())))
        reachable: set[int] = set()
        for ids in view.lookup_many(sorted(key for key in probe_keys
                                           if not keyer.is_exploded(key[0], key[1]))).values():
            reachable.update(ids)
        fetched = view.rows(sorted((reachable | set(chunk)) - set(local_guards)
                                   - set(lookup.cache)))
        lookup.cache.update({i: row.guard for i, row in fetched.items()})
        for listing_id in chunk:
            fp = working.fps.get(listing_id)
            if fp is not None:
                cand[listing_id] = retrieve(fp, keyer, view, lookup, settings, vetoed)
                continue
            guard = lookup.cache.get(listing_id)
            if guard is None:
                continue
            found = retrieve_posted(guard, posted.get(listing_id, ()), keyer, view, lookup,
                                    settings, vetoed)
            if found is None:
                unordered.append(listing_id)
            else:
                cand[listing_id] = found
    if unordered:
        working.ensure(unordered, reason="order")
        for listing_id in unordered:
            fp = working.fps.get(listing_id)
            if fp is not None:
                cand[listing_id] = retrieve(fp, keyer, view, lookup, settings, vetoed)
        cand = {listing_id: cand[listing_id] for listing_id in sorted(cand)}
    result.timings["retrieve_s"] = time.perf_counter() - clock
    _in_time(deadline, stopping)

    # --- 4. the pair set: either side retrieving the other keeps it ------------------------
    clock = time.perf_counter()
    stored = store.pairs_touching(set(dirty) | retired)
    wanted: dict[tuple[int, int], dict[str, Any]] = {}
    for listing_id, found in cand.items():
        for other, probes in found.items():
            lo, hi = (listing_id, other) if listing_id < other else (other, listing_id)
            entry = wanted.setdefault(
                (lo, hi), {"probes": set(), "from_lo": False, "from_hi": False})
            entry["probes"] |= probes
            if listing_id == lo:
                entry["from_lo"] = True
            else:
                entry["from_hi"] = True
    # A pair with one clean side keeps the clean side's own direction, which did not move —
    # unless that side was RETIRED, in which case the clean direction is the stale one and
    # keeping it would resurrect the pair the retirement exists to drop.
    for (lo, hi), row in stored.items():
        if lo in retired or hi in retired:
            continue
        entry = wanted.get((lo, hi))
        if lo not in cand and row.from_lo:
            entry = entry or {"probes": set(), "from_lo": False, "from_hi": False}
            entry["from_lo"] = True
            entry["probes"] |= set(row.probes)
            wanted[(lo, hi)] = entry
        if hi not in cand and row.from_hi:
            entry = wanted.get((lo, hi)) or {"probes": set(), "from_lo": False,
                                             "from_hi": False}
            entry["from_hi"] = True
            entry["probes"] |= set(row.probes)
            wanted[(lo, hi)] = entry

    result.wanted_pairs = len(wanted)

    # --- 5. score what is new or stale -----------------------------------------------------
    # E960: whether a pair is decided reads each side's ROW — its digest and decision digest
    # as the lane last read its facts (a refreshed side's as this pass did) — so only the sides
    # of the pairs the pass then decides are read, once the budget has agreed.
    endpoints = {i for pair in wanted for i in pair}
    sides = view.rows(sorted(endpoints))
    digests = {i: row.digest for i, row in sides.items()}
    dds = {i: stamps.get(i) or row.dd for i, row in sides.items()}
    # A stored decision stands while neither side's digest moved and neither side was refreshed.
    # Only the pairs it does not cover are decided, and only their two sides read photographs —
    # the features the CLIP vectors, E93's hold their presence — so only they carry them (E933).
    decide: set[tuple[int, int]] = set()
    for lo, hi in wanted:
        if lo not in sides or hi not in sides:
            continue
        previous = stored.get((lo, hi))
        if (previous is None or previous.fp_lo != digests[lo] or previous.fp_hi != digests[hi]
                or lo in changed or hi in changed):
            decide.add((lo, hi))
    # E943: a pair decided below `store_floor` is not stored, so it reaches here with no row. The
    # ledger answers it while both sides' decision digests and the store's calibration, feature
    # version and engine digest all still match, which is exactly when a re-score would decide
    # it the same. A refreshed side is never answered: its pairs are re-decided whatever its
    # digest says. A side whose row predates its digest (migration 604) is read for it.
    asked = [(lo, hi) for lo, hi in sorted(decide)
             if (lo, hi) not in stored and lo not in changed and hi not in changed]
    # Read only while the ledger holds a row under this store's key: right after a deploy that
    # moved the engine digest (the one that ships 604) none matches, and the sides are read
    # once their pairs are decided instead.
    blank = sorted({i for pair in asked for i in pair if not dds[i]})
    if blank and store.ledger_keyed():
        working.ensure(blank, reason="digest")
        for listing_id in blank:
            if listing_id in working.listings:
                stamps[listing_id] = dds[listing_id] = decision_digest(
                    working.listings[listing_id], working.images.get(listing_id, ()))
    skipped = store.rejected([(lo, hi, dds[lo], dds[hi]) for lo, hi in asked
                              if dds[lo] and dds[hi]])
    decide -= skipped
    result.ledger_skipped = len(skipped)
    result.pairs_to_decide = len(decide)
    # E75: the budget is refused BEFORE the first write, because a partial pair set re-clusters
    # from a partial edge set and the watermark would then step over the difference for ever.
    if len(decide) > caps.max_pairs:
        # Nothing of this pass has reached the store yet, so the refusal IS the rollback.
        result.aborted = "pair_budget"
        result.reads = dict(working.read)
        result.timings["total_s"] = sum(
            value for key, value in result.timings.items() if key.endswith("_s"))
        return result

    # E92's evidence counts the refreshed listings' CLIP vectors, the one read of the claim the
    # wanted set does not need: read only now, so a refused attempt never pays it (E952).
    working.ensure(sorted(changed), clip=True, reason="claim")
    for listing_id in changed:
        view.fp[listing_id] = replace(
            view.fp[listing_id], evidence=evidence_of(working.images.get(listing_id, ())))
    view.apply()
    dropped = [key for key in stored if key not in wanted]
    if dropped:
        store.delete_pairs(dropped)
        result.pairs_deleted = len(dropped)

    # Bare: the decided sides' CLIP vectors are read a chunk at a time (E950), below.
    working.ensure({i for pair in decide for i in pair}, reason="decided")
    for listing_id in {i for pair in decide for i in pair}:
        if not dds[listing_id] and listing_id in working.listings:
            stamps[listing_id] = dds[listing_id] = decision_digest(
                working.listings[listing_id], working.images.get(listing_id, ()))
    if stamps:
        store.put_decision_digests(stamps)
    ctx = context_for(calibration, settings, working.fps, working.listings)
    census = _census(store, working.listings, working.images)
    # E93's two inputs, batched for the whole pass: what each endpoint's photographs ARE right
    # now (off the facts this pass read) and when this generation first decided it.
    decided_at = ({i: row.first_decided_at for i, row in view.rows(sorted(endpoints)).items()}
                  if hold is not None else {})
    # E950: the decided pairs are scored `score_chunk` at a time, in key order. A chunk's sides
    # read their CLIP vectors as it opens; as it closes its rows are written and every side no
    # later chunk decides gives its vectors and its feature context back (in a dense pass most
    # sides are decided again by a later chunk). Every pair is decided on the facts the unchunked
    # pass decided it on, so only WHEN a row reaches the store moves.
    ordered = sorted(wanted)
    opens, closes = _score_chunks(ordered, decide, working.fps, max(1, caps.score_chunk))
    rows: list[PairRow] = []
    moved: set[int] = set()
    result.redecided = len(redecide)
    for index, (lo, hi) in enumerate(ordered):
        if index % 256 == 255:
            _in_time(deadline, stopping)
        if index in opens:
            working.ensure(opens[index], clip=True, reason="decided")
        entry = wanted[(lo, hi)]
        if lo not in sides or hi not in sides:
            continue
        previous = stored.get((lo, hi))
        dlo = digests[lo]
        dhi = digests[hi]
        if (lo, hi) not in decide:
            # A stored decision that stands, or a reject the ledger answered (no row to touch).
            if previous is not None and (
                    set(previous.probes) != entry["probes"]
                    or previous.from_lo != entry["from_lo"]
                    or previous.from_hi != entry["from_hi"]):
                previous.probes = sorted(entry["probes"])
                previous.from_lo = bool(entry["from_lo"])
                previous.from_hi = bool(entry["from_hi"])
                rows.append(previous)
            continue
        if lo not in working.fps or hi not in working.fps:
            continue
        fa, fb = working.fps[lo], working.fps[hi]
        la, lb = working.listings[lo], working.listings[hi]
        if lo not in working.vectored or hi not in working.vectored:
            # E950: a side given back too early would score with no CLIP gallery, no tfidf and
            # no vectors in its evidence, which E93's hold covers only while the side is young.
            raise RuntimeError(f"pair ({lo}, {hi}) reached scoring without its sides' CLIP "
                               "vectors (E950)")
        feats: Feats = pair_features(fa, fb, la, lb, working.images.get(lo, ()),
                                     working.images.get(hi, ()), ctx, settings)
        decision = decide_pair(fa, fb, la, lb, feats, entry["probes"], model, settings, census)
        result.pairs_scored += 1
        evidence = dict(decision.evidence)
        if decision.certificate == "K-R":
            evidence["ref_codes"] = ",".join(ctx.shared_codes(lo, hi))
        # E93: a MERGE that does not rest on a photograph-free certificate waits while either
        # side's gallery exists and carries no hash at all. The zone written is the BAND — the
        # engine's own "look again" verdict — and what the pass decided is kept beside it so a
        # release is a re-decision and not a reconstruction.
        zone, reason, certificate = decision.zone, decision.reason, decision.certificate
        if (hold is not None and zone == "merge"
                and (certificate or "") not in PHOTO_FREE_CERTIFICATES
                and (hold.holds(evidence_of(working.images.get(lo, ())), decided_at.get(lo))
                     or hold.holds(evidence_of(working.images.get(hi, ())),
                                   decided_at.get(hi)))):
            evidence = {**evidence, "held_zone": "merge", "held_reason": decision.reason,
                        "held_certificate": certificate or ""}
            zone, reason, certificate = "band", EVIDENCE_HOLD_REASON, None
            result.held += 1
        elif previous is not None and previous.reason == EVIDENCE_HOLD_REASON:
            result.released += 1
        result.zones[zone] = result.zones.get(zone, 0) + 1
        if certificate:
            result.certificates[certificate] = (
                result.certificates.get(certificate, 0) + 1)
        rows.append(PairRow(
            lo=lo, hi=hi, probes=sorted(entry["probes"]),
            from_lo=bool(entry["from_lo"]), from_hi=bool(entry["from_hi"]),
            zone=zone, score=decision.score, families=sorted(decision.families),
            certificate=certificate, veto=decision.veto, reason=reason,
            evidence=evidence,
            # `block` is not part of PairContext: it is the per-block cap E64 counts on,
            # and the rail needs it off the stored row rather than off a live re-read.
            context={**census.pair_context(la, lb).to_json(),
                     "block": address_block_key(la)},
            fp_lo=dlo, fp_hi=dhi, feats=feats,
            slots={name: feats[name] for name in FEATURE_SLOTS if name in feats},
            dd_lo=dds[lo], dd_hi=dds[hi],
        ))
        if index in closes:
            moved |= _write_rows(store, rows, stored, result)
            result.chunk_rss_mb.append(rss_mb())
            result.chunk_vectored.append(len(working.vectored))
            working.release(closes[index])
            ctx.forget(closes[index])
    moved |= _write_rows(store, rows, stored, result)
    result.timings["score_s"] = time.perf_counter() - clock

    # --- 6. E72: re-cluster the touched components ----------------------------------------
    # Only a component whose MERGE edges or vetoes moved can cluster differently, so a pass
    # that scores a thousand rejects re-clusters nothing. A pair that merely changed its
    # probes or its band score leaves the component's edge set alone.
    clock = time.perf_counter()
    seeds: set[int] = set(moved)
    for key in dropped:
        previous = stored.get(key)
        if previous is not None and previous.zone == "merge":
            seeds |= set(key)
    _in_time(deadline, stopping)
    rulings = read_rulings(store)
    seeds |= _ruling_seeds(store, rulings)
    # A retired listing is not a seed: it has no postings, no pairs and no cell any more. Its
    # ex-partners are already seeds (they are the other side of every dropped edge), and they
    # are the component that has to re-cluster without it.
    seeds -= retired
    _recluster(store, facts, settings, working, seeds, caps, result, rulings)
    result.timings["cluster_s"] = time.perf_counter() - clock

    # --- 7. E64: a census that has overtaken a stamped promotion re-opens it to the band ---
    _in_time(deadline, stopping)
    clock = time.perf_counter()
    result.rail = _run_rail(store, facts, settings, working, result, sorted(touched_blocks),
                            rulings)
    result.timings["rail_s"] = time.perf_counter() - clock

    store.flush()
    result.reads = dict(working.read)
    if now is not None:
        for item in items:
            if item.arrived_at is not None:
                result.latency_s.append(max(0.0, now - item.arrived_at))
    result.cursors = work.commit(items)
    result.timings["total_s"] = sum(
        value for key, value in result.timings.items() if key.endswith("_s")
    )
    return result


def _score_chunks(ordered: Sequence[tuple[int, int]], decide: set[tuple[int, int]],
                  known: Container[int], size: int
                  ) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """E950: the scoring chunks of `ordered`, each at most `size` of the pairs a pass scores (the
    decided ones whose sides it holds). `opens` maps the index of a chunk's first such pair to
    the sides they read, `closes` the index of its last to the sides no later chunk reads,
    which it gives back once written."""
    opens: dict[int, set[int]] = {}
    ends: list[int] = []
    last: dict[int, int] = {}
    sides: set[int] = set()
    taken = 0
    for index, pair in enumerate(ordered):
        if pair not in decide or pair[0] not in known or pair[1] not in known:
            continue
        if taken % size == 0:
            sides = opens[index] = set()
            ends.append(index)
        taken += 1
        sides.update(pair)
        ends[-1] = index
        last[pair[0]] = last[pair[1]] = len(ends) - 1
    closes: dict[int, set[int]] = {index: set() for index in ends}
    for side, chunk in last.items():
        closes[ends[chunk]].add(side)
    return opens, closes


def _write_rows(store: Store, rows: list[PairRow], stored: Mapping[tuple[int, int], PairRow],
                result: PassResult) -> set[int]:
    """Write one chunk's rows and empty the list (E950); return the listings whose merge edges or
    vetoes they moved, which step 6 re-clusters from."""
    result.ledger_written += store.upsert_pairs(rows)
    result.pairs_written += len(rows)
    moved: set[int] = set()
    for row in rows:
        previous = stored.get((row.lo, row.hi))
        was = previous.zone if previous is not None else None
        if row.zone == "merge" or was == "merge" or row.veto == UNIT_DESIGNATOR_VETO:
            moved |= {row.lo, row.hi}
    rows.clear()
    return moved


def run_pass_bounded(
    store: Store,
    facts: FactSource,
    work: WorkSource,
    settings: Settings,
    model: LogisticModel,
    calibration: Calibration,
    limits: Limits | None = None,
    generation: str = GENERATION,
    now: float | None = None,
    shrink: int = 4,
    attempts: int = 5,
    hold: EvidenceHold | None = None,
    deadline: float | None = None,
    stopping: Callable[[], bool] | None = None,
) -> PassResult:
    """`run_pass`, re-claiming a SMALLER slice when the pair budget refused the last one.

    An aborted pass has written nothing and advanced no cursor (E75), so a retry is the same
    work over fewer arrivals rather than a resumption of a half-written one. The retry asks for
    a `shrink`th of the claim the source MADE (its `claim_bound["limit"]`, which a clock or a
    cap may have cut below `max_listings`; E952): a quarter of `max_listings` above that would
    re-claim the identical slice. When even a single-listing claim will not fit, the lane
    STOPS: a neighbourhood that alone exceeds the budget is a block worth an operator's eye,
    not a number to quietly truncate."""
    caps = limits or Limits()
    result = run_pass(store, facts, work, settings, model, calibration, caps, generation, now,
                      hold, deadline, stopping)
    wanted = [result.wanted_pairs]
    to_decide = [result.pairs_to_decide]
    reads = dict(result.reads)
    while result.aborted and len(wanted) < attempts:
        made = min(caps.max_listings, int(
            (getattr(work, "claim_bound", None) or {}).get("limit") or caps.max_listings))
        if made <= 1:
            break
        caps = replace(caps, max_listings=max(1, made // shrink))
        result = run_pass(store, facts, work, settings, model, calibration, caps, generation,
                          now, hold, deadline, stopping)
        wanted.append(result.wanted_pairs)
        to_decide.append(result.pairs_to_decide)
        for reason, count in result.reads.items():
            reads[reason] = reads.get(reason, 0) + count
    result.reads = reads
    result.attempts = len(wanted)
    result.wanted_by_attempt = wanted
    result.pairs_to_decide_by_attempt = to_decide
    return result


class _GuardLookup:
    """`guards.get(other)` over the working set first, the prefetched rows second, the store
    last — so retrieval never builds a fingerprint for a candidate the rule floor is about to
    refuse, and never spends a statement on one the pass already read."""

    def __init__(self, store: Store, local: Mapping[int, GuardRow],
                 prefetched: Mapping[int, GuardRow] | None = None) -> None:
        self.store = store
        self.local = local
        self.cache: dict[int, GuardRow | None] = dict(prefetched or {})

    def get(self, listing_id: int) -> GuardRow | None:
        row = self.local.get(listing_id)
        if row is not None:
            return row
        if listing_id in self.cache:
            return self.cache[listing_id]
        fetched = self.store.rows([listing_id]).get(listing_id)
        guard = fetched.guard if fetched is not None else None
        self.cache[listing_id] = guard
        return guard


@dataclass(frozen=True, slots=True)
class Rulings:
    """The operator's two binding answers, read once a pass: `different` (must-not-link) and
    `same` (must-link, Decision 8 / E910), the latter only between listings this generation
    holds — a ruling reaching outside the store cannot be clustered here. `changed` is every
    stored listing a ruling written since the previous pass names (G4, E920)."""

    must_not_link: frozenset[tuple[int, int]] = frozenset()
    must_link: frozenset[tuple[int, int]] = frozenset()
    changed: frozenset[int] = frozenset()

    def within(self, members: Iterable[int]) -> tuple[frozenset[tuple[int, int]],
                                                       frozenset[tuple[int, int]]]:
        inside = set(members)
        return (frozenset(p for p in self.must_not_link if p[0] in inside and p[1] in inside),
                frozenset(p for p in self.must_link if p[0] in inside and p[1] in inside))


def read_rulings(store: Store) -> Rulings:
    same = {(min(lo, hi), max(lo, hi)) for lo, hi in store.must_link() if lo != hi}
    changed = {int(i) for i in store.rulings_changed()}
    wanted = {i for pair in same for i in pair} | changed
    known = store.known(wanted) if wanted else set()
    return Rulings(frozenset(store.must_not_link()),
                   frozenset(p for p in same if p[0] in known and p[1] in known),
                   frozenset(changed & known))


def _ruling_seeds(store: Store, rulings: Rulings) -> set[int]:
    """Operator rulings that TODAY'S clusters contradict — a must-not-link inside one cluster,
    or a must-link across two (or outside any) — and every listing a ruling written since the
    previous pass names.

    A ruling added after the clustering it contradicts would otherwise never be honoured:
    re-clustering is seeded by pairs whose zone moved, and a ruling moves no pair. The sets are
    operator-curated and small, so one membership read per pass settles both. A must-link the
    invariants refuse (E910's dissolved closure) seeds its component every pass, which is the
    price of re-reading a contradiction rather than forgetting it. A CHANGED ruling contradicts
    nothing when it withdraws a word (G4, E920): the group a withdrawn `same` held together
    contradicts no ruling left, so only the change itself can re-cluster it before the
    reconcile merges it."""
    seeds: set[int] = set(rulings.changed)
    pairs = sorted(rulings.must_not_link | rulings.must_link)
    if not pairs:
        return seeds
    members: dict[int, int] = {}
    for key, ids in store.clusters_touching({i for pair in pairs for i in pair}).items():
        for listing_id in ids:
            members[listing_id] = key
    for lo, hi in rulings.must_not_link:
        if lo in members and members[lo] == members.get(hi):
            seeds |= {lo, hi}
    for lo, hi in rulings.must_link:
        if members.get(lo) is None or members.get(lo) != members.get(hi):
            seeds |= {lo, hi}
    return seeds


def _census(
    store: Store, listings: Mapping[int, Listing], images: Mapping[int, Sequence[Image]]
) -> ContextIndex:
    """The live census (E64), served from the store's counters for the working set's cells."""
    keys = {(address_block_key(listing), category_group(listing))
            for listing in listings.values()}
    cells = {key: row.cell() for key, row in store.cells(keys).items()}
    phashes = {
        listing_id: frozenset(img.phash for img in bucket if img.phash is not None)
        for listing_id, bucket in images.items()
        if any(img.phash is not None for img in bucket)
    }
    # The carrier count is read off the IMAGE, never recounted: `public.images.phash` carries
    # no index and D8 forbids adding one, so the corpus-wide count is one sequential scan the
    # cohort lane runs per pass into `autodedup.phash_pop` and this lane joins per image. That
    # makes it a cohort statistic like the other five, and E70 freezes it with them — a pass
    # that recounted it from what it happened to have seen would move `catalog_ratio`,
    # `anchor_bands` and with them the K4 probe and every certificate reading a catalogue ratio.
    population: dict[int, int] = {}
    for bucket in images.values():
        for image in bucket:
            if image.phash is None or image.pop is None:
                continue
            population[image.phash] = max(population.get(image.phash, 0), int(image.pop))
    return ContextIndex(
        cells=cells,
        listing_phashes=phashes,
        phash_population=population,
        from_price=frozenset(listing_id for listing_id, listing in listings.items()
                             if states_from_price(listing.description)),
    )


class _Reach:
    """Union-find over the merge edges one pass walks, so the whole seed set is expanded
    TOGETHER rather than one component at a time."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.count: dict[int, int] = {}

    def add(self, item: int) -> int:
        if item not in self.parent:
            self.parent[item] = item
            self.count[item] = 1
        return self.find(item)

    def find(self, item: int) -> int:
        parent = self.parent
        root = parent.setdefault(item, item)
        self.count.setdefault(item, 1)
        while root != parent[root]:
            parent[root] = parent[parent[root]]
            root = parent[root]
        parent[item] = root
        return root

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        small, large = (a, b) if self.count[a] < self.count[b] else (b, a)
        self.parent[small] = large
        self.count[large] += self.count[small]

    def size(self, item: int) -> int:
        return self.count[self.find(item)]

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for item in self.parent:
            out.setdefault(self.find(item), []).append(item)
        return {root: sorted(members) for root, members in out.items()}


def _components(
    store: Store, seeds: Iterable[int], max_size: int,
    must_link: Iterable[tuple[int, int]] = (),
) -> tuple[list[list[int]], list[int]]:
    """Connected components of the merge-edge graph reachable from the seeds, BFS-bounded.

    ONE `merge_neighbours` per BFS LEVEL for the whole seed set, not one per level per seed
    (E74). W9f's first live pass walked 700 components this way and paid 7,131 serial
    statements at ~96 ms of Frankfurt round trip each — 682 s of a 876 s pass — and the shape
    is per-pass, not per-cold-store: a rescope, a re-seed or a large delist batch moves the
    merge graph at that scale again.

    A component larger than the cap is NOT silently clustered from a partial member set — it is
    named on the pass summary and left to the cohort lane, because a partial component is the
    one input that would make E72's result differ from the batch pass's. It also stops growing
    the moment it passes the cap, which is what bounds this walk."""
    wanted = sorted(set(seeds))
    if not wanted:
        return [], []
    # E910: a must-link is an edge of the component graph like a merge edge — a closure is
    # clustered whole or not at all, so its members are one component's.
    linked: dict[int, set[int]] = {}
    for lo, hi in must_link:
        linked.setdefault(lo, set()).add(hi)
        linked.setdefault(hi, set()).add(lo)
    reach = _Reach()
    for seed in wanted:
        reach.add(seed)
    frontier = set(wanted)
    seen: set[int] = set()
    while frontier:
        seen |= frontier
        found: set[int] = set()
        neighbours = store.merge_neighbours(sorted(frontier))
        for listing_id in sorted(frontier):
            if linked.get(listing_id):
                neighbours.setdefault(listing_id, set())
                neighbours[listing_id] = set(neighbours[listing_id]) | linked[listing_id]
        for listing_id, others in neighbours.items():
            reach.add(listing_id)
            for other in others:
                reach.add(other)
                reach.union(listing_id, other)
            found |= others
        frontier = {i for i in (found - seen) if reach.size(i) <= max_size}
    out: list[list[int]] = []
    oversized: list[int] = []
    inside = set(wanted)
    for members in sorted(reach.groups().values()):
        if len(members) > max_size:
            oversized.extend(i for i in members if i in inside)
            continue
        out.append(members)
    return out, sorted(oversized)


def _recluster(
    store: Store,
    facts: FactSource,
    settings: Settings,
    working: _Working,
    seeds: Iterable[int],
    caps: Limits,
    result: PassResult,
    rulings: Rulings | None = None,
) -> None:
    """Re-cluster every touched component, in a fixed number of statements (E74).

    The per-component spelling cost ~10 serial round trips each — a fact read, `pairs_within`,
    `clusters_touching` and up to six writes — so a pass that moved 700 components spent its
    whole budget on latency. The components are computed in memory; the four reads and the one
    write are batched across all of them. A pair whose two sides fell in DIFFERENT components
    is nobody's edge (a merge edge would have joined them), which is exactly what the
    per-component `pairs_within` used to answer.

    The batch pass is reproduced BY CONSTRUCTION rather than by resemblance: a touched
    component is re-clustered from ALL of its stored edges through `cluster_pairs`, which
    sorts them by `edge_rank` before it unions anything and offers the refused bridges once in
    that same order — so nothing here grows a cluster edge by edge in arrival order, and the
    claim size a pass took cannot reach the partition. That holds exactly as far as
    `edge_rank` is a total order over the STORED rows (E115, E117)."""
    rulings = read_rulings(store) if rulings is None else rulings
    components, oversized = _components(store, seeds, caps.max_component, rulings.must_link)
    result.oversized_components = sorted(set(result.oversized_components) | set(oversized))
    result.components += len(components)
    if not components:
        return
    every = sorted({i for members in components for i in members})
    working.ensure(every, reason="cluster")
    of_component = {i: index for index, members in enumerate(components) for i in members}
    edges: list[list[PairRow]] = [[] for _ in components]
    # F2 (E909): the D43 relation the batch pass clusters with, read off the SAME rows the batch
    # keeps slots for (`storable`) — without it `d43_cluster_invariant` and both repartition
    # repairs ran here on no relation at all, a looser engine than the one the cohorts validated.
    slots: dict[tuple[int, int], dict[str, tuple[float, bool]]] = {}
    # E303: the price limb's K-C excuse reads the certificate each stored pair earned; this is
    # the relation's one production caller, so without it the dial was inert in every lane.
    certificates: dict[tuple[int, int], str] = {}
    for row in store.pairs_within(every):
        index = of_component.get(row.lo)
        if index is not None and index == of_component.get(row.hi):
            edges[index].append(row)
            if row.certificate:
                certificates[(row.lo, row.hi)] = row.certificate
            if storable({"zone": row.zone, "score": row.score, "evidence": row.evidence},
                        settings.store_floor):
                slots[(row.lo, row.hi)] = dict(row.slots or {})
    relation = relation_for(settings, working.listings, slots, certificates)
    touched = store.clusters_touching(every)

    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    keep: set[int] = set()
    for index, members in enumerate(components):
        pairs = edges[index]
        decisions = [row.decision() for row in pairs]
        vetoed = frozenset((row.lo, row.hi) for row in pairs
                           if row.veto == UNIT_DESIGNATOR_VETO)
        mnl, same = rulings.within(members)
        fps = {i: working.fps[i] for i in members if i in working.fps}
        listings = {i: working.listings[i] for i in members if i in working.listings}
        clustered = cluster_pairs(decisions, listings, fps, settings, mnl, relation,
                                  must_link=same, machine_vetoes=vetoed)
        rows.extend(cluster_rows(clustered, decisions, fps))
        keep |= set(clustered.clusters)
        # Refused unions AND refused bridges: §8 calls these the highest-value rows in the UI,
        # because a conflict is evidence in two directions. An APPLIED bridge is a union and
        # has nothing left to show.
        conflicts += [{**conflict, "kind": "invariant", "generation": result.generation}
                      for conflict in clustered.conflicts]
        conflicts += [{**bridge, "kind": "bridge", "generation": result.generation}
                      for bridge in clustered.bridges if not bridge.get("applied")]
        # E926: a dissolved must-link closure is a refused union too — the operator's rulings,
        # not an edge — and its `must_link` rows are what the rulings page reads it by.
        conflicts += [{**closure, "kind": "invariant", "generation": result.generation}
                      for closure in clustered.dissolved]
        result.dissolved.update(tuple(closure["members"]) for closure in clustered.dissolved)
    # A stale cluster is dropped once, by the WHOLE pass: the per-component spelling let one
    # component drop a key another had just kept, so which of the two ran last decided whether
    # the cluster survived.
    drop = sorted(key for key in touched if key not in keep)
    store.write_clusters(drop, rows, conflicts)
    result.cluster_keys.extend(int(row["cluster_key"]) for row in rows)
    result.clusters_written += len(rows)
    result.clusters_dropped += len(drop)


def _run_rail(
    store: Store,
    facts: FactSource,
    settings: Settings,
    working: _Working,
    result: PassResult,
    blocks: Sequence[str],
    rulings: Rulings | None = None,
) -> dict[str, int]:
    """E64: every stamped E63 promotion a census limb has since overtaken goes back to the band.

    Certificates are exempt by construction — a shared order code or a disjoint-window re-post
    is a per-pair FACT no later census can decay — and the re-opening is capped per block, so a
    block that doubles overnight re-opens a few merges rather than all of them. With every
    census limb off (the configuration W8's verification preferred) no census enters a warrant
    and this returns an empty plan, which is the rail costing nothing rather than not existing."""
    if (settings.context_rule_block_min is None
            and settings.context_rule_image_population_min is None) or not blocks:
        return {"reopened": 0, "deferred_by_cap": 0, "blocks_at_cap": 0, "blocks_touched": 0,
                "owed": 0}
    # Only the blocks whose census MOVED this pass: under a frozen calibration (E70) the image
    # population cannot change at all and a block counter changes only where a listing was
    # bumped, so a generation-wide scan would re-confirm what cannot have moved.
    stamped = store.stamped_merges(list(blocks))
    if not stamped:
        return {"reopened": 0, "deferred_by_cap": 0, "blocks_at_cap": 0, "blocks_touched": 0,
                "owed": 0}
    working.ensure({i for lo, hi, _stamp, _block, _cert in stamped for i in (lo, hi)},
                   reason="rail")
    merges = [(lo, hi, stamp, block) for lo, hi, stamp, block, _cert in stamped]
    exempt = frozenset((lo, hi) for lo, hi, _s, _b, cert in stamped if cert)
    live = _census(store, working.listings, working.images)
    actions, counters = rail_plan(
        merges, live, working.listings,
        block_min=settings.context_rule_block_min,
        image_population_min=settings.context_rule_image_population_min,
        max_per_block=settings.context_rail_max_reopen_per_block,
        exempt=exempt,
    )
    demoted: list[PairRow] = []
    live_rows = (store.pairs_touching({action.lo for action in actions}) if actions else {})
    for action in actions:
        row = live_rows.get((action.lo, action.hi))
        if row is None or row.zone != "merge":
            continue
        row.zone = "band"
        row.reason = f"{row.reason}:rail_reopen:{action.limb}"
        demoted.append(row)
    if demoted:
        store.upsert_pairs(demoted)
        _recluster(store, facts, settings, working,
                   {i for row in demoted for i in (row.lo, row.hi)},
                   Limits(), result, rulings)
    counters["owed"] = len(actions)
    return counters


def _parse_epoch(value: str | None) -> float | None:
    if not value:
        return None
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
