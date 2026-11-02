"""The wave shadow: a location wave's movers through the engine before the wave deploys.

    python3 -m location_data.what_if --ids-sql "<SELECT>" --scope apply --out shadow/w4 ...
    python3 -m autodedup.shadow shadow/w4
    python3 -m autodedup.harness shadow shadow/w4/base/cohort.jsonl.gz shadow/w4/positions.jsonl \\
        shadow/w4/labels --scorer shadow/w4/scorer.json --out shadow/w4/judge

The engine reads every position through `COHORT_LOCATION_SQL` and `export.build_location`, so the
seam is the cohort artifact (`DECISION_MODULES` untouched). `main` is step 2: the movers' blocks,
sized and capped first, then the cohort, the rulings and the live scorer, exported IN-PROCESS on a
READ ONLY connection, so `lane.run` never writes its `autodedup.iterations` row. `read_cursor` is
the only way this module and `location_data.what_if` reach SQL. `override` and `judge` are step 3's
halves (`harness shadow`). Design: `docs/design/location-quality/wave-shadow.md`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, NamedTuple

import psycopg

from autodedup.apply_scope import SCOPE_SETTING, Member, Scope, scope_fields
from autodedup.apply_sql import SETTING_SQL
from autodedup.blocking import SPLIT_CITY_OBEC_KODS, BlockIndex
from autodedup.cohort import ids_block, parse_blocks
from autodedup.dataset import Listing
from autodedup.evaluate import DIFFERENT, RULINGS_FILES, SAME, RunView, read_rulings
from autodedup.export import ARTIFACT_NAME, build_location, fetch_block_ids, run_export
from autodedup.features import haversine_m
from autodedup.fingerprint import Fingerprint, pin_key_of
from autodedup.incremental import GENERATION
from autodedup.indistinguishable import _cp_of, _filed_apart, stored_house_number_conflict
from autodedup.incremental_sql import RT_CALIBRATION_SCORER_SQL
from autodedup.labels import PairKey, pair_key
from autodedup.labels_lane import DEFAULT_GENERATION, run_labels
from autodedup.settings import Settings

POSITIONS_FILE: str = "positions.jsonl"
MOVERS_FILE: str = "movers.jsonl"
WHAT_IF_FILE: str = "what_if.json"
SCORER_FILE: str = "scorer.json"
SHADOW_FILE: str = "shadow.json"
GROUP_FILE: str = "group.json"
D83_EDGES: tuple[str, ...] = ("merge_gained", "merge_lost", "copairs_gained", "copairs_lost")
MAX_ADS: int = 60_000
MAX_BLOCKS: int = 40
# The operator's Browse merges since 2026-10-27: 413 `same` rulings at scoping, reported apart.
RECENT_SINCE: str = "2026-10-27"
_BLOCK_TIMEOUT_MS: int = 600_000

# One advert's apply-scope facts by listing id, in `Member` order (`PROPERTY_LISTINGS_SQL`'s).
_SCOPE_FACTS_SQL = """
select l.id, l.property_id, l.category_type, l.category_main, ll.obec_kod, ll.cast_obce_kod,
       ll.okres_kod
  from public.listings l
  left join public.listing_location ll on ll.listing_id = l.id
 where l.id = any(%(ids)s::bigint[])
"""

# The ads a ruling or a property ties to the listed ones, every ruling the judge reads
# (`evaluate.read_rulings`): the other side of a pair verdict or an operator must-not-link, the
# members of a group verdict or an operator merge, the sibling ads of their property.
_TIES_SQL = """
select case when p.lo = any(%(ids)s::bigint[]) then p.hi else p.lo end
  from (select v.listing_lo, v.listing_hi from autodedup.verdicts v where v.kind = 'pair'
        union all
        select n.listing_lo, n.listing_hi from autodedup.must_not_link n
         where n.source = 'operator') p (lo, hi)
 where p.lo = any(%(ids)s::bigint[]) or p.hi = any(%(ids)s::bigint[])
