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
from typing import Any, Callable, Iterable, Iterator, NamedTuple

import psycopg

from autodedup.apply_scope import SCOPE_SETTING, Member, Scope, scope_fields
from autodedup.apply_sql import SETTING_SQL
from autodedup.blocking import SPLIT_CITY_OBEC_KODS
from autodedup.cohort import parse_blocks
from autodedup.evaluate import DIFFERENT, RULINGS_FILES, SAME, read_rulings
from autodedup.export import ARTIFACT_NAME, build_location, fetch_block_ids, run_export
from autodedup.incremental import GENERATION
from autodedup.incremental_sql import RT_CALIBRATION_SCORER_SQL
from autodedup.labels import PairKey, pair_key
from autodedup.labels_lane import run_labels

POSITIONS_FILE: str = "positions.jsonl"
MOVERS_FILE: str = "movers.jsonl"
WHAT_IF_FILE: str = "what_if.json"
SCORER_FILE: str = "scorer.json"
SHADOW_FILE: str = "shadow.json"
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


def blocks_for(movers: Iterable[dict[str, Any]]) -> tuple[list[str], int]:
    """Each mover's old and new block at E300's grain, and the split-city sides with no quarter."""
    keys: dict[str, None] = {}
    left_out = 0
    for mover in movers:
        for side in (mover.get("old"), mover.get("new")):
            if not side or side.get("obec_kod") is None:
                continue
            if int(side["obec_kod"]) not in SPLIT_CITY_OBEC_KODS:
                keys[f"town:{int(side['obec_kod'])}"] = None
            elif side.get("cast_obce_kod") is not None:
                keys[f"quarter:{int(side['cast_obce_kod'])}"] = None
            else:
                left_out += 1  # its candidates are the whole city, which is never exported
    return list(keys), left_out


def block_sizes(conn: psycopg.Connection, blocks: list[str]) -> dict[str, int]:
    """Each block's ad count, selected exactly as the export will select it."""
    return {f"{block.grain}:{block.code}": len(fetch_block_ids(
                conn, block, timeout_ms=_BLOCK_TIMEOUT_MS, negctl_max=1)[0])
            for block in parse_blocks(" ".join(blocks))}


def export_cohort(conn_factory: Callable[[], Any], shadow_dir: Path, blocks: list[str]
                  ) -> dict[str, Any]:
    """Step 2's export: the base cohort over `blocks`, the rulings and the live scorer."""
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


def judge(report: dict[str, Any], done: Override, *, together: set[PairKey],
          recent: set[PairKey], what_if: dict[str, Any]) -> dict[str, Any]:
    """PASS = E111 clean and no ruled pair moved against its ruling; D83 and recent pairs listed."""
    lists = report["d83"]["lists"]
    broken = ([row for row in lists["copairs_lost"] if row[2] == SAME]
              + [row for row in lists["copairs_gained"] if row[2] == DIFFERENT])
    honoured = ([row for row in lists["copairs_gained"] if row[2] == SAME]
                + [row for row in lists["copairs_lost"] if row[2] == DIFFERENT])
    revoked = bool(report["revocation"]["revoked"])
    judged = {key for key in recent if key[0] in done.cohort and key[1] in done.cohort}
    split = judged & {(int(row[0]), int(row[1])) for row in broken}
    held = judged & together
    unjudged_movers = len(done.listed - done.cohort)
    return {
        "pass": not revoked and not broken,
        "partial": bool(what_if.get("limit")) or unjudged_movers > 0,
        "run": what_if,
        "revoked": revoked,
        "broken_rulings": broken,
        "honoured_rulings": honoured,
        "movers_listed": len(done.listed),
        "movers_in_cohort": len(done.changed),
        "movers_unjudged": unjudged_movers,
        "recent_same": {"pairs": len(recent), "judged": len(judged),
                        "held": len(held), "broken": len(split),
                        "apart_in_both": len(judged - held - split),
                        "unjudged": len(recent - judged)},
        "d83_touching_movers": {name: [row for row in lists[name]
                                       if row[0] in done.changed or row[1] in done.changed]
                                for name in D83_EDGES},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodedup.shadow", description=__doc__.splitlines()[0])
    parser.add_argument("shadow_dir", help="the directory `what_if` wrote movers.jsonl into")
    parser.add_argument("--max-ads", type=int, default=MAX_ADS, help="refuse a larger cohort")
    parser.add_argument("--max-blocks", type=int, default=MAX_BLOCKS, help="refuse more blocks")
    args = parser.parse_args(argv)
    from scraper import db

    shadow_dir = Path(args.shadow_dir)
    blocks, left_out = blocks_for(_jsonl(shadow_dir / MOVERS_FILE))
    if not blocks:
        raise SystemExit(f"no exportable block in {shadow_dir / MOVERS_FILE}")
    conn = db.connect_read_only()
    try:
        sizes = block_sizes(conn, blocks)
    finally:
        conn.close()
    for block, ads in sizes.items():
        print(f"[shadow] {block} {ads} ads")
    total = sum(sizes.values())
    print(f"[shadow] {len(sizes)} blocks, {total} ads; {left_out} split-city sides without a "
          "quarter left out", flush=True)
    if len(sizes) > args.max_blocks or total > args.max_ads:
        print(f"[shadow] REFUSED: over --max-blocks {args.max_blocks} or --max-ads "
              f"{args.max_ads}; narrow the ids (--scope apply, --limit) or raise a cap",
              file=sys.stderr)
        return 2
    summary = export_cohort(db.connect_read_only, shadow_dir, blocks)
    print(json.dumps({**summary, "left_out": left_out}, indent=1, sort_keys=True))
    print(f"[shadow] base cohort: {shadow_dir / 'base' / ARTIFACT_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
