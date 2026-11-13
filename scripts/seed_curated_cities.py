"""Seed the city-quality indexes: the index definitions and one revision of values for the curated cities.

Run from GitHub Actions (.github/workflows/seed_curated_cities.yml) or a local checkout with `SUPABASE_DB_URL` set.

Reads data/obce_v_datech_2025.csv (the operator-supplied input) and writes, in one transaction, the index definitions
(upserted by index_name) and a fresh city_index_revisions row with its city_index_values. Each row names a city
already in curated_cities, by name and kraj; its population is its town's (obec_population by obec_kod), read through
curated_cities_public. A row naming no such city, or an unknown CSV column, refuses the run by name and writes
nothing: a new city arrives as a one-line data migration checked against the register, never by name (Hodonín is two
obce in one kraj).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "obce_v_datech_2025.csv"

LOG = logging.getLogger("seed_curated_cities")


# --- index column → slug / label / category mapping ----------------------
# Order matters: it drives sort_order in city_index_definitions, which the
# UI uses to render the dropdown + the /cities table column order.

INDEX_METADATA: list[dict[str, str]] = [
    # Overall + category aggregates.
    {"header": "Celkové hodnocení - calculated", "slug": "celkove_hodnoceni",
     "label_en": "Overall rating", "category": "overall"},
    {"header": "Zdraví a životní prostředí", "slug": "zdravi_zivotni_prostredi",
     "label_en": "Health and environment", "category": "health_env"},
    {"header": "Materiální zabezpečení a vzdělání", "slug": "material_vzdelani",
     "label_en": "Material conditions and education", "category": "material_edu"},
    {"header": "Vztahy a služby", "slug": "vztahy_sluzby",
     "label_en": "Relations and services", "category": "services_relations"},

    # Health and environment sub-indexes.
    {"header": "Index praktických lékařů", "slug": "prakticti_lekari",
     "label_en": "GPs availability", "category": "sub_index"},
    {"header": "Index dětských lékařů", "slug": "detsti_lekari",
     "label_en": "Pediatricians availability", "category": "sub_index"},
    {"header": "Index dojezdu do nemocnice", "slug": "dojezd_nemocnice",
     "label_en": "Hospital access time", "category": "sub_index"},
    {"header": "Index lékáren", "slug": "lekarny",
     "label_en": "Pharmacies availability", "category": "sub_index"},
    {"header": "Index průměrné délky života", "slug": "prumerna_delka_zivota",
     "label_en": "Life expectancy", "category": "sub_index"},
    {"header": "Index znečištění ovzduší", "slug": "znecisteni_ovzdusi",
     "label_en": "Air quality", "category": "sub_index"},
    {"header": "Index znečišťovatelů", "slug": "znecistovatele",
     "label_en": "Polluters proximity", "category": "sub_index"},
    {"header": "Index chráněných území", "slug": "chranena_uzemi",
     "label_en": "Protected areas", "category": "sub_index"},

    # Material conditions sub-indexes.
    {"header": "Index nezaměstnanosti", "slug": "nezamestnanost",
     "label_en": "Employment rate", "category": "sub_index"},
    {"header": "Index nabídky pracovních míst", "slug": "pracovni_mista",
     "label_en": "Job offers", "category": "sub_index"},
    {"header": "Index finanční dostupnosti bydlení", "slug": "dostupnost_bydleni",
     "label_en": "Housing affordability", "category": "sub_index"},
    {"header": "Index hmotné nouze", "slug": "hmotna_nouze",
     "label_en": "Material need recipients", "category": "sub_index"},
    {"header": "Index exekucí", "slug": "exekuce",
     "label_en": "Foreclosure rate", "category": "sub_index"},
    {"header": "Index kapacity mateřských škol", "slug": "kapacita_ms",
     "label_en": "Kindergarten capacity", "category": "sub_index"},
    {"header": "Index kapacity základních škol", "slug": "kapacita_zs",
     "label_en": "Primary school capacity", "category": "sub_index"},
    {"header": "Index kvalitních středních škol", "slug": "kvalitni_ss",
     "label_en": "Quality secondary schools", "category": "sub_index"},

    # Services and relations sub-indexes.
    {"header": "Index marketů", "slug": "markety",
     "label_en": "Grocery stores", "category": "sub_index"},
    {"header": "Index bankomatů", "slug": "bankomaty",
     "label_en": "ATM availability", "category": "sub_index"},
    {"header": "Index restaurací", "slug": "restaurace",
     "label_en": "Restaurants", "category": "sub_index"},
    {"header": "Index kin", "slug": "kina",
     "label_en": "Cinemas", "category": "sub_index"},
    {"header": "Index digitalizace", "slug": "digitalizace",
     "label_en": "Digital services", "category": "sub_index"},
    {"header": "Index silniční sítě", "slug": "silnicni_sit",
     "label_en": "Road network", "category": "sub_index"},
    {"header": "Index železniční dopravy", "slug": "zeleznicni_doprava",
     "label_en": "Rail transport", "category": "sub_index"},
    {"header": "Index sounáležitosti", "slug": "sounalezitost",
     "label_en": "Community belonging", "category": "sub_index"},
    {"header": "Index zájmu o obecní a krajské volby", "slug": "zajem_volby",
     "label_en": "Voter turnout", "category": "sub_index"},
    {"header": "Index bezpečnosti", "slug": "bezpecnost",
     "label_en": "Safety", "category": "sub_index"},
    {"header": "Index hazardu", "slug": "hazard",
     "label_en": "Gambling absence", "category": "sub_index"},
    {"header": "Index stěhování mladých", "slug": "stehovani_mladych",
     "label_en": "Young residents retention", "category": "sub_index"},
    {"header": "Index přírůstku obyvatelstva", "slug": "prirustek_obyvatel",
     "label_en": "Population growth", "category": "sub_index"},
]

INDEX_BY_HEADER: dict[str, dict[str, str]] = {
    m["header"]: m for m in INDEX_METADATA
}


class SeedRefused(Exception):
    """The input names something the seed must not guess at; the message names it, and nothing is written."""


@dataclass
class CsvRow:
    name: str
    kraj_name: str
    values: dict[str, float]  # slug → value


def read_csv(path: Path) -> list[CsvRow]:
    rows: list[CsvRow] = []
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        unknown = [h for h in reader.fieldnames or [] if h not in ("Město", "Kraj") and h not in INDEX_BY_HEADER]
        if unknown:
            raise SeedRefused(f"unknown CSV column(s) {unknown}: add each to INDEX_METADATA in this script first")
        for row_idx, row in enumerate(reader, start=2):
            name = (row.get("Město") or "").strip()
            kraj = (row.get("Kraj") or "").strip()
            if not name or not kraj:
                LOG.warning("CSV row %d has empty name or kraj, skipping", row_idx)
                continue
            values: dict[str, float] = {}
            for header, cell in row.items():
                if header in ("Město", "Kraj") or cell is None or cell.strip() == "":
                    continue
                try:
                    values[INDEX_BY_HEADER[header]["slug"]] = float(cell.replace(",", "."))
                except ValueError:
                    LOG.warning(
                        "Bad numeric value %r for %s in row %d",
                        cell, header, row_idx,
                    )
            rows.append(CsvRow(name=name, kraj_name=kraj, values=values))
    LOG.info("Read %d city rows from %s", len(rows), path)
    return rows


def upsert_definitions(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for order, meta in enumerate(INDEX_METADATA):
            cur.execute(
                """
                insert into city_index_definitions
                  (index_name, label_cs, label_en, category, sort_order)
                values (%s, %s, %s, %s, %s)
                on conflict (index_name) do update set
                  label_cs   = excluded.label_cs,
                  label_en   = excluded.label_en,
                  category   = excluded.category,
                  sort_order = excluded.sort_order
                """,
                (meta["slug"], meta["header"], meta["label_en"],
                 meta["category"], order),
            )
    LOG.info("Upserted %d index definitions", len(INDEX_METADATA))


def city_ids(conn: psycopg.Connection, rows: list[CsvRow]) -> dict[tuple[str, str], int]:
    """{(name, kraj): city_id} for every row; a row naming no curated city refuses the run, naming each such row."""
    with conn.cursor() as cur:
        cur.execute("select name, kraj_name, id from curated_cities")
        known = {(name, kraj): city_id for name, kraj, city_id in cur.fetchall()}
    missing = sorted({(r.name, r.kraj_name) for r in rows} - known.keys())
    if missing:
        raise SeedRefused("no curated city named " + "; ".join(f"{name}, {kraj}" for name, kraj in missing)
                          + ": a new city arrives as a data migration checked against the register")
    return {(r.name, r.kraj_name): known[(r.name, r.kraj_name)] for r in rows}


def insert_revision_and_values(
    conn: psycopg.Connection,
    rows: list[CsvRow],
    ids: dict[tuple[str, str], int],
    source_filename: str,
    uploaded_by: str | None,
) -> int:
    raw_rows = [{"name": r.name, "kraj_name": r.kraj_name, "values": r.values}
                for r in rows]
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into city_index_revisions
              (uploaded_by, source_filename, row_count, raw_rows)
            values (%s, %s, %s, %s::jsonb)
            returning source_revision
            """,
            (uploaded_by, source_filename, len(rows), json.dumps(raw_rows)),
        )
        (revision,) = cur.fetchone()

        values_payload = [(ids[(row.name, row.kraj_name)], revision, slug, value)
                          for row in rows for slug, value in row.values.items()]

        with cur.copy(
            "copy city_index_values (city_id, source_revision, index_name, value) "
            "from stdin"
        ) as copy:
            for record in values_payload:
                copy.write_row(record)

    LOG.info("Inserted revision %d with %d values", revision, len(values_payload))
    return revision