union
select unnest(g.member_ids)
  from (select v.member_ids from autodedup.verdicts v where v.kind = 'cluster'
        union all
        select m.member_ids from autodedup.operator_merges m) g
 where g.member_ids && %(ids)s::bigint[]
union
select s.id from public.listings l join public.listings s on s.property_id = l.property_id
 where l.id = any(%(ids)s::bigint[])
"""


# Every block (E300's export grain) that holds an advert some ruling names, today: the sources the
# labels lane exports (pair verdicts, the cluster-grain verdicts' member sets, the operator's
# must-not-links, the Browse merges). A block holding none can move no ruling, so it cannot fail.
RULED_BLOCKS_SQL = """
with ruled as (
    select v.listing_lo as listing_id from autodedup.verdicts v
     where v.kind = 'pair' and v.listing_lo is not null and v.listing_hi is not null
    union
    select v.listing_hi from autodedup.verdicts v
     where v.kind = 'pair' and v.listing_lo is not null and v.listing_hi is not null
    union
    select unnest(v.member_ids) from autodedup.verdicts v
     where v.kind = 'cluster' and v.member_ids is not null
    union
    select m.listing_id from autodedup.verdicts v
      join autodedup.cluster_members m
        on m.cluster_key = v.cluster_key and m.generation = %(generation)s::text
     where v.kind = 'cluster' and v.member_ids is null
    union
    select n.listing_lo from autodedup.must_not_link n where n.source = 'operator'
    union
    select n.listing_hi from autodedup.must_not_link n where n.source = 'operator'
    union
    select unnest(o.member_ids) from autodedup.operator_merges o
)
select distinct case when ll.obec_kod = any(%(split)s::bigint[])
                     then 'quarter:' || ll.cast_obce_kod
                     else 'town:' || ll.obec_kod end as block
  from ruled r
  join public.listing_location ll on ll.listing_id = r.listing_id
 where ll.obec_kod is not null
   and (ll.cast_obce_kod is not null or not ll.obec_kod = any(%(split)s::bigint[]))
"""


@contextmanager
def read_cursor(conn: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    """The wave shadow's one way to a cursor: its own transaction, READ ONLY on that conn."""
    with conn.transaction(), conn.cursor() as cur:
        yield cur


def read(conn: psycopg.Connection, sql: str, params: Any = None, *, binary: bool | None = None
         ) -> tuple[list[str], list[tuple[Any, ...]]]:
    """One statement through `read_cursor`: its column names and rows."""
    with read_cursor(conn) as cur:
        cur.execute(sql, params, binary=binary)
        return [column.name for column in cur.description or ()], cur.fetchall()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def in_apply_scope(conn: psycopg.Connection, listing_ids: Iterable[int]) -> list[int]:
    """The ids the live apply scope admits, by its own predicate (`apply_scope.Scope.outside`)."""
    _, rows = read(conn, SETTING_SQL, {"key": SCOPE_SETTING})
    value = rows[0][0] if rows else None
    scope = Scope(**scope_fields(json.loads(value) if isinstance(value, str) else value))
    if problems := scope.live_problems():
        raise SystemExit(f"{SCOPE_SETTING} admits nothing: {'; '.join(problems)}")
    _, rows = read(conn, _SCOPE_FACTS_SQL, {"ids": list(listing_ids)})
    return sorted(int(row[0]) for row in rows if scope.outside(Member.read(row)) is None)


def tied_ads(conn: psycopg.Connection, listing_ids: Iterable[int]) -> list[int]:
    """`_TIES_SQL` over the ids, read only."""
    _, rows = read(conn, _TIES_SQL, {"ids": sorted(set(listing_ids))})
    return sorted({int(row[0]) for row in rows if row[0] is not None})


def side_block(side: dict[str, Any] | None) -> str | None:
    """A row's block at E300's grain: its quarter in a split city, else its town; None when
    it has no town, or sits in a split city with no quarter (its candidates are the whole city)."""
    if not side or side.get("obec_kod") is None:
        return None
    if int(side["obec_kod"]) not in SPLIT_CITY_OBEC_KODS:
        return f"town:{int(side['obec_kod'])}"
    if side.get("cast_obce_kod") is not None:
        return f"quarter:{int(side['cast_obce_kod'])}"
    return None


