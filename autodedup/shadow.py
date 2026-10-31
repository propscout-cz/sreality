"""The wave shadow: a location wave's movers through the engine before the wave deploys.

    python3 -m location_data.resolver.what_if --ids ids.txt --out shadow/w4 --claims remine ...
    python3 -m autodedup.shadow shadow/w4
    python3 -m autodedup.harness shadow shadow/w4/base/cohort.jsonl.gz shadow/w4/positions.jsonl \\
        shadow/w4/labels --scorer shadow/w4/scorer.json --out shadow/w4/judge

The engine reads every position through `COHORT_LOCATION_SQL` and `export.build_location`, so the
seam is the cohort artifact (`DECISION_MODULES` untouched). `main` is step 2, the one production
read: the movers' blocks, the rulings and the live scorer, exported IN-PROCESS on a READ ONLY
connection, so `lane.run` never writes its `autodedup.iterations` row. `override` and `judge` are
step 3's halves (`harness shadow`). Design: `docs/design/location-quality/wave-shadow.md`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from autodedup.blocking import SPLIT_CITY_OBEC_KODS
from autodedup.evaluate import DIFFERENT, SAME
from autodedup.export import ARTIFACT_NAME, build_location, run_export
from autodedup.incremental import GENERATION
from autodedup.incremental_sql import RT_CALIBRATION_SCORER_SQL
from autodedup.labels_lane import run_labels

MOVERS_FILE: str = "movers.jsonl"
SCORER_FILE: str = "scorer.json"
SHADOW_FILE: str = "shadow.json"
D83_EDGES: tuple[str, ...] = ("merge_gained", "merge_lost", "copairs_gained", "copairs_lost")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def blocks_for(movers: Iterable[dict[str, Any]]) -> tuple[list[str], int]:
    """Each mover's old and new block at E300's grain, and how many sides were left out."""
    # The quarter in a split city, else the town. A split-city side with no quarter reaches the
    # whole city, and a whole city is never exported.
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
                left_out += 1
    return list(keys), left_out


def export(conn_factory: Callable[[], Any], shadow_dir: Path) -> dict[str, Any]:
    """Step 2: the base cohort over the movers' blocks, the rulings and the live scorer."""
    blocks, left_out = blocks_for(_jsonl(shadow_dir / MOVERS_FILE))
    if not blocks:
        raise SystemExit(f"no exportable block in {shadow_dir / MOVERS_FILE}")
    print(f"[shadow] {len(blocks)} blocks; {left_out} split-city sides without a quarter left out",
          flush=True)
    cohort = run_export(conn_factory, {"blocks": " ".join(blocks)}, shadow_dir / "base")
    labels = run_labels(conn_factory, {}, shadow_dir / "labels")
    conn = conn_factory()
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(RT_CALIBRATION_SCORER_SQL, {"generation": GENERATION})
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"generation {GENERATION!r} has no calibration row: no live scorer")
    settings = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
    (shadow_dir / SCORER_FILE).write_text(json.dumps(
        {"generation": GENERATION, "settings": settings, "model_version": row[1]},
        indent=1, sort_keys=True), encoding="utf-8")
    return {"blocks": blocks, "left_out": left_out, "cohort": cohort["counts"],
            "labels": labels["counts"]}


def override(base: Path, positions: Path, out: Path) -> set[int]:
    """`base` with the listed blocks rebuilt by the export's builder; returns the ids changed."""
    rows = {int(row["listing_id"]): row for row in _jsonl(positions)}
    changed: set[int] = set()
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(base, "rt", encoding="utf-8") as src, \
            gzip.open(out, "wt", encoding="utf-8", newline="\n") as dst:
        for line in src:
            record = json.loads(line)
            if record.get("t") == "listing" and record["id"] in rows:
                block = build_location(rows[record["id"]])
                if block != record["location"]:
                    changed.add(record["id"])
                    record["location"] = block
                    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
            dst.write(line)
    return changed


def judge(report: dict[str, Any], movers: set[int]) -> dict[str, Any]:
    """PASS = E111 clean and no ruled pair moved against its ruling; D83 edges only listed."""
    # Against its ruling: a `same` pair the base held together split, a `different` pair newly
    # joined. A ruling newly honoured is reported. D83 edges touching a mover are never judged.
    lists = report["d83"]["lists"]
    broken = ([row for row in lists["copairs_lost"] if row[2] == SAME]
              + [row for row in lists["copairs_gained"] if row[2] == DIFFERENT])
    honoured = ([row for row in lists["copairs_gained"] if row[2] == SAME]
                + [row for row in lists["copairs_lost"] if row[2] == DIFFERENT])
    revoked = bool(report["revocation"]["revoked"])
    return {
        "pass": not revoked and not broken,
        "revoked": revoked,
        "broken_rulings": broken,
        "honoured_rulings": honoured,
        "movers_in_cohort": len(movers),
        "d83_touching_movers": {name: [row for row in lists[name]
                                       if row[0] in movers or row[1] in movers]
                                for name in D83_EDGES},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodedup.shadow", description=__doc__.splitlines()[0])
    parser.add_argument("shadow_dir", help="the directory `what_if` wrote movers.jsonl into")
    args = parser.parse_args(argv)
    from scraper import db

    summary = export(db.connect_read_only, Path(args.shadow_dir))
    print(json.dumps(summary, indent=1, sort_keys=True))
    print(f"[shadow] base cohort: {Path(args.shadow_dir) / 'base' / ARTIFACT_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