def seed(conn: psycopg.Connection, rows: list[CsvRow], *, source_filename: str, uploaded_by: str | None) -> int:
    """Writes the definitions and one revision of `rows`' values in one transaction; returns the revision."""
    with conn.transaction():
        ids = city_ids(conn, rows)
        upsert_definitions(conn)
        return insert_revision_and_values(conn, rows, ids, source_filename=source_filename, uploaded_by=uploaded_by)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="seed_curated_cities")
    p.add_argument("--csv", default=str(CSV_PATH),
                   help="Path to obce_v_datech_*.csv")
    p.add_argument("--source-filename", default=None,
                   help="Override the source_filename written to "
                        "city_index_revisions (defaults to the CSV's basename)")
    p.add_argument("--uploaded-by", default=os.environ.get("GITHUB_ACTOR"),
                   help="Optional uploader name (defaults to $GITHUB_ACTOR)")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    csv_path = Path(args.csv)
    if not csv_path.exists():
        LOG.error("CSV not found at %s", csv_path)
        return 2
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        LOG.error("SUPABASE_DB_URL is not set")
        return 2

    try:
        rows = read_csv(csv_path)
        if not rows:
            LOG.error("CSV produced zero rows")
            return 2
        with psycopg.connect(dsn, prepare_threshold=None) as conn:
            revision = seed(conn, rows, source_filename=args.source_filename or csv_path.name,
                            uploaded_by=args.uploaded_by)
    except SeedRefused as exc:
        LOG.error("Refused, nothing written: %s", exc)
        return 2

    LOG.info("Done: revision %d.", revision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