def _unquartered(mover: dict[str, Any]) -> bool:
    """A side in a split city with no quarter: its candidates are the whole city."""
    return any(side and side.get("obec_kod") is not None and side_block(side) is None
               for side in (mover.get("old"), mover.get("new")))


def blocks_for(movers: Iterable[dict[str, Any]], ties: Callable[[list[int]], Iterable[int]]
               ) -> list[str]:
    """Each mover's old and new block; a mover with an unquartered side rides ONE `ids` block
    with every ad `ties` binds to it (`tied_ads`), so both arms hold it and its rulings."""
    keys: dict[str, None] = {}
    listed: list[int] = []
    for mover in movers:
        for side in (mover.get("old"), mover.get("new")):
            if (block := side_block(side)) is not None:
                keys[block] = None
        if _unquartered(mover):
            listed.append(int(mover["listing_id"]))
    if listed:
        keys[ids_block([*listed, *ties(sorted(set(listed)))])] = None
    return list(keys)


def ruled_blocks(conn: psycopg.Connection) -> set[str]:
    """The blocks holding an advert some ruling names (`RULED_BLOCKS_SQL`)."""
    _, rows = read(conn, RULED_BLOCKS_SQL, {"split": sorted(SPLIT_CITY_OBEC_KODS),
                                            "generation": DEFAULT_GENERATION})
    return {str(row[0]) for row in rows}


def judgeable(movers: Iterable[dict[str, Any]], ruled: set[str]) -> tuple[set[str], set[int]]:
    """The blocks to export and the movers no ruling can see. A mover with a side in a ruled block
    brings BOTH its blocks: it reaches the cohort only through its old one. A mover with an
    unquartered side rides the `ids` block, which is always exported, so it is never unseen."""
    keep: set[str] = set()
    blind: set[int] = set()
    for mover in movers:
        sides = [block for block in (side_block(mover.get("old")), side_block(mover.get("new")))
                 if block is not None]
        if any(block in ruled for block in sides):
            keep.update(sides)
        elif not _unquartered(mover):
            blind.add(int(mover["listing_id"]))
    return keep, blind


def parse_group(text: str) -> tuple[int, int]:
    """`i/n`, 1 <= i <= n: the i-th of n contiguous block groups."""
    index, _, count = str(text).partition("/")
    if not (index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count)):
        raise argparse.ArgumentTypeError(f"--block-group must be i/n with 1 <= i <= n: {text!r}")
    return int(index), int(count)


def chunks(blocks: Iterable[str], count: int) -> list[list[str]]:
    """The blocks sorted by key and cut into `count` contiguous groups, the first ones a block
    longer when they do not divide evenly."""
    ordered = sorted(blocks)
    size, extra = divmod(len(ordered), count)
    out, at = [], 0
    for index in range(count):
        step = size + (1 if index < extra else 0)
        out.append(ordered[at:at + step])
        at += step
    return out


def groups_needed(sizes: dict[str, int], max_blocks: int, max_ads: int) -> int | None:
    """The fewest groups whose every chunk fits both caps; None when one block alone does not."""
    if any(ads > max_ads for ads in sizes.values()):
        return None
    for count in range(1, len(sizes) + 1):
        if all(len(chunk) <= max_blocks and sum(sizes[b] for b in chunk) <= max_ads
               for chunk in chunks(sizes, count)):
            return count
    return None


def outside_group(movers: Path, group: dict[str, Any]) -> set[int]:
    """The movers another group exports (by the old block, or the `ids` block an unquartered one
    rides): not judged here, and not unjudged."""
    chunk = set(group.get("blocks") or ())
    if not chunk:
        return set()
    listed_here = {i for block in parse_blocks(" ".join(sorted(chunk))) for i in block.ids}
    out: set[int] = set()
    for mover in _jsonl(movers):
        listing_id, old = int(mover["listing_id"]), side_block(mover.get("old"))
        exported_somewhere = old is not None or _unquartered(mover)
        exported_here = old in chunk or listing_id in listed_here
        if exported_somewhere and not exported_here:
            out.add(listing_id)
    return out


