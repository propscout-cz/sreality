"""One-off heal: bazos / `web` flats whose floor 0 came from a stray 'přízemí'.

Until the parser fix that ships with this script, `scraper.floor.floor_from_text` ended
with "any 'přízemí' anywhere -> 0", so a flat whose advert says "v šestém podlaží" and
"sklep v přízemí domu" was filed on the ground storey. Measured 2026-10-25
(`bazos_floor_zero.md`): 373 bazos flats, 116 active, carry floor 0 while their text names
an above-ground storey; 95 % of them contain 'přízemí'.

WHY NOT `scripts/reparse.py`. The seam never blanks a stored value: a row whose new parse
reads no storey keeps its wrong 0. This heal selects by the measurement's own storey
regexes (`names_above_ground_storey`, a port of `bazos_floor_zero.sql`) and then writes the
fixed parser's reading when it reads a storey above ground, else NULL. NULL is the safe
default — the dedup floor limb abstains on an unstated floor, and podlaží against patro is
exactly where portals disagree by one.

Standing heal rules: NO `listing_snapshots` row (correcting our own reading of the SAME
stored text; `floor` is hashed, so each healed LIVE row's next detail fetch appends one
genuine snapshot, an inactive row never gets one); never touches `last_seen_at`; writes
compare-and-set on `floor = 0`, so a row a fresh detail write moved is left alone; enqueues
`dirty_properties` in the same statement (rule 20). `--apply` requires `--backup`, and the
JSON backup of every (id, old floor, new floor) is written BEFORE the first write.

Usage:  python -m scripts.heal_bazos_floor_zero                       # dry run
        python -m scripts.heal_bazos_floor_zero --apply --backup heal_floor_zero.json
Required: SUPABASE_DB_URL.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from scraper import db
from scraper.bazos_parser import ad_haystack
from scraper.floor import _fold, floor_from_text
from scripts.backfill_support import _STATEMENT_TIMEOUT_SQL, execute_with_lock_retry

LOG = logging.getLogger("heal_bazos_floor_zero")

SOURCES: tuple[str, ...] = ("bazos", "web")
_BATCH = 500

# The measurement's three readings, first match wins (A, then C, then B), plus the
# nominative add-on on ads none of them read. patro/poschodí count from ground 0,
# podlaží/NP from ground 1; "above ground" = patro N >= 1 or podlaží/NP N >= 2.
_ORD = r"(?:prvn|druh|tret|ctvrt|pat|sest|sedm|osm|devat|desat|jedenact|dvanact)"
_NOUN = r"(patr(?:o|a|e|u|em)\b|podlazi|np\b|poschodi)"
_PREP_RE = re.compile(
    rf"\b(?:v|ve|na)\s+(\d{{1,2}}\.\s*|\d{{1,2}}\s+|{_ORD}(?:i|y|e|em|im|eho|iho|ym)\s+)(?:\w+\s+)?{_NOUN}")
_DIGIT_RE = re.compile(rf"(\d{{1,2}})\.\s*{_NOUN}")
_LABEL_RE = re.compile(r"patro\s*:?\s*(\d{1,2})\b")
_NOMINATIVE_RE = re.compile(
    rf"\b((?:prvn|druh|tret|ctvrt|pat|sest|sedm|osm|devat|desat)(?:i|e|y|em|im|eho|ym))\s+{_NOUN}")
_ORD_VALUE = {"prv": 1, "dru": 2, "tre": 3, "ctv": 4, "pat": 5, "ses": 6, "sed": 7,
              "osm": 8, "dev": 9, "des": 10, "jed": 11, "dva": 12}


def _number(token: str) -> int:
    digits = re.search(r"\d+", token)
    return int(digits.group(0)) if digits else _ORD_VALUE[token.strip()[:3]]


def _above(n: int, noun: str) -> bool:
    ground0 = noun.startswith(("patr", "poschod"))
    return n >= 1 if ground0 else n >= 2


def names_above_ground_storey(description: str | None) -> bool:
    """The measurement's instrument: the text names a storey above the ground one."""
    if not description:
        return False
    text = _fold(description)
    m = _PREP_RE.search(text) or _DIGIT_RE.search(text)
    if m:
        return _above(_number(m.group(1)), m.group(2))
    m = _LABEL_RE.search(text)
    if m:
        return int(m.group(1)) >= 1
    m = _NOMINATIVE_RE.search(text)
    return bool(m) and _above(_number(m.group(1)), m.group(2))


@dataclass(frozen=True)
class Change:
    id: int
    source: str
    is_active: bool
    old_floor: int
    new_floor: int | None


