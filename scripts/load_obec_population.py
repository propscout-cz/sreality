"""Load ČSÚ municipality population into obec_population (all obce, by RÚIAN code and year).

Reads the committed ČSÚ DataStat export (data/csu_population.json, OBY02AT02 —
download from https://data.csu.gov.cz/datastat/data/VYBER/OBY02AT02) and upserts
each obec's population by its code (the ČSÚ municipality code IS the RÚIAN obec
code) and year, so every year loaded is kept (ruling Q11, migration 725). The
proximity filters, the Datasets picker and the curated cities read the latest year.

Idempotent: a re-run with an unchanged file writes nothing (the `is distinct
from` guard). One transaction, so a partial year never appears. Requires
SUPABASE_DB_URL. Run via .github/workflows/load_obec_population.yml or locally.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.csu_population import DATASTAT_URL, load_population_by_code  # noqa: E402

JSON_PATH = ROOT / "data" / "csu_population.json"
LOG = logging.getLogger("load_obec_population")

_UPSERT_SQL = """
insert into obec_population (obec_kod, year, population)
select * from unnest(%(codes)s::bigint[], %(years)s::int[], %(populations)s::int[])
on conflict (obec_kod, year) do update
   set population = excluded.population, loaded_at = now()
 where obec_population.population is distinct from excluded.population
"""


def upsert_population(conn: Any, rows: list[tuple[int, int, int]]) -> int:
    """(code, population, year) rows in one transaction; returns the rows written (new or changed)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_UPSERT_SQL, {"codes": [r[0] for r in rows], "populations": [r[1] for r in rows],
                                  "years": [r[2] for r in rows]})
        return cur.rowcount or 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="load_obec_population",
        description=f"Load obec population into obec_population (source: {DATASTAT_URL})",
    )
    p.add_argument("--json", default=str(JSON_PATH))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    by_code = load_population_by_code(Path(args.json))
    rows = [(cid, pop, yr) for cid, (pop, yr) in by_code.items()]
    LOG.info("Parsed %d obce from %s", len(rows), args.json)
    if args.dry_run:
        LOG.info("Dry-run: would upsert %d obec population rows", len(rows))
        return 0

    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        LOG.error("SUPABASE_DB_URL is not set")
        return 2

    with psycopg.connect(dsn, prepare_threshold=None) as conn:
        written = upsert_population(conn, rows)
    LOG.info("Upserted population for %d obce (new or changed rows)", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