def block_sizes(conn: psycopg.Connection, blocks: list[str]) -> dict[str, int]:
    """Each block's ad count, selected exactly as the export will select it."""
    return {text: len(fetch_block_ids(conn, block, timeout_ms=_BLOCK_TIMEOUT_MS, negctl_max=1)[0])
            for text, block in zip(blocks, parse_blocks(" ".join(blocks)))}


def export_cohort(conn_factory: Callable[[], Any], shadow_dir: Path, blocks: list[str]
                  ) -> dict[str, Any]:
    """Step 2's export: the base cohort over `blocks`, the rulings and the live scorer."""
    if not blocks:
        raise ValueError("no block to export (an empty list would mean the trial cohort)")
    cohort = run_export(conn_factory, {"blocks": " ".join(blocks)}, shadow_dir / "base")
    labels = run_labels(conn_factory, {}, shadow_dir / "labels")
    conn = conn_factory()
    try:
        _, rows = read(conn, RT_CALIBRATION_SCORER_SQL, {"generation": GENERATION})
    finally:
        conn.close()
    if not rows:
        raise SystemExit(f"generation {GENERATION!r} has no calibration row: no live scorer")
    settings = rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0] or "{}")
    (shadow_dir / SCORER_FILE).write_text(json.dumps(
        {"generation": GENERATION, "settings": settings, "model_version": rows[0][1]},
        indent=1, sort_keys=True), encoding="utf-8")
    return {"blocks": blocks, "cohort": cohort["counts"], "labels": labels["counts"]}


class Override(NamedTuple):
    """What `override` did: the ads listed, the ads in the cohort, the ads whose block changed."""

    listed: set[int]
    cohort: set[int]
    changed: set[int]


def override(base: Path, positions: Path, out: Path) -> Override:
    """`base` with the listed blocks rebuilt by the export's builder, written to `out`."""
    rows = {int(row["listing_id"]): row for row in _jsonl(positions)}
    cohort: set[int] = set()
    changed: set[int] = set()
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(base, "rt", encoding="utf-8") as src, \
            gzip.open(out, "wt", encoding="utf-8", newline="\n") as dst:
        for line in src:
            record = json.loads(line)
            if record.get("t") == "listing":
                cohort.add(record["id"])
                if record["id"] in rows:
                    block = build_location(rows[record["id"]])
                    if block != record["location"]:
                        changed.add(record["id"])
                        record["location"] = block
                        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
            dst.write(line)
    return Override(set(rows), cohort, changed)


def recent_same(rulings: Path, since: str = RECENT_SINCE) -> set[PairKey]:
    """The `same` rulings written since `since` that are still their pair's newest word."""
    newest = read_rulings(rulings)
    stamped = [(row.get("decided_at"), pair_key(row["listing_lo"], row["listing_hi"]))
               for row in _jsonl(rulings / RULINGS_FILES[0]) if row.get("verdict") == SAME]
    for row in _jsonl(rulings / RULINGS_FILES[1]):
        members = sorted({int(member["listing_id"]) for member in row.get("members") or ()})
        stamped += [(row.get("merged_at"), (a, b))
                    for i, a in enumerate(members) for b in members[i + 1:]]
    return {key for stamp, key in stamped if str(stamp or "") >= since and newest.get(key) == SAME}