def decide(title: str | None, description: str | None) -> tuple[bool, int | None]:
    """(selected, floor to write) for one stored floor-0 flat."""
    if not names_above_ground_storey(description):
        return False, None
    reread, _total = floor_from_text(ad_haystack(title, description), category_main="byt")
    return True, reread if reread is not None and reread > 0 else None


def plan(rows: Iterable[tuple[int, str, bool, str | None, str | None]]) -> list[Change]:
    """The changes for (id, source, is_active, title, description) rows read at floor 0."""
    out: list[Change] = []
    for listing_id, source, is_active, title, description in rows:
        selected, new_floor = decide(title, description)
        if selected:
            out.append(Change(int(listing_id), source, bool(is_active), 0, new_floor))
    return out


_IDS_SQL = """
    SELECT l.id FROM listings l
    WHERE l.source = ANY(%(sources)s::text[]) AND l.category_main = 'byt' AND l.floor = 0
    ORDER BY l.id
"""

_ROWS_SQL = """
    SELECT l.id, l.source, l.is_active, l.raw_json->>'title', l.description
    FROM listings l
    WHERE l.id = ANY(%(ids)s::bigint[]) AND l.floor = 0
    ORDER BY l.id
"""

# Compare-and-set on the 0 this pass read; the dirty mark rides the same statement.
_HEAL_SQL = """
    WITH fresh AS (
        SELECT * FROM unnest(%(ids)s::bigint[], %(floors)s::integer[]) AS t(id, floor)
    ), healed AS (
        UPDATE listings AS l SET floor = f.floor
        FROM fresh AS f
        WHERE l.id = f.id AND l.floor = 0
        RETURNING l.property_id
    )
    INSERT INTO dirty_properties (property_id)
    SELECT DISTINCT property_id FROM healed WHERE property_id IS NOT NULL
    ON CONFLICT (property_id) DO UPDATE SET marked_at = now()
"""

_BATCH_GUARDS: tuple[str, ...] = (
    "SET LOCAL statement_timeout = '120s'",
    "SET LOCAL lock_timeout = '30s'",
)


def _chunks(ids: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def _report(changes: list[Change], examined: int, *, dry_run: bool) -> None:
    by = Counter((c.source, "active" if c.is_active else "inactive",
                  "null" if c.new_floor is None else "reread") for c in changes)
    LOG.info("HEAL examined=%d selected=%d (active %d) to_null=%d to_reread=%d dry_run=%s",
             examined, len(changes), sum(c.is_active for c in changes),
             sum(c.new_floor is None for c in changes),
             sum(c.new_floor is not None for c in changes), dry_run)
    for key, n in sorted(by.items()):
        LOG.info("HEAL   %-6s %-8s %-6s %d", *key, n)
    for c in changes[:5]:
        LOG.info("HEAL   e.g. id=%d floor 0 -> %s", c.id, c.new_floor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write. Without it, report only.")
    parser.add_argument("--backup", type=Path, default=None,
                        help="JSON file for (id, old_floor, new_floor); required with --apply.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.apply and args.backup is None:
        print("ERROR: --apply needs --backup <path>.", file=sys.stderr)
        return 2
    if not os.environ.get("SUPABASE_DB_URL"):
        print("ERROR: SUPABASE_DB_URL is not set.", file=sys.stderr)
        return 2

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_STATEMENT_TIMEOUT_SQL)
            cur.execute(_IDS_SQL, {"sources": list(SOURCES)})
            ids = [int(r[0]) for r in cur.fetchall()]
        changes: list[Change] = []
        for chunk in _chunks(ids, _BATCH):
            with conn.cursor() as cur:
                cur.execute(_ROWS_SQL, {"ids": chunk})
                changes.extend(plan(cur.fetchall()))
        _report(changes, len(ids), dry_run=not args.apply)
        if not args.apply or not changes:
            return 0

        args.backup.write_text(json.dumps([asdict(c) for c in changes], indent=1),
                               encoding="utf-8")
        LOG.info("HEAL backup of %d rows written to %s", len(changes), args.backup)
        enqueued = 0
        for i in range(0, len(changes), _BATCH):
            batch = changes[i:i + _BATCH]
            params: dict[str, Any] = {"ids": [c.id for c in batch],
                                      "floors": [c.new_floor for c in batch]}
            enqueued += execute_with_lock_retry(conn, _HEAL_SQL, params,
                                                label="HEAL floor", setup=_BATCH_GUARDS)
        LOG.info("HEAL done healed<=%d dirty_properties enqueued=%d", len(changes), enqueued)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
