"""Load RÚIAN buildings and each address point's building code, one obec file at a time.

Reads the per-obec state files of the registry version the address mirror currently serves,
so a building and its address points are always of one vintage. Resumable: an obec with a clean
receipt in `ruian_building_loads` for that version is not read again. One obec failing never
stops the others: its receipt carries the error and the next run tries it again.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import logging
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg
import requests

from location_data import loader_db, ruian_vfr
from scraper import contact

LOG = logging.getLogger("location_data.ruian_buildings")

_PAUSE_S = 0.2
_TIMEOUT = (30, 600)
_RETRY_WAITS_S = (2, 10, 30)
# A file that would close more than this share of an obec's current buildings, or clear more than this share of
# its doors' building links (once it holds at least _SHRINK_MIN of them), is refused before any write: a truncated
# or wrong-but-valid file must not empty an obec.
_SHRINK_MAX_SHARE = 0.5
_SHRINK_MIN = 20
_OBEC_STATEMENT_TIMEOUT_S = 900  # Praha, the largest obec, is 152,517 buildings in one transaction
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


def registry_load_in_flight(conn: psycopg.Connection, version: Version) -> int | None:
    """The id of a registry version newer than the served one that has not published, else None.

    The registry load (the 1st of a month, a GitHub workflow the runner's `location-heavy` cannot exclude) merges
    into `ruian_address_points` in place for hours before it publishes, and this load writes the same rows. Its
    merge also re-stamps every obec's `last_version_id` in one statement, after which nothing is pending for the
    served version anyway; this check covers the hours before that. A newer row that never published counts for
    two days: an aborted load is marked, retried or superseded well inside that."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select v.id from registry_versions v
             where v.id > %s and not v.is_current and v.loaded_at > now() - interval '2 days'
               and not exists (select 1 from registry_load_discrepancies d
                                where d.registry_version_id = v.id and d.discrepancy = 'load_aborted')
             order by v.id desc limit 1
            """,
            (version.id,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


@dataclass(frozen=True)
class Clock:
    """When a run may START an obec, in UTC. `window`: (first, last) minute of the day, the run does nothing outside
    it, so a run restarted by a deploy keeps the hours its schedule was given (its seconds budget starts afresh, the
    window's end does not move).
    `quiet`: minutes of each hour to wait out (the Browse rebuild)."""

    window: tuple[int, int] | None = None
    quiet: tuple[int, int] | None = None

    def open(self, now: datetime.datetime) -> bool:
        if self.window is None:
            return True
        minute = now.hour * 60 + now.minute
        return self.window[0] <= minute < self.window[1]

    def quiet_for(self, now: datetime.datetime) -> float:
        """Seconds until the quiet minutes pass; 0 outside them."""
        if self.quiet is None or not self.quiet[0] <= now.minute < self.quiet[1]:
            return 0.0
        return (self.quiet[1] - now.minute) * 60 - now.second


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def pending_obce(conn: psycopg.Connection, version: Version) -> list[tuple[int, int]]:
    """(unit id, code) of every obec the served version holds and this load has no clean receipt for.

    `last_version_id = version` and not only `valid_to is null`: the registry load never closes a unit missing
    from a new vintage, and an abolished obec has no file.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select u.id, u.code
              from ruian_admin_units u
             where u.level = 'obec' and u.valid_to is null and u.last_version_id = %(v)s
               and not exists (select 1 from ruian_building_loads l
                                where l.version_id = %(v)s and l.obec_kod = u.code and l.error is null)
             order by u.code
            """,
            {"v": version.id},
        )
        return [(int(i), int(c)) for i, c in cur.fetchall()]


def _step(cur: psycopg.Cursor, name: str, sql: str, params: dict | None, level: int) -> None:
    began = time.monotonic()
    cur.execute(sql, params)
    LOG.log(level, "BUILDINGS statement=%s rows=%d seconds=%.2f", name, cur.rowcount, time.monotonic() - began)


def _refuse_shrink(what: str, in_file: int, held: int, gone: int) -> None:
    if held >= _SHRINK_MIN and gone > held * _SHRINK_MAX_SHARE:
        raise ShrinkRefused(f"the file holds {in_file} {what} and would drop {gone} of {held}")


def write_obec(conn: psycopg.Connection, version: Version, obec_unit_id: int, obec_kod: int,
               rows: list[ruian_vfr.BuildingRow | ruian_vfr.AddressLink], sha256: str, *,
               accept_shrink: bool = False, log_level: int = logging.DEBUG) -> tuple[int, int]:
    """One obec, one bounded transaction: its buildings, its address links, its receipt."""
    buildings = [r for r in rows if isinstance(r, ruian_vfr.BuildingRow)]
    links = [r for r in rows if isinstance(r, ruian_vfr.AddressLink)]
    facts = ", ".join(_FACTS)
    params = {"v": version.id, "obec": obec_unit_id, "obec_kod": obec_kod, "source_date": version.source_date}
    with loader_db.bounded(conn, _OBEC_STATEMENT_TIMEOUT_S) as cur:
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
        cur.execute("analyze stage_buildings")
        cur.execute("analyze stage_links")

        if not accept_shrink:
            cur.execute(
                """
                select count(*), count(*) filter (where not exists (select 1 from stage_buildings s where s.code = b.code))
                  from ruian_building_objects b
                 where b.valid_to is null
                   and b.obec_unit_id in (select u.id from ruian_admin_units u
                                           where u.level = 'obec' and u.code = %(obec_kod)s)
                """,
                params,
            )
            _refuse_shrink("buildings", len(buildings), *cur.fetchone())
            cur.execute(
                """
                select count(*), count(*) filter (where not exists (select 1 from stage_links l where l.kod_adm = a.kod_adm))
                  from ruian_address_points a
                 where a.stavebni_objekt_code is not null
                   and a.obec_unit_id in (select u.id from ruian_admin_units u
                                           where u.level = 'obec' and u.code = %(obec_kod)s)
                """,
                params,
            )
            _refuse_shrink("address links", len(links), *cur.fetchone())

        # A building the register changed gets a new row: close its current one first, by CODE, wherever it
        # stands (an obec's unit row is re-created on a rename or a parent's re-version, and a building can
        # move obec), so the one-current-row-per-code index never sees two.
        _step(cur, "close_changed",
            """
            update ruian_building_objects b
               set valid_to = greatest(s.valid_from, b.valid_from), last_version_id = %(v)s
              from stage_buildings s
             where b.code = s.code and b.valid_to is null and b.valid_from <> s.valid_from
            """,
            params, log_level,
        )
        # A building gone from this obec's file is closed. The obec is named by its CODE: every unit row it had.
        _step(cur, "close_gone",
            """
            update ruian_building_objects b
               set valid_to = %(source_date)s, last_version_id = %(v)s
             where b.valid_to is null
               and b.obec_unit_id in (select u.id from ruian_admin_units u
                                       where u.level = 'obec' and u.code = %(obec_kod)s)
               and not exists (select 1 from stage_buildings s where s.code = b.code)
            """,
            params, log_level,
        )
        _step(cur, "upsert_buildings",
            f"""
            insert into ruian_building_objects as t
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
            -- An unchanged building is not written, so last_version_id is the last version that changed it.
            where t.valid_to is not null
               or (t.obec_unit_id, t.cast_obce_unit_id, {", ".join(f"t.{f}" for f in _FACTS)})
                  is distinct from
                  (excluded.obec_unit_id, excluded.cast_obce_unit_id, {", ".join(f"excluded.{f}" for f in _FACTS)})
            """,
            params, log_level,
        )
        # Each door's building, by the door's own key; then the obec's doors the file no longer links.
        _step(cur, "link_doors",
            """
            update ruian_address_points a
               set stavebni_objekt_code = l.building_code
              from stage_links l
             where a.kod_adm = l.kod_adm and a.stavebni_objekt_code is distinct from l.building_code
            """,
            None, log_level,
        )
        _step(cur, "clear_doors",
            """
            update ruian_address_points a
               set stavebni_objekt_code = null
             where a.obec_unit_id in (select u.id from ruian_admin_units u
                                       where u.level = 'obec' and u.code = %(obec_kod)s)
               and a.stavebni_objekt_code is not null
               and not exists (select 1 from stage_links l where l.kod_adm = a.kod_adm)
            """,
            params, log_level,
        )
        cur.execute(_RECEIPT_SQL, (version.id, obec_kod, len(buildings), len(links), sha256, None))
    return len(buildings), len(links)


_RECEIPT_SQL = """
insert into ruian_building_loads (version_id, obec_kod, buildings, links, sha256, error)
values (%s, %s, %s, %s, %s, %s)
on conflict (version_id, obec_kod) do update set
       buildings = excluded.buildings, links = excluded.links, sha256 = excluded.sha256,
       error = excluded.error, loaded_at = now()
"""


class ShrinkRefused(RuntimeError):
    """The file would close most of the obec's buildings; nothing was written. It stays refused on every run
    until someone judges it: a hand run with `--accept-shrink <obec code>` applies it."""


class Unavailable(RuntimeError):
    """The obec's file could not be fetched; the message is what the receipt keeps. `missing` = the server
    answered that there is no such file (never tried again within a run)."""

    def __init__(self, message: str, *, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


def download(sess: requests.Session, url: str, dest: Path) -> str:
    """Stream one file to disk; its sha256. A refused connection, a 429 or a 5xx is tried again, a 404 is not.

    (Not `ruian_csv.download`: that module needs pyproj, which the runner's image has not.)
    """
    for wait in (*_RETRY_WAITS_S, None):
        try:
            digest = hashlib.sha256()
            with sess.get(url, timeout=_TIMEOUT, stream=True) as resp:
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.ConnectionError(f"HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise Unavailable(f"HTTP {resp.status_code}", missing=resp.status_code == 404)
                with dest.open("wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        digest.update(chunk)
                        fh.write(chunk)
            return digest.hexdigest()
        except requests.RequestException as exc:  # a reset mid-body (ChunkedEncodingError) is one of these too
            if wait is None:
                raise Unavailable(type(exc).__name__ + ": " + str(exc)[:120]) from None
            time.sleep(wait)
    raise AssertionError("unreachable")


def load_obec(conn: psycopg.Connection, sess: requests.Session, version: Version, obec_unit_id: int,
              obec_kod: int, work_dir: Path, *, accept_shrink: bool = False,
              log_level: int = logging.DEBUG) -> tuple[int, int]:
    stamp = version.source_date.strftime("%Y%m%d")
    path = work_dir / f"{stamp}_OB_{obec_kod}_UZSZ.xml.zip"
    try:
        sha256 = download(sess, ruian_vfr.OBEC_STATE_URL.format(vintage=stamp, obec=obec_kod), path)
        rows = list(ruian_vfr.iter_obec_state(path))
    finally:
        path.unlink(missing_ok=True)
    return write_obec(conn, version, obec_unit_id, obec_kod, rows, sha256, accept_shrink=accept_shrink,
                      log_level=log_level)


def record_failure(conn: psycopg.Connection, version: Version, obec_kod: int, error: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_RECEIPT_SQL, (version.id, obec_kod, 0, 0, None, error[:300]))


_MISSING_STOP = 20   # this many files in a row answered 404: the vintage is not published, stop asking
_ANALYZE_EVERY = 500  # obce; the doors' building column goes from all-NULL to filled, and plans follow statistics


def run(*, max_seconds: int | None, only: list[int] | None, work_dir: Path,
        accept_shrink: frozenset[int] = frozenset(), clock: Clock = Clock(),
        now: Callable[[], datetime.datetime] = _utc_now, sleep: Callable[[float], None] = time.sleep) -> int:
    if not clock.open(now()):
        LOG.info("BUILDINGS not started: %s UTC is outside the run's hours", now().strftime("%H:%M"))
        return 0
    started = time.monotonic()
    conn = loader_db.open_loader_connection()
    # A hand run on named obce reports every statement; a full run would log ~44,000 lines.
    log_level = logging.INFO if only is not None else logging.DEBUG
    try:
        version = current_version(conn)
        todo = [(i, c) for i, c in pending_obce(conn, version) if only is None or c in only]
        LOG.info("BUILDINGS start version=%d vintage=%s pending=%d", version.id, version.source_date, len(todo))
        sess = requests.Session()
        sess.headers["User-Agent"] = contact.USER_AGENT
        done = failed = refused = buildings = links = missing_run = 0
        unpublished = False
        for obec_unit_id, obec_kod in todo:
            if max_seconds is not None and time.monotonic() - started > max_seconds:
                break
            if (wait := clock.quiet_for(now())) > 0:
                sleep(wait)
            if not clock.open(now()):
                LOG.info("BUILDINGS stopping: %s UTC is the end of the run's hours", now().strftime("%H:%M"))
                break
            # Asked before every obec, the first too: the registry load is a GitHub workflow that can start at any
            # minute of this run.
            if only is None and (merging := registry_load_in_flight(conn, version)) is not None:
                LOG.warning("BUILDINGS stopping: registry version %d is being merged over served version %d",
                            merging, version.id)
                break
            began = time.monotonic()
            try:
                b, n = load_obec(conn, sess, version, obec_unit_id, obec_kod, work_dir,
                                 accept_shrink=obec_kod in accept_shrink, log_level=log_level)
            except Exception as exc:  # the isolation boundary: one obec's failure, whatever it is, never ends the run
                refused += isinstance(exc, ShrinkRefused)
                failed += not isinstance(exc, ShrinkRefused)
                LOG.warning("BUILDINGS obec=%d failed: %s: %s", obec_kod, type(exc).__name__, str(exc)[:200])
                if conn.closed or conn.broken:
                    conn = loader_db.open_loader_connection()  # a dead handle cannot write the failure; a second
                    # failure here ends the run, loudly, with every finished obec already receipted
                record_failure(conn, version, obec_kod, f"{type(exc).__name__}: {exc}")
                missing_run = missing_run + 1 if getattr(exc, "missing", False) else 0
                if missing_run >= _MISSING_STOP:
                    LOG.error("BUILDINGS stopped: %d files in a row answered 404; vintage %s is not published "
                              "as per-obec state files", missing_run, version.source_date)
                    unpublished = True
                    break
                time.sleep(_PAUSE_S)
                continue
            missing_run = 0
            done, buildings, links = done + 1, buildings + b, links + n
            if only is not None or done % 250 == 0:
                LOG.info("BUILDINGS progress obce=%d/%d buildings=%d links=%d last_obec=%d last_seconds=%.1f",
                         done, len(todo), buildings, links, obec_kod, time.monotonic() - began)
            if done % _ANALYZE_EVERY == 0:
                try:
                    with conn.cursor() as cur:
                        cur.execute("analyze ruian_address_points (stavebni_objekt_code)")
                except psycopg.Error as exc:  # statistics only: a lock wait here must not end the day's run
                    LOG.warning("BUILDINGS analyze skipped: %s", type(exc).__name__)
            time.sleep(_PAUSE_S)
        LOG.info("BUILDINGS done obce=%d failed=%d refused=%d remaining=%d buildings=%d links=%d seconds=%d",
                 done, failed, refused, len(todo) - done - failed - refused, buildings, links,
                 int(time.monotonic() - started))
    finally:
        conn.close()
    # A run that tried obce and loaded none made no progress: it must not settle as an ok run.
    return 1 if unpublished or (done == 0 and failed + refused > 0) else 0


def _minutes_span(text: str) -> tuple[int, int]:
    try:
        first, last = (int(h) * 60 + int(m) for h, m in (part.split(":") for part in text.split("-")))
    except ValueError:
        raise argparse.ArgumentTypeError("expected HH:MM-HH:MM") from None
    if not 0 <= first < last <= 24 * 60:
        raise argparse.ArgumentTypeError("the window must start before it ends, within one UTC day")
    return first, last


def _minute_span(text: str) -> tuple[int, int]:
    try:
        first, last = (int(part) for part in text.split("-"))
    except ValueError:
        raise argparse.ArgumentTypeError("expected MM-MM") from None
    if not 0 <= first < last <= 60:
        raise argparse.ArgumentTypeError("minutes of one hour, the first before the last")
    return first, last


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=int, default=None, help="stop starting new obce after this long")
    parser.add_argument("--obec", type=int, action="append", help="only this obec code (repeatable)")
    parser.add_argument("--accept-shrink", type=int, action="append", default=[],
                        help="hand runs only: apply this obec's file with BOTH shrink floors off (a file with few "
                             "buildings, and a file that closes most of the obec's buildings)")
    parser.add_argument("--window-utc", type=_minutes_span, default=None, metavar="HH:MM-HH:MM",
                        help="start obce only between these UTC times; outside them the run does nothing")
    parser.add_argument("--quiet-minutes", type=_minute_span, default=None, metavar="MM-MM",
                        help="minutes of each hour in which no obec is started (waited out)")
    parser.add_argument("--work-dir", default=None, help="where each zip is downloaded before it is read")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with tempfile.TemporaryDirectory() as tmp:
        return run(max_seconds=args.max_seconds, only=args.obec, work_dir=Path(args.work_dir or tmp),
                   accept_shrink=frozenset(args.accept_shrink),
                   clock=Clock(window=args.window_utc, quiet=args.quiet_minutes))


if __name__ == "__main__":
    sys.exit(main())