def judge(report: dict[str, Any], done: Override, *, base_e111: dict[str, Any],
          together: set[PairKey], recent: set[PairKey], what_if: dict[str, Any],
          elsewhere: set[int] = frozenset(), unjudgeable: set[int] = frozenset()
          ) -> dict[str, Any]:
    """PASS = no NEW operator negative inside a K-B family and no ruled pair moved against its
    ruling; E111's standing state (both arms), D83 and the recent pairs are reported."""
    lists = report["d83"]["lists"]
    broken = ([row for row in lists["copairs_lost"] if row[2] == SAME]
              + [row for row in lists["copairs_gained"] if row[2] == DIFFERENT])
    honoured = ([row for row in lists["copairs_gained"] if row[2] == SAME]
                + [row for row in lists["copairs_lost"] if row[2] == DIFFERENT])
    candidate_e111 = report["revocation"]
    new = sorted({tuple(pair) for pair in candidate_e111["negatives"]}
                 - {tuple(pair) for pair in base_e111["negatives"]})
    judged = {key for key in recent if key[0] in done.cohort and key[1] in done.cohort}
    split = judged & {(int(row[0]), int(row[1])) for row in broken}
    held = judged & together
    blind = set(unjudgeable) & done.listed
    unjudged_movers = len(done.listed - done.cohort - set(elsewhere) - blind)
    return {
        "pass": not new and not broken,
        "partial": bool(what_if.get("limit")) or unjudged_movers > 0,
        "run": what_if,
        "e111": {"base": base_e111, "candidate": candidate_e111,
                 "new_negatives": [list(pair) for pair in new]},
        "broken_rulings": broken,
        "honoured_rulings": honoured,
        "movers_listed": len(done.listed),
        "movers_in_cohort": len(done.changed),
        "movers_unjudged": unjudged_movers,
        "movers_elsewhere": len((set(elsewhere) - blind) & done.listed),
        "movers_unjudgeable": len(blind),
        "recent_same": {"pairs": len(recent), "judged": len(judged),
                        "held": len(held), "broken": len(split),
                        "apart_in_both": len(judged - held - split),
                        "unjudged": len(recent - judged)},
        "d83_touching_movers": {name: [row for row in lists[name]
                                       if row[0] in done.changed or row[1] in done.changed]
                                for name in D83_EDGES},
    }


class Arm(NamedTuple):
    """One arm of the gate: what `harness run` wrote, over which adverts and fingerprints."""

    run: RunView
    listings: Mapping[int, Listing]
    fps: Mapping[int, Fingerprint]


def _blockers(arm: Arm, changed: set[int], settings: Settings) -> tuple[dict[PairKey, Any], ...]:
    """(E242, E272) over the stored rows touching a mover: the two door readings that can keep a
    pair apart. E272 is `_filed_apart` on a pair stating two storeys, the case its excuse removal
    can reach."""
    e242: dict[PairKey, Any] = {}
    e272: dict[PairKey, Any] = {}
    for key, row in arm.run.rows.items():
        if key[0] not in changed and key[1] not in changed:
            continue
        a, b = arm.listings[key[0]], arm.listings[key[1]]
        if stored_house_number_conflict(a, b, settings) is not None:
            e242[key] = row.get("zone")
        if (a.floor is not None and b.floor is not None and a.floor != b.floor
                and _filed_apart(a, b, settings)):
            e272[key] = row.get("zone")
    return e242, e272


def _moved(base: dict[PairKey, Any], candidate: dict[PairKey, Any]) -> dict[str, Any]:
    return {"base": len(base), "candidate": len(candidate),
            "new": [[*key, candidate[key]] for key in sorted(set(candidate) - set(base))],
            "gone": [[*key, base[key]] for key in sorted(set(base) - set(candidate))]}


def _position_moved(base: Arm, candidate: Arm, changed: set[int]) -> dict[str, Any]:
    """Movers whose `pin_key` changes, as `[listing_id, metres]`: a row BIND placed on its street's
    point because its exact pin was off the street stands on that pin once the pin names a door."""
    moved: list[list[Any]] = []
    for i in sorted(changed):
        was, now = base.listings[i].location, candidate.listings[i].location
        if pin_key_of(was.lat, was.lon) != pin_key_of(now.lat, now.lon):
            known = None not in (was.lat, was.lon, now.lat, now.lon)
            moved.append([i, round(haversine_m(was.lat, was.lon, now.lat, now.lon), 1) if known else None])
    return {"count": len(moved), "listings": moved}


def _index(fps: Mapping[int, Fingerprint], settings: Settings) -> BlockIndex:
    index = BlockIndex(settings)
    for listing_id in sorted(fps):
        index.add(fps[listing_id])
    return index.finalize()


