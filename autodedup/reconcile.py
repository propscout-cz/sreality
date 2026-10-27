"""A9 — the real-time lane's reconcile: the live stream's groups onto production, in the pass.

The worker's `autodedup` lane decides, groups AND merges in one pass under one lease
(`autodedup.rt_lease`): after the pass's transaction commits, `run` takes the groups it
re-clustered plus a slice swept past the `rt_reconcile` cursor and hands them to THE apply
path — `apply.plan_groups` (every refusal E903 names: operator negatives, categories, the scope,
carry-along, spans-groups, refused-before) and `apply.apply_group`
(`recheck_group` over locked rows, `merge_property_set(source='autodedup')` and the ledger row
in one transaction). Nothing here decides a refusal of its own; the reconcile is the batch
apply's brain run by the lane.

What it adds is WHICH groups and WHEN:
  * a group waits while any advert its merge would move — a member, or any advert of a property
    a member sits on — sits in a scope block the lane has not fully read (never walked, inside
    its first page cycle, or a snapshot row with no fingerprint), because a partly read block
    can be missing a member; an advert the snapshot does not hold yet is placed by its live
    location, and during a build one no scope block holds waits too (E964);
  * a group already on one property is a no-op, and one an operator negative now covers is
    reported (`ruled_different_after_merge`), never undone — the reconcile NEVER splits
    (Decision 9): a grouping the stream no longer supports is a proposal;
  * a skipped or refused row is filed only when a member set's outcome CHANGES — at plan time
    and at apply time alike — so a group
    waiting on the same rule files one row, not one a minute;
  * a group that failed `QUARANTINE_AFTER` passes running on an error nothing names is
    QUARANTINED (reported, not attempted) until `QUARANTINE_RETRY_H` after its last failure; an
    error inside one group never escapes the pass, and `MAX_ERRORS` of them stop the reconcile;
  * each group's transaction runs under its own local statement and lock timeouts;
  * it spends what is left of the pass's time and stops BETWEEN groups, never inside one, and
    merges at most the scope row's `max_clusters_per_run` groups a pass (the rest wait for the
    next pass, which re-plans them from fresh state);
  * a group waits (`stale_decision`) while a member not yet on its property was last refreshed
    more than `FRESH_DECISION_HOURS` ago, under another engine, or never (E960): a pass reads a
    neighbour as its row stored it, so a stored merge can rest on facts a sighting, a location
    correction or a reparse has since moved. Every group owed a merge and not merged this pass
    (waiting, past the run cap, out of time) is handed on (`rt_redecide`) in `_rank`'s order —
    Praha, the towns the scope names, the rest (E974), then flats for sale, flats for rent,
    houses and commercial for sale, the rest (E972, E974), the oldest moved member first within
    each, active and inactive groups together (E974); the next pass re-claims whole groups of it
    in that order, `STALE_FLOOR` adverts on top of its feeds, and the reconcile re-plans every
    group handed on, so a group refreshed whole merges at once.

The rollout control is unchanged until W6: `app_settings.autodedup_apply_scope` must admit the
group, and the WHOLE row is re-read before every group (blocks, deal types) and handed to the
apply-time re-check, so narrowing it stops the lane's merges between two groups. The brake is
the lane interval (0 = stop), then `mode=unapply`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Collection, Iterable, Mapping, NamedTuple, Sequence

from autodedup import apply as A
from autodedup import apply_sql as S
from autodedup import rt_lease
from autodedup.cohort import PRAHA_OBEC_KOD
from autodedup.incremental_sql import (
    RT_KNOWN_SQL,
    RT_SETTING_WRITE_SQL,
    RT_STALE_SQL,
    RT_CURSOR_READ_SQL,
    RT_CURSOR_SET_SQL,
    RT_LOCK_GUARD_SQL,
    RT_STATEMENT_GUARD_SQL,
)
from toolkit.property_identity import merge_property_set

CURSOR: str = "rt_reconcile"
RUN_PREFIX: str = "rt:"
# How many of the generation's groups one pass sweeps past the cursor, besides the ones it
# re-clustered. 200 was the trial scope's (~1,100 groups, a 6-pass cycle); since the Prague seed
# the store holds ~34k groups and the sweep is the only way a group no feed touches reaches the
# hand-on (E973: the discovery sets the pace), so at 200 the list ran dry (E975, 2026-10-21:
# 524 groups to 24 in two hours, merges to a third). 2,000 finds ~240 stale groups a pass at the
# measured 12 % share, three times what the re-claim (`STALE_FLOOR`, ~70 groups) takes a pass,
# so the list stays full, and a cycle is ~17 passes; the swept groups are planned in bulk
# (~36 s for 2,000) before the time-bound apply loop, so a late find is still handed on.
SWEEP_SLICE: int = 2000
# A group is started only while this much of the pass's time is left: one group is a handful
# of statements and one merge, and the reconcile stops between groups, never inside one.
GROUP_MARGIN_S: float = 30.0
# Each group's own transaction is bounded LOCALLY (the pass's guards ended with its commit):
# no statement outlives the margin above, and a lock an operator's merge holds is waited on
# for seconds, not for the rest of the pass.
GROUP_STATEMENT_TIMEOUT_MS: int = 25_000
GROUP_LOCK_TIMEOUT_MS: int = 5_000
# A member set whose newest QUARANTINE_AFTER outcomes are all `failed` is not attempted again
# until QUARANTINE_RETRY_H after the last of them; MAX_ERRORS errors nothing names stop one
# reconcile (a dead connection fails every group alike).
QUARANTINE_AFTER: int = 3
QUARANTINE_RETRY_H: float = 24.0
MAX_ERRORS: int = 3
WAITING = "block_not_fully_read"
# E960: a merge is applied only on facts at most this old. Half the sighting cycle (the walks
# see an active advert about once a day: 12,920 of 13,281 active within the store's first day),
# so an applied decision's clock features are off by at most half a day; a heal, a correction
# or a population move waits at most this long; and the groups a pass applies are mostly the
# ones it just re-clustered around a refreshed member, so the rule costs a pass of latency only
# where a member's facts are older.
# A policy window, for the operator to rule on.
FRESH_DECISION_HOURS: float = 12.0
STALE = "stale_decision"
REDECIDE_SETTING: str = "rt_redecide"
# The adverts the handed-on groups may name in all: whole groups, in `_rank`'s order (E972).
REDECIDE_CAP: int = 5000
# The adverts a pass re-claims for waiting groups, whatever its time-bound share: a share of 1
# (a pass of 9 adverts) would leave a backlog of thousands for days. They ride ON TOP of the
# feeds' limit, so this is what the worker's memory leaves beside the feeds (E971): ~6 GB plus
# ~4.5 GB a hundred claimed adverts against its 22,888 MB holds a pass to ~300 adverts, and the
# feeds brought 16-39 a pass on 2026-10-20. A pass the feeds overfill dies (the first after a
# long brake is the likeliest), and E948's cap, which bounds this budget too, and this arm's
# own pause and halving below answer the death.
STALE_FLOOR: int = 200
# A group the lane tried to re-claim is not tried again for this long, whatever came of it: a
# re-claim the pair budget refused, the deadline cut or a raise broke must not hold the head of
# the queue. Stamped once the pass that carried it ended on its own terms (E973): one the
# worker's shutdown, a lost lease or the system's kill took away is not stamped, so its groups
# lead the re-claim again once the paused pass is over (its count in flight, `rt_redecide_budget`,
# still pauses the next pass's re-claim and halves the one after).
TRIED_SETTING: str = "rt_redecide_tried"
TRIED_BACKOFF_HOURS: float = 6.0
# E972, the operator's Prague priority of 2026-10-20: the waiting groups are handed on and
# re-claimed flats for sale first, flats for rent second, the rest third, the oldest wait first
# within each; a group goes in its best member's class, so a flat with its komerční twin goes as
# the flat. An order only: the same groups are re-read, none is re-decided.
# E974, the operator's order of 2026-10-21: Praha first, then the towns the scope names with a
# `town:` block (eight that day), then the rest (the Středočeský kraj okresy to come, and an
# advert with no location row); within each region flats for sale, flats for rent, then houses
# and commercial for sale together, the rest last. Active and inactive groups go together: the
# level that put a group whose cluster holds an active advert before every group that holds none
# (added 2026-10-20 under E973) was retired by the operator's ruling of 2026-10-21 ("aktivní i
# neaktivní dohromady"). An order only, as E972.
CLAIM_ORDER: tuple[tuple[tuple[str, str], ...], ...] = (
    (("byt", "prodej"),), (("byt", "pronajem"),), (("dum", "prodej"), ("komercni", "prodej")))
REGION_PRAHA, REGION_TOWN, REGION_REST = 0, 1, 2


class Refresh(NamedTuple):
    """One advert as `refreshed` reads it."""

    at: Any                 # its last refresh; None without an rt_fp row
    stale: bool             # a merge may NOT rest on it (E960)
    place: int              # its tier in `CLAIM_ORDER` (E972, E974)
    region: int             # Praha, a scope town, the rest (E974)


# The re-claim's own budget, kept like the rate (E941): `{budget, inflight}`, the count a pass
# re-claims written AHEAD of it; a pass that finds a count still in flight (the last pass that
# carried re-claims died: its deadline, a kill, a raise) offers none and halves the budget, and
# a pass that commits with re-claims restores `STALE_FLOOR`; a pass takes at most the claim cap
# of it, which only a seed or a bigger container raises (E948b), so a death's lesson stays.
BUDGET_SETTING: str = "rt_redecide_budget"
QUARANTINED = "quarantined"
SUMMARY_CAP: int = 50


def _rows(conn: Any, sql: str, params: Mapping[str, Any]) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, dict(params))
        return list(cur.fetchall())


def _exec(conn: Any, sql: str, params: Mapping[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, dict(params))


def _cursor(conn: Any) -> int:
    for name, last_listing_id, _snapshot, _watermark in _rows(
            conn, RT_CURSOR_READ_SQL, {"names": [CURSOR]}):
        if name == CURSOR:
            return int(last_listing_id or 0)
    return 0


def read_scope(conn: Any) -> tuple[A.Scope | None, str | None]:
    """The operator's scope row, read NOW: the whole Scope when it admits a live merge, else
    None and why (E904: absent, malformed, or naming no deal types or no area is closed)."""
    try:
        scope = A.Scope(**A.scope_fields(A.read_scope_setting(conn)))
    except ValueError as exc:
        return None, str(exc)
    problems = scope.live_problems()
    return (None, "; ".join(problems)) if problems else (scope, None)


def _unread(conn: Any, generation: str, adverts: Sequence[A.Member],
            blocks: Sequence[str], *, building: bool = False,
            reads: tuple[set[str], set[str]] | None = None, riders: Iterable[int] = (),
            scope: A.Scope | None = None) -> set[int]:
    """The adverts among `adverts` whose scope block the lane has not fully read — a block never
    walked, inside its first cycle of pages, or whose snapshot still holds an advert with no
    fingerprint. An advert's block is the snapshot's; one the snapshot does not hold yet (a
    forward-feed arrival its block's walk has not reached, E964 review F3) is placed by its LIVE
    location in the scope block that holds it, and during a build one that no scope block holds
    either reads as unread (fail closed). `reads` is `apply.rt_block_reads`' answer when the
    caller has it.

    During a build (E977) a block whose walk is complete — read to its end, so its snapshot is
    whole — holds only its own unread adverts: an advert the snapshot places there and in no
    block still mid-walk is unread while it has no fingerprint, whatever else the block still
    owes. A block never walked or mid-walk holds every advert it places (its snapshot may lack
    some), an advert the snapshot does not place keeps F3's block rule, and outside a build the
    block rule stands for every block.

    `riders` are the adverts a merge would CARRY along, not members (E964 review R2): one this
    generation has never fingerprinted is unread whatever its block's state — it may have moved
    into a block after that block's last walk — when a scope block holds it or the apply `scope`
    admits it (an apply-scope block outside `rt_scope` lets in no advert the lane never read);
    one neither holds is the plan's refusal (`carries_out_of_scope`)."""
    unwalked, unfingerprinted = (A.rt_block_reads(conn, generation, blocks) if reads is None
                                 else reads)
    unread = unwalked | unfingerprinted
    riders = {int(i) for i in riders}
    if not adverts or not (unread or building or riders):
        return set()
    placed: dict[int, set[str]] = {}
    for lid, block in _rows(conn, S.RC_MEMBER_BLOCKS_SQL, {
            "generation": generation,
            "listing_ids": sorted({advert.listing_id for advert in adverts})}):
        placed.setdefault(int(lid), set()).add(str(block))
    # E977: the adverts judged by their own fingerprint — placed by the snapshot in a complete
    # block that still owes adverts, and in no block that is not complete.
    own = ({advert.listing_id for advert in adverts
            if placed.get(advert.listing_id)
            and not placed[advert.listing_id] & unwalked
            and placed[advert.listing_id] & unfingerprinted} if building else set())
    asked = riders | own
    known = ({int(row[0]) for row in _rows(conn, RT_KNOWN_SQL, {
        "generation": generation, "ids": sorted(asked)})} if asked else set())
    keys = set(blocks)
    out: set[int] = set()
    for advert in adverts:
        held = placed.get(advert.listing_id) or (keys & {
            f"obec:{advert.obec_kod}", f"cast_obce:{advert.cast_obce_kod}",
            f"okres:{advert.okres_kod}"})
        if advert.listing_id in riders and advert.listing_id not in known and (
                held or (scope is not None and scope.outside(advert) is None)):
            out.add(advert.listing_id)
        elif advert.listing_id in own:
            if advert.listing_id not in known:
                out.add(advert.listing_id)
        elif (held & unread) if held else building:
            out.add(advert.listing_id)
    return out


def _carried(conn: Any, members_by: Mapping[int, Sequence[A.Member]]
             ) -> dict[int, list[A.Member]]:
    """Every advert of every property a group's members sit on, by property (E964 review F2):
    a merge moves them all, so an unread one holds the group back as an unread member does."""
    out: dict[int, list[A.Member]] = {}
    for chunk in A._chunks({m.property_id for ms in members_by.values() for m in ms
                            if m.property_id is not None}):
        for row in _rows(conn, S.PROPERTY_LISTINGS_SQL, {"property_ids": chunk}):
            advert = A.Member.read(row)
            out.setdefault(int(row[1]), []).append(advert)
    return out


def redecide_key(generation: str) -> str:
    """`rt_redecide:<generation>` — the members the reconcile hands the next pass (E960)."""
    return f"{REDECIDE_SETTING}:{generation}"


def budget_key(generation: str) -> str:
    """`rt_redecide_budget:<generation>` — the re-claim's budget and its count in flight."""
    return f"{BUDGET_SETTING}:{generation}"


def tried_key(generation: str) -> str:
    """`rt_redecide_tried:<generation>` — {a group's first advert: when the lane tried it}."""
    return f"{TRIED_SETTING}:{generation}"


def scope_towns(scope: A.Scope) -> list[int]:
    """The town codes the scope names with a `town:` block (E974), as `read_scope` normalised
    them: the region's one source, so no second reader of the row can disagree with the apply."""
    return sorted(int(block.partition(":")[2]) for block in scope.blocks or ()
                  if block.startswith("town:"))


def refreshed(conn: Any, generation: str, listing_ids: Sequence[int], *, fresh_hours: float,
              engine_since: str | None, towns: Collection[int]) -> dict[int, Refresh]:
    """Each advert's last refresh, whether a merge may NOT rest on it (E960): refreshed longer
    ago than `fresh_hours`, before `engine_since`, or never (no row: fail closed), its tier in
    `CLAIM_ORDER` by its row's categories (E972) and its region by its live location row's town
    against `towns`, the scope's (`scope_towns`, E974)."""
    if not listing_ids:
        return {}
    named = frozenset(int(code) for code in towns)
    return {int(lid): Refresh(at, bool(stale), _claim_class(main, kind),
                              _region(obec_kod, named))
            for lid, at, stale, main, kind, obec_kod in _rows(conn, RT_STALE_SQL, {
                "generation": generation, "listing_ids": sorted({int(i) for i in listing_ids}),
                "fresh_s": float(fresh_hours) * 3600.0, "engine_since": engine_since})}


def stale_members(conn: Any, generation: str, listing_ids: Sequence[int], *,
                  fresh_hours: float, engine_since: str | None) -> set[int]:
    """The adverts among `listing_ids` a merge may not rest on (E960); staleness reads no
    region, so no towns."""
    return {listing_id for listing_id, row in refreshed(
        conn, generation, listing_ids, fresh_hours=fresh_hours,
        engine_since=engine_since, towns=()).items() if row.stale}


def _claim_class(category_main: str | None, category_type: str | None) -> int:
    """An advert's tier in `CLAIM_ORDER`; every other category after it."""
    pair = (category_main, category_type)
    return next((tier for tier, pairs in enumerate(CLAIM_ORDER) if pair in pairs),
                len(CLAIM_ORDER))


def _region(obec_kod: int | None, towns: Collection[int]) -> int:
    """An advert's region (E974): Praha, a town among the scope's `towns`, the rest (no
    location row included)."""
    if obec_kod is None:
        return REGION_REST
    if int(obec_kod) == PRAHA_OBEC_KOD:
        return REGION_PRAHA
    return REGION_TOWN if int(obec_kod) in towns else REGION_REST


def _rank(members: Sequence[int], ages: Mapping[int, Refresh]) -> tuple[int, int, int, Any]:
    """A group's place in the queue, over its moved members whichever of them are stale, so the
    re-claim and the hand-on's cap rank it alike: a group holding an advert with no row (no
    claim can refresh it) last of all, then its best member's region (E974) and tier in
    `CLAIM_ORDER` (E972, E974), then the oldest refresh first; whether its cluster holds an
    active advert does not rank it (E974, the operator's ruling of 2026-10-21)."""
    known = [ages[i] for i in members if i in ages]
    if not known or any(row.at is None for row in known):
        return (1, 0, 0, "")
    region, place = min((row.region, row.place) for row in known)
    return (0, region, place, min(row.at for row in known))


def claims(groups: Sequence[Sequence[int]], ages: Mapping[int, Refresh],
           budget: int) -> list[int]:
    """The adverts a pass re-claims for the groups handed on (E960): WHOLE groups — every stale
    member a claim can refresh (it has a row) — in `_rank`'s order, the hand-on's (E972), as
    many as fit in `budget` adverts: a group that does not fit is skipped for the next ones
    (E973), and the first is taken whatever its size, so no group is ever cut across passes."""
    queue = []
    for members in groups:
        stale = sorted({int(i) for i in members if int(i) in ages and ages[int(i)].stale
                        and ages[int(i)].at is not None})
        if stale:
            queue.append((_rank([int(i) for i in members], ages), stale))
    queue.sort()
    out: list[int] = []
    for _place, stale in queue:
        if out and len(out) + len(stale) > budget:
            continue
        out.extend(i for i in stale if i not in out)
    return out


def select_claims(groups: Sequence[Sequence[int]], ages: Mapping[int, Refresh],
                  tried: Mapping[str, str], now: datetime, budget: int = STALE_FLOOR
                  ) -> tuple[list[int], dict[str, str]]:
    """The adverts the next pass re-claims (`claims` at `budget`) over the groups not tried
    within `TRIED_BACKOFF_HOURS`, and the back-off map with those groups stamped `now` and the
    expired or forgotten stamps dropped (E960), for the lane to write once that pass ends on its
    own terms (E973). Keyed by a group's first advert."""
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    cutoff = now.timestamp() - TRIED_BACKOFF_HOURS * 3600.0
    queued = {str(min(group)) for group in groups if group}
    recent = {head: at for head, at in tried.items()
              if head in queued and _epoch(at) is not None and _epoch(at) > cutoff}
    open_groups = [group for group in groups if group and str(min(group)) not in recent]
    taken = claims(open_groups, ages, budget)
    chosen = set(taken)
    for group in open_groups:
        if chosen & set(group):
            recent[str(min(group))] = now.isoformat()
    return taken, recent


def _epoch(value: Any) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()


def handed_groups(value: Any) -> list[list[int]]:
    """`rt_redecide:<generation>` as written: lists of advert ids."""
    if not isinstance(value, list):
        return []
    return [[int(i) for i in group] for group in value
            if isinstance(group, list) and all(isinstance(i, int) for i in group)]


def _history(conn: Any, generation: str, listing_ids: Sequence[int]
             ) -> dict[frozenset[int], list[tuple[str, str | None, Any]]]:
    """Per member set, its newest outcomes (newest first): (outcome, error, at)."""
    if not listing_ids:
        return {}
    out: dict[frozenset[int], list[tuple[str, str | None, Any]]] = {}
    for members, outcome, error, at in _rows(conn, S.RC_OUTCOME_HISTORY_SQL, {
            "generation": generation, "listing_ids": sorted(listing_ids),
            "depth": QUARANTINE_AFTER}):
        out.setdefault(frozenset(int(x) for x in members), []).append(
            (str(outcome), error, at))
    return out


def _quarantined(past: Sequence[tuple[str, str | None, Any]], now: datetime) -> bool:
    streak = list(past[:QUARANTINE_AFTER])
    if len(streak) < QUARANTINE_AFTER or any(outcome != "failed" for outcome, _e, _a in streak):
        return False
    at = streak[0][2]
    if not isinstance(at, datetime):
        return True
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return (now - at).total_seconds() < QUARANTINE_RETRY_H * 3600.0


def run(
    conn: Any,
    generation: str,
    touched: Sequence[int],
    *,
    run_id: str,
    deadline: float,
    blocks: Sequence[str] = (),
    merge: Callable[..., dict[str, Any]] = merge_property_set,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    fence: Callable[[Any], None] | None = None,
    fresh_hours: float = FRESH_DECISION_HOURS,
    engine_since: str | None = None,
    waiting: Sequence[Sequence[int]] = (),
    building: bool = False,
) -> dict[str, Any]:
    """Reconcile the pass's groups onto production. Returns the readout the pass summary and
    the worker's heartbeat carry; writes only through `apply.apply_group` and the ledger.

    `building` says the generation is in its build phase (E964): an advert the snapshot does
    not place and no scope block holds by its live location then holds its group back, and a
    block whose walk is complete holds back only the groups of its own unread adverts (E977).

    `fence` (E941) is the lane's lease check, run last inside every group's transaction: a
    lease that ended under the reconcile — the worker's shutdown, a `release_lease=` dispatch —
    rolls that group back and stops the run (`stopped` = `lease_lost`)."""
    scope, closed = read_scope(conn)
    if scope is None:
        # E960: no merge can follow a re-claim, so the queue goes too (no pass re-claims it).
        if waiting:
            _hand_on(conn, generation, run_id, [], {})
        return {"skipped": "scope_closed", "reason": closed}
    after = _cursor(conn)
    swept = [int(row[0]) for row in _rows(conn, S.RC_SWEEP_SQL, {
        "generation": generation, "after": after, "limit": SWEEP_SLICE})]
    # A short slice is the end of the cycle: the cursor wraps, the next pass starts again.
    _exec(conn, RT_CURSOR_SET_SQL, {
        "name": CURSOR,
        "last_listing_id": swept[-1] if len(swept) >= SWEEP_SLICE else 0})
    def group_of(listing_ids: set[int]) -> dict[int, int]:
        return {int(lid): int(key) for lid, key in _rows(conn, S.RC_GROUP_OF_SQL, {
            "generation": generation, "listing_ids": sorted(listing_ids)})}

    # E960: every group handed on is re-planned, so one refreshed whole merges in this pass.
    handed = (set(group_of({int(i) for group in waiting for i in group}).values())
              if waiting else set())
    keys = sorted({int(k) for k in touched} | set(swept) | handed)
    counts: dict[str, Any] = {"groups": len(keys), "touched": len(set(touched)),
                              "swept": len(swept), "handed_replanned": len(handed)}
    out: dict[str, Any] = {"run_id": run_id, "counts": counts, "applied": [],
                           "skipped_at_apply": [], "refused": [], "failed": [],
                           QUARANTINED: []}
    if not keys:
        if waiting:
            counts["handed_on"], counts["handed_on_dropped"] = _hand_on(
                conn, generation, run_id, [], {})
        return out
    cluster_rows = [dict(zip(("cluster_key", "size", "status", "min_edge_score",
                              "model_version", "feature_version"), row))
                    for row in _rows(conn, S.RC_CLUSTERS_SQL,
                                     {"generation": generation, "keys": keys})]
    members_by: dict[int, list[A.Member]] = {}
    for row in _rows(conn, S.RC_MEMBERS_SQL, {"generation": generation, "keys": keys}):
        members_by.setdefault(int(row[0]), []).append(A.Member.read(row[1:]))
    # A group waits while any advert a merge would move — a member, or any advert of a property
    # a member sits on (E964 review F2: an unbuilt advert carried along is compared with nothing)
    # — sits in a block the lane has not fully read, or is carried along and was never
    # fingerprinted (R2); during a build, in a block whose walk is complete, only while that
    # advert itself has no fingerprint (E977).
    reads = A.rt_block_reads(conn, generation, blocks)
    carried = _carried(conn, members_by)
    adverts = {m.listing_id: m for ms in members_by.values() for m in ms}
    riders = {c.listing_id for cs in carried.values() for c in cs} - set(adverts)
    adverts.update((c.listing_id, c) for cs in carried.values() for c in cs)
    unread = _unread(conn, generation, list(adverts.values()), blocks, building=building,
                     reads=reads, riders=riders, scope=scope)
    if unread:
        waiting = {key for key, ms in members_by.items()
                   if any(m.listing_id in unread
                          or any(c.listing_id in unread for c in carried.get(m.property_id, ()))
                          for m in ms)}
        counts[WAITING] = len(waiting)
        cluster_rows = [row for row in cluster_rows if int(row["cluster_key"]) not in waiting]

    # The scope row's run cap holds here too, at most `max_clusters_per_run` merges a pass, but
    # over the groups that may merge NOW (E960): it is applied below, after the freshness rule.
    plan = A.plan_groups(conn, generation, cluster_rows, members_by, scope, group_of=group_of,
                         cap=False)
    counts.update(plan.counts)

    history = _history(conn, generation, sorted({lid for g in plan.groups
                                                 for lid in g.member_ids}))

    def last(group: A.GroupPlan) -> tuple[str, str | None] | None:
        past = history.get(frozenset(group.member_ids))
        return (past[0][0], past[0][1]) if past else None

    # A waiting or refused group files a row only when its outcome changed.
    changed = [g for g in plan.skipped if last(g) != ("skipped", g.reasons[0])]
    counts["skipped_rows_written"] = A.file_skipped(conn, run_id, generation, changed)
    out[A.RULED_AFTER_MERGE] = [A.ruled_brief(g) for g in plan.skipped
                                if g.ruled_after_merge][:SUMMARY_CAP]

    # E960: every member a merge would move must have been refreshed lately, under this engine;
    # the run cap then counts only the groups that may merge now.
    owed = {group.cluster_key: sorted(m.listing_id for m in members_by.get(group.cluster_key, ())
                                      if m.property_id != group.survivor_id)
            for group in plan.to_apply}
    ages = refreshed(conn, generation, sorted({i for ids in owed.values() for i in ids}),
                     fresh_hours=fresh_hours, engine_since=engine_since,
                     towns=scope_towns(scope))
    fresh = [group for group in plan.to_apply
             if not any(ages.get(i, (None, True))[1] for i in owed[group.cluster_key])]
    counts[STALE] = len(plan.to_apply) - len(fresh)
    todo = fresh[:scope.max_clusters_per_run]
    counts["deferred_run_cap"] = len(fresh) - len(todo)
    counts["planned"] = len(todo)
    for key in ("applied", "skipped_at_apply", "refused", "failed", QUARANTINED):
        counts[key] = 0
    counts["listings_moved"] = 0
    attempted: set[int] = set()
    errors = 0
    guards = ((RT_STATEMENT_GUARD_SQL, {"statement_timeout_ms": GROUP_STATEMENT_TIMEOUT_MS}),
              (RT_LOCK_GUARD_SQL, {"lock_timeout_ms": GROUP_LOCK_TIMEOUT_MS}))
    for index, group in enumerate(todo):
        if clock() >= deadline - GROUP_MARGIN_S:
            out["stopped"] = "the pass's time is spent"
            counts["not_attempted"] = len(todo) - index
            break
        # E39: the operator's WHOLE scope row, read fresh before every group, is the one the
        # group's re-check reads over its locked rows.
        scope, closed = read_scope(conn)
        if scope is None:
            out["stopped"] = f"app_settings.{A.SCOPE_SETTING} admits no live merge: {closed}"
            counts["not_attempted"] = len(todo) - index
            break
        attempted.add(group.cluster_key)
        past = history.get(frozenset(group.member_ids), [])
        if _quarantined(past, now()):
            counts[QUARANTINED] += 1
            if len(out[QUARANTINED]) < SUMMARY_CAP:
                out[QUARANTINED].append(A.group_brief(group, error=past[0][1]))
            continue
        try:
            outcome, brief = A.apply_group(conn, group, scope, run_id=run_id,
                                           generation=generation, merge=merge,
                                           last=last(group), guards=guards, fence=fence)
        except rt_lease.LeaseLost:
            # The group rolled back and filed nothing; the lease is someone else's now.
            out["stopped"] = rt_lease.LEASE_LOST
            counts["not_attempted"] = len(todo) - index
            break
        except Exception as exc:  # noqa: BLE001 — recorded by apply_group, counted here
            errors += 1
            counts["failed"] += 1
            if len(out["failed"]) < SUMMARY_CAP:
                out["failed"].append(A.group_brief(group, error=f"{type(exc).__name__}: {exc}"))
            if errors >= MAX_ERRORS:
                out["stopped"] = f"{errors} errors nothing names; the last: {type(exc).__name__}"
                counts["not_attempted"] = len(todo) - index - 1
                break
            continue
        counts[outcome] += 1
        if outcome == "applied":
            counts["listings_moved"] += int(brief["listings_moved"])
        if len(out[outcome]) < SUMMARY_CAP:
            out[outcome].append(brief)
    counts["handed_on"], counts["handed_on_dropped"] = _hand_on(
        conn, generation, run_id,
        [owed[g.cluster_key] for g in plan.to_apply
         if g.cluster_key not in attempted and owed[g.cluster_key]], ages)
    return out


def _hand_on(conn: Any, generation: str, run_id: str, groups: Sequence[Sequence[int]],
             ages: Mapping[int, Refresh]) -> tuple[int, int]:
    """Every group owed a merge and not attempted (waiting, past the run cap, out of time), as its
    moved members, in the re-claim's order (`_rank`), whole groups up to `REDECIDE_CAP` adverts
    (E960); a full list drops the rest's region before any town's and a town's before Praha's,
    active and inactive groups alike (E974), and within a region the last tier's newest waits
    first (E972, E974). It REPLACES the list: every group on it was re-planned this pass, so what
    stays owed is here."""
    kept: list[list[int]] = []
    total = 0
    for _place, members in sorted((_rank(m, ages), list(m)) for m in groups):
        if total + len(members) > REDECIDE_CAP:
            break
        kept.append(members)
        total += len(members)
    _exec(conn, RT_SETTING_WRITE_SQL, {"key": redecide_key(generation),
                                       "value": json.dumps(kept), "updated_by": run_id})
    return len(kept), len(groups) - len(kept)
