"""Load RÚIAN buildings and each address point's building code, one obec file at a time.

Reads the per-obec state files of the registry version the address mirror currently serves,
so a building and its address points are always of one vintage. Resumable: an obec recorded in
`ruian_building_loads` for that version is not read again.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import logging
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
import requests

from location_data import loader_db, ruian_vfr
from scraper import contact

LOG = logging.getLogger("location_data.ruian_buildings")

_PAUSE_S = 0.2
_TIMEOUT = (30, 600)
_TYP_SO = {1: "č.p.", 2: "č.ev."}

_STAGE_BUILDINGS = """
create temp table stage_buildings (
  code bigint primary key, valid_from date, typ_so text, usage_code smallint, iskn_building_id bigint,
  identifying_parcel_id bigint, cast_obce_kod bigint, house_numbers integer[], completed_on date,
  construction_code smallint, floors smallint, dwelling_count integer, built_up_area_m2 integer,
  floor_area_m2 integer, enclosed_volume_m3 integer, lift_code smallint, water_code smallint,
  sewer_code smallint, gas_code smallint, heating_code smallint, protection_codes smallint[]
) on commit drop
"""
_STAGE_LINKS = "create temp table stage_links (kod_adm bigint primary key, building_code bigint) on commit drop"

_FACTS = (
    "typ_so", "usage_code", "iskn_building_id", "identifying_parcel_id", "house_numbers", "completed_on",
    "construction_code", "floors", "dwelling_count", "built_up_area_m2", "floor_area_m2", "enclosed_volume_m3",
    "lift_code", "water_code", "sewer_code", "gas_code", "heating_code", "protection_codes",
)


@dataclass(frozen=True)
class Version:
    id: int
    source_date: datetime.date


def current_version(conn: psycopg.Connection) -> Version:
    with conn.cursor() as cur:
        cur.execute("select id, source_date from registry_versions where is_current")
        row = cur.fetchone()
    if row is None:
        raise loader_db.LoadAborted("no current registry version: the address mirror must load first")
    return Version(int(row[0]), row[1])


def pending_obce(conn: psycopg.Connection, version: Version) -> list[tuple[int, int]]:
    """(unit id, code) of every current obec not yet read for this version."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select u.id, u.code
              from ruian_admin_units u
             where u.level = 'obec' and u.valid_to is null
               and not exists (select 1 from ruian_building_loads l
                                where l.version_id = %s and l.obec_kod = u.code)
             order by u.code
            """,
            (version.id,),
        )
        return [(int(i), int(c)) for i, c in cur.fetchall()]


def write_obec(conn: psycopg.Connection, version: Version, obec_unit_id: int, obec_kod: int,
               rows: list[ruian_vfr.BuildingRow | ruian_vfr.AddressLink], sha256: str) -> tuple[int, int]:
    """One obec, one transaction: its buildings, its address links, its receipt."""
    buildings = [r for r in rows if isinstance(r, ruian_vfr.BuildingRow)]
    links = [r for r in rows if isinstance(r, ruian_vfr.AddressLink)]
    facts = ", ".join(_FACTS)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("drop table if exists stage_buildings, stage_links")
        cur.execute(_STAGE_BUILDINGS)
        cur.execute(_STAGE_LINKS)
        with cur.copy("copy stage_buildings from stdin") as copy:
            for b in buildings:
                copy.write_row((
                    b.code, b.plati_od, _TYP_SO.get(b.typ_so), b.usage_code, b.iskn_building_id,
                    b.identifying_parcel_id, b.cast_obce_kod, list(b.house_numbers) or None, b.completed_on,
                    b.construction_code, b.floors, b.dwelling_count, b.built_up_area_m2, b.floor_area_m2,
                    b.enclosed_volume_m3, b.lift_code, b.water_code, b.sewer_code, b.gas_code, b.heating_code,
                    list(b.protection_codes) or None,
                ))
        with cur.copy("copy stage_links from stdin") as copy:
            for link in links:
                copy.write_row((link.kod_adm, link.building_code))

        # A building the register changed gets a new row; one that left the obec's file is closed.
        cur.execute(
            """
            update ruian_building_objects t
               set valid_to = coalesce(s.valid_from, %(source_date)s), last_version_id = %(v)s
              from (select b.id, s.valid_from
                      from ruian_building_objects b
                 left join stage_buildings s on s.code = b.code
                     where b.valid_to is null and b.obec_unit_id = %(obec)s
                       and (s.code is null or s.valid_from <> b.valid_from)) s
             where t.id = s.id
            """,
            {"v": version.id, "obec": obec_unit_id, "source_date": version.source_date},
        )
        cur.execute(
            f"""
            insert into ruian_building_objects
                   (code, obec_unit_id, cast_obce_unit_id, valid_from, valid_to,
                    first_version_id, last_version_id, {facts})
            select s.code, %(obec)s, c.id, s.valid_from, null, %(v)s, %(v)s, {", ".join(f"s.{f}" for f in _FACTS)}
              from stage_buildings s
         left join ruian_admin_units c
                on c.level = 'cast_obce' and c.code = s.cast_obce_kod and c.valid_to is null
            on conflict (code, valid_from) do update set
                   valid_to = null, last_version_id = excluded.last_version_id,
                   obec_unit_id = excluded.obec_unit_id, cast_obce_unit_id = excluded.cast_obce_unit_id,
                   {", ".join(f"{f} = excluded.{f}" for f in _FACTS)}
            """,
            {"v": version.id, "obec": obec_unit_id},
        )
        cur.execute(
            """
            update ruian_address_points a
               set stavebni_objekt_code = s.building_code
              from (select a.kod_adm, l.building_code
                      from ruian_address_points a
                 left join stage_links l on l.kod_adm = a.kod_adm
                     where a.obec_kod = %(obec_kod)s
                       and a.stavebni_objekt_code is distinct from l.building_code) s
             where a.kod_adm = s.kod_adm
            """,
            {"obec_kod": obec_kod},
        )
        cur.execute(
            "insert into ruian_building_loads (version_id, obec_kod, buildings, links, sha256) "
            "values (%s, %s, %s, %s, %s)",
            (version.id, obec_kod, len(buildings), len(links), sha256),
        )
    return len(buildings), len(links)


def download(sess: requests.Session, url: str, dest: Path) -> str:
    """Stream one file to disk; its sha256. (Not `ruian_csv.download`: that module needs pyproj, the runner has none.)"""
    digest = hashlib.sha256()
    with sess.get(url, timeout=_TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                digest.update(chunk)
                fh.write(chunk)
    return digest.hexdigest()


def load_obec(conn: psycopg.Connection, sess: requests.Session, version: Version, obec_unit_id: int,
              obec_kod: int, work_dir: Path) -> tuple[int, int]:
    stamp = version.source_date.strftime("%Y%m%d")
    path = work_dir / f"{stamp}_OB_{obec_kod}_UZSZ.xml.zip"
    try:
        sha256 = download(sess, ruian_vfr.OBEC_STATE_URL.format(vintage=stamp, obec=obec_kod), path)
        rows = list(ruian_vfr.iter_obec_state(path))
    finally:
        path.unlink(missing_ok=True)
    return write_obec(conn, version, obec_unit_id, obec_kod, rows, sha256)


def run(*, max_seconds: int | None, only: list[int] | None, work_dir: Path) -> int:
    started = time.monotonic()
    conn = loader_db.open_loader_connection()
    try:
        version = current_version(conn)
        todo = [(i, c) for i, c in pending_obce(conn, version) if only is None or c in only]
        LOG.info("BUILDINGS start version=%d vintage=%s pending=%d", version.id, version.source_date, len(todo))
        sess = requests.Session()
        sess.headers["User-Agent"] = contact.USER_AGENT
        done = buildings = links = 0
        for obec_unit_id, obec_kod in todo:
            if max_seconds is not None and time.monotonic() - started > max_seconds:
                break
            b, n = load_obec(conn, sess, version, obec_unit_id, obec_kod, work_dir)
            done, buildings, links = done + 1, buildings + b, links + n
            if done % 250 == 0:
                LOG.info("BUILDINGS progress obce=%d/%d buildings=%d links=%d", done, len(todo), buildings, links)
            time.sleep(_PAUSE_S)
        LOG.info("BUILDINGS done obce=%d remaining=%d buildings=%d links=%d seconds=%d",
                 done, len(todo) - done, buildings, links, int(time.monotonic() - started))
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=int, default=None, help="stop starting new obce after this long")
    parser.add_argument("--obec", type=int, action="append", help="only this obec code (repeatable)")
    parser.add_argument("--work-dir", default=None, help="where each zip is downloaded before it is read")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with tempfile.TemporaryDirectory() as tmp:
        return run(max_seconds=args.max_seconds, only=args.obec, work_dir=Path(args.work_dir or tmp))


if __name__ == "__main__":
    sys.exit(main())