def _addr_key(index: BlockIndex, fp: Fingerprint) -> Any:
    return next((key for probe, key in index.index_keys(fp) if probe == "addr"), None)


def _two_doors(a: Listing, b: Listing) -> tuple[int, int] | None:
    """One building holding two different doors. The mirror has no building code, so č.p. + část
    is the proxy (the cohort's `cast_obce_kod` is 1:1 with the unit id)."""
    left, right = a.location, b.location
    if (left.ruian_adm_kod and right.ruian_adm_kod and left.ruian_adm_kod != right.ruian_adm_kod
            and left.cast_obce_kod and left.cast_obce_kod == right.cast_obce_kod
            and _cp_of(left.house_number) and _cp_of(left.house_number) == _cp_of(right.house_number)):
        return left.ruian_adm_kod, right.ruian_adm_kod
    return None


def door_report(base: Arm, candidate: Arm, lists: Mapping[str, Any], changed: set[int],
                settings: Settings) -> dict[str, Any]:
    """What a door does to the engine, base against candidate, with the pair ids. It never decides:
    PASS stays `judge`'s. Blockers and `same_house_number` read the STORED rows, so a pair under the
    store floor is not in them; retrieval (`addr_new`, `cap_displaced`, the two-door pairs) reads
    every candidate pair, off the cohort pass's own `BlockIndex` in each arm."""
    gained = {i for i in changed if base.listings[i].location.ruian_adm_kod is None
              and candidate.listings[i].location.ruian_adm_kod is not None}
    e242_base, e272_base = _blockers(base, changed, settings)
    e242_candidate, e272_candidate = _blockers(candidate, changed, settings)
    to_one = []
    for key, row in sorted(candidate.run.rows.items()):
        value, present = (row.get("feats") or {}).get("same_house_number", (0.0, False))
        was = (base.run.rows.get(key, {}).get("feats") or {}).get("same_house_number", (0.0, False))
        if present and value == 1.0 and not (was[1] and was[0] == 1.0):
            to_one.append([*key, row.get("zone")])
    index_base, index_candidate = _index(base.fps, settings), _index(candidate.fps, settings)
    addr_base = {i: _addr_key(index_base, fp) for i, fp in base.fps.items()}
    addr_new: set[PairKey] = set()
    doors: dict[PairKey, tuple[int, int]] = {}
    at_cap: dict[int, set[int]] = {}
    for i, fp in candidate.fps.items():
        found = index_candidate.candidates(fp)
        if len(found) >= settings.max_candidates_per_listing:
            at_cap[i] = set(found)
        for other, probes in found.items():
            key = pair_key(i, other)
            if "addr" in probes and (addr_base.get(i) is None or addr_base[i] != addr_base.get(other)):
                addr_new.add(key)
            if (i in gained or other in gained) and key not in doors:
                if (pair := _two_doors(candidate.listings[key[0]], candidate.listings[key[1]])):
                    doors[key] = pair
    displaced: list[list[Any]] = []
    for i, found in sorted(at_cap.items()):
        fp = candidate.fps[i]
        for other in sorted(set(index_base.candidates(base.fps[i])) - found):
            shared = set(index_candidate.probe_keys(fp)) & set(index_candidate.index_keys(candidate.fps[other]))
            if (any(token not in index_candidate.exploded[probe] for probe, token in shared)
                    and i not in index_candidate.candidates(candidate.fps[other])):
                key = pair_key(i, other)
                displaced.append([*key, base.run.rows.get(key, {}).get("zone")])
    zone = lambda key: candidate.run.rows.get(key, {}).get("zone")  # noqa: E731
    return {
        "door_gainers": len(gained),
        "position_moved": _position_moved(base, candidate, changed),
        "e242": _moved(e242_base, e242_candidate),
        "e272": _moved(e272_base, e272_candidate),
        "same_house_number_to_1": to_one,
        "addr_new": [[*key, zone(key)] for key in sorted(addr_new)],
        "cap_displaced": displaced,
        "merge_gained": list(lists["merge_gained"]),
        "merge_lost": list(lists["merge_lost"]),
        "two_doors_one_building": [[*key, *pair, zone(key)] for key, pair in sorted(doors.items())],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodedup.shadow", description=__doc__.splitlines()[0])
    parser.add_argument("shadow_dir", help="the directory `what_if` wrote movers.jsonl into")
    parser.add_argument("--block-group", type=parse_group, default=(1, 1), metavar="I/N",
                        help="export and judge the i-th of n contiguous block groups")
    parser.add_argument("--max-ads", type=int, default=MAX_ADS, help="refuse a larger group")
    parser.add_argument("--max-blocks", type=int, default=MAX_BLOCKS, help="refuse more blocks")
    parser.add_argument("--all-blocks", action="store_true",
                        help="keep the blocks no ruling is inside (they add only D83 lines)")
    args = parser.parse_args(argv)
    from scraper import db

    shadow_dir = Path(args.shadow_dir)
    movers = _jsonl(shadow_dir / MOVERS_FILE)
    conn = db.connect_read_only()
    try:
        blocks = blocks_for(movers, lambda ids: tied_ads(conn, ids))
        if not blocks:
            raise SystemExit(f"no exportable block in {shadow_dir / MOVERS_FILE}")
        every = block_sizes(conn, blocks)
        ruled = None if args.all_blocks else ruled_blocks(conn)
    finally:
        conn.close()
    parsed = dict(zip(sorted(every), parse_blocks(" ".join(sorted(every)))))
    for text, block in parsed.items():
        print(f"[shadow] {block.key if block.ids else text} {every[text]} ads")
    kept, blind = (set(every), set()) if ruled is None else judgeable(movers, ruled)
    kept |= {text for text, block in parsed.items() if block.ids}
    skipped = sorted(set(every) - kept)
    no_ruling = {"blocks": len(skipped), "ads": sum(every[block] for block in skipped)}
    print(f"[shadow] {no_ruling['blocks']} blocks skipped: no ruling inside ({no_ruling['ads']} "
          f"ads); {len(blind)} movers unjudgeable (no ruling on either side)", flush=True)
    sizes = {block: every[block] for block in every if block in kept}
    index, count = args.block_group
    chunk = chunks(sizes, count)[index - 1]
    ads = sum(sizes[block] for block in chunk)
    needed = groups_needed(sizes, args.max_blocks, args.max_ads) if sizes else 0
    print(f"[shadow] {len(sizes)} blocks kept, {sum(sizes.values())} ads; group {index}/{count}: "
          f"{len(chunk)} blocks, {ads} ads; groups needed under the caps: "
          f"{needed if needed is not None else 'none'}",
          flush=True)
    record = {"group": f"{index}/{count}", "blocks": chunk, "ads": ads,
              "all_blocks": len(sizes), "all_ads": sum(sizes.values()),
              "blocks_skipped_no_ruling": no_ruling, "unjudgeable": sorted(blind),
              "rulings_filter": ruled is not None}
    if not chunk:
        # An empty list would export the trial cohort (`cohort.parse_blocks("")`): never.
        (shadow_dir / GROUP_FILE).write_text(json.dumps(record, indent=1, sort_keys=True),
                                             encoding="utf-8")
        print(f"[shadow] group {index}/{count} holds no block a ruling is inside: nothing in it "
              "can fail, nothing exported", flush=True)
        return 0
    if len(chunk) > args.max_blocks or ads > args.max_ads:
        advice = (f"dispatch --block-group i/{needed} for i in 1..{needed}" if needed
                  else "narrow the ids or raise --max-ads")
        print(f"[shadow] REFUSED: group {index}/{count} is over --max-blocks {args.max_blocks} "
              f"or --max-ads {args.max_ads}; {advice}", file=sys.stderr)
        return 2
    summary = export_cohort(db.connect_read_only, shadow_dir, chunk)
    (shadow_dir / GROUP_FILE).write_text(json.dumps(record, indent=1, sort_keys=True),
                                         encoding="utf-8")
    print(json.dumps(summary, indent=1, sort_keys=True))
    print(f"[shadow] base cohort: {shadow_dir / 'base' / ARTIFACT_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
