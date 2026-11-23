"""Cadastre prices bought on request: one building, one paid WSDP output, on the asking account's own ČÚZK login.

What ČÚZK charges, in its own words ("Účtování výstupů z KN poskytovaných DP a WSDP", cuzk.gov.cz, read 2026-11-22):
"Celková částka, která bude účtována za konkrétně požadovaný výstup, je uvedena u sestavy po jejím vytvoření"; "Zaúčtování
je však provedeno teprve v okamžiku, kdy uživatel potvrdí souhlas se zaúčtováním"; "Zaúčtování je provedeno pouze při
prvním otevření sestavy." Item 5010 of its price list: "Údaje o dosažených cenách nemovitostí podle nemovitostí", 50 Kč
an output. And the WSDP 3.1 description, § 3.9.2 (vratSestavu; read from the PDF by a research agent, not first-hand):
"Zaplatí (pokud nebyl ještě zaúčtován) a vrací vygenerovanou sestavu v odpovědi. … Pokud sestava neexistuje, nebo je již
smazaná, je uživateli vrácena chyba číslo 304." So ordering and polling are free, the FIRST fetch is the charge, a
second fetch of the same report is not charged again, and only error 304 says a report is gone.

ONE RUN PER ACCOUNT. A request that will talk to ČÚZK first takes the account's login lease (`cuzk_login.lease`, on
the stored login's own row; a save or removal of the login takes the same lease) and gives it back when it ends.
While it is held, every other request of that account, for this building or another, changes nothing, sends
nothing and spends nothing: it is answered with the stored state and why. The holder renews the lease before EVERY
call to ČÚZK and sends the call only if the lease is still its own, the login is still the one it read (not
refused, not re-saved), and the call is admitted by the login-check budgets. A lease may be taken over only when
it has not been renewed for `cuzk_login.LEASE_S`, three times what any single call can last by the wall clock
(`wsdp_client.CALL_MAX_S`), so a live holder is never displaced. After a fetch whose answer was lost the lease is
NOT given back: it goes stale, and for FETCH_COOL_S nobody sends that fetch again, because ČÚZK may still be
serving the first.

THE PURCHASE'S STATES (`cadastre_price_purchases.status`), every write there is, and its guard. "fenced" = the
UPDATE itself requires the account, the expected status, this run's lease token and a fresh renewal (`_move`).

  from          to            when                                               by                 guard
  (none)        pending       a new purchase is admitted                         `_run` → `_start`  under the lease; no
                                                                                                    purchase of the building
                                                                                                    in flight (also a unique
                                                                                                    index); not paid for
                                                                                                    before unless `repeat`
  pending       ordered       ČÚZK accepted the order; report id stored          `_talk`            fenced
  pending       failed        the order failed (what it ordered stays unfetched) `_talk`            fenced
  pending       failed        the user gave it up                                `cancel`           account, building, status,
                                                                                                    no file; under the lease
                                                                                                    when a login is stored
  ordered       ordered       `fetch_sent_at = now()`, BEFORE each fetch is sent `_talk`            fenced
  ordered       ordered       `fetch_sent_at` cleared: ČÚZK refused the login on `_talk`            fenced, no file
                              that very call, and no fetch was sent before
  ordered       ordered       a kept file that cannot be decrypted is dropped    `_run`             fenced
  ordered       failed        never fetched and ČÚZK no longer serves it (304,   `_talk`            fenced, no file
                              an error state)
  ordered       failed        never fetched; the user gave it up                 `cancel`           as above
  ordered /     failed        ČÚZK shows it as not yet charged (`zpracován`) at  `_talk`            fenced, no file
  paid_unread                 another price than acknowledged: not fetched
  ordered /     paid_unread   a fetch was sent and ČÚZK no longer serves it      `_talk`            fenced, no file
  paid_unread                 (`error` then starts "gone: ")
  ordered       paid_unread   a fetch was sent; the user gave it up              `cancel`           as above
  paid_unread   paid_unread   a kept file that cannot be decrypted is dropped    `_run`             fenced
  ordered /     same, file    the fetch answered: file and cost stored, before   `keep_fetched`     NOT fenced: account, status
  paid_unread   kept          the ledger is settled and before anything reads it
  failed        paid_unread,  the fetch answered after the purchase was closed   `keep_fetched`     NOT fenced
                file kept
  ordered /     paid_unread   the fetched file cannot be read                    `mark_unread`      NOT fenced: account, status
  paid_unread
  ordered /     done          the deeds are stored; the file cleared unless it   `finish`           NOT fenced: account, status
  paid_unread                 held no deed
  done (file)   done          a kept file that held no deed is read again        `finish`           NOT fenced: account, status

The four NOT-fenced writes follow a fetch ČÚZK has already answered (or read a file already kept): they must never
be dropped, each is guarded by status so it is harmless twice, and each one's row count is read.

Ways out of every state that is not an end: `pending`/`ordered` are continued by id or given up (`cancel`, which
sends nothing and needs no login). `paid_unread` is retried for free by id (the kept file is read again; with no
file, or one that can no longer be decrypted, the report is fetched again, which ČÚZK does not charge twice)
unless ČÚZK said the report is gone, and a new purchase of the building may be confirmed beside it.

TWO KINDS, ONE MACHINE (migration 767). A purchase is of a `Kind`: the building's price report, or one unit's
"Informace o jednotkách" (10 Kč, only for a unit named in a deed the account already bought). Everything above
holds for both; a kind differs only in what is ordered, the price it may cost, what is stored from the file, and
this: a unit's report names the unit's owners, so that part is cut out before the file is kept, and if the cut
fails the file is not kept at all (the purchase is then paid and unread with no file, and fetching it again is
free while ČÚZK serves it). Every statement names the kind and the unit, so neither reads as the other's.

Every call a run sends is one row of the login-check ledger (`cuzk_login.admit_check`, #2608), admitted before it
leaves and settled by its own answer. No database connection is held while waiting on ČÚZK. Everything is the
buying account's own (operator ruling 2026-11-22): every statement names the account (service-role connection)."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from api import cadastre_deed_matches, cadastre_price_report, cadastre_unit_report, cuzk_login, wsdp_client
from api.building_scope import holds_no_building_ad

LOG = logging.getLogger(__name__)

PRICE_CZK = 50  # ČÚZK's price list, item 5010; one output covers up to 200 properties of one type (vyhláška 358/2013)
UNIT_PRICE_CZK = 10  # item 5004, "Informace o bytech a nebytových prostorech (jednotkách)": 10 Kč a unit
POLL_EVERY_S = 2
POLL_FOR_S = 30
# No call to ČÚZK is started later than this into a request, so the request ends within RUN_S + CALL_MAX_S: inside
# the SPA's own deadline, and the card is never told "failed" while the server still works.
RUN_S = 70
_WAITING = frozenset({"čeká", "vytváří se", "podepisuje se"})
_UNCHARGED, _CHARGED = "zpracován", "zaúčtován"

Opener = Callable[[], AbstractContextManager[psycopg.Connection]]


class NoProperty(LookupError):
    """The property is unknown."""


class NoBuilding(LookupError):
    """The property's door names no building the cadastre knows."""


class NoLogin(LookupError):
    """The account has no ČÚZK login stored."""


class LoginRefused(RuntimeError):
    """ČÚZK refused the stored login earlier; nothing is sent on it until it is saved again."""


class TooManyUnits(ValueError):
    """The building has more units than one output covers."""


class InProgress(RuntimeError):
    """This request did not do what it asked, and nothing it must answer for changed. Its one argument says why,
    for the card: `busy` (another request of the account is using the ČÚZK login), `in_flight` (a purchase of
    this building is in flight and was not named), `preparing` (ČÚZK has not finished the report), `fetching` (a
    fetch was sent moments ago and may still be served), `time` (the request's own time ran out before its next
    call), `login` (the stored login may not be used right now). The stored state says where the purchase stands."""

    @property
    def why(self) -> str:
        return str(self.args[0]) if self.args else "busy"


class AlreadyBought(RuntimeError):
    """The account already paid for this building's report; a second one needs the caller's `repeat`."""

    def __init__(self, finished_at: Any) -> None:
        super().__init__("already bought")
        self.finished_at = finished_at


class NotResumable(RuntimeError):
    """The purchase the request names is not one that can be continued (it ended, or is not this building's)."""


class PriceChanged(RuntimeError):
    """ČÚZK states another price for the finished report than the one acknowledged; it was not fetched."""

    def __init__(self, stated_czk: int | None) -> None:
        super().__init__(f"stated price {stated_czk}")
        self.stated_czk = stated_czk


class ReportGone(RuntimeError):
    """ČÚZK no longer serves the report. `possibly_charged`: a fetch of it had been sent."""

    def __init__(self, possibly_charged: bool) -> None:
        super().__init__("report gone")
        self.possibly_charged = possibly_charged


class ChargeUncertain(RuntimeError):
    """The charging fetch was sent and its answer did not arrive; continuing fetches the same report."""


class PaidUnread(RuntimeError):
    """The report was fetched (charged) and its deeds are not stored; the file is kept."""


@dataclass(frozen=True)
class Building:
    code: int
    iskn_id: int


@dataclass(frozen=True)
class Kind:
    """Which paid output a purchase is: the building's price report, or one unit's information (migration 767).
    The machine is the same for both; a kind supplies what is ordered, its price, what is cut from the file
    before it is kept, and what is stored from it. Every statement over `cadastre_price_purchases` names the kind
    and the unit (`_OF`), so the two never read as each other's."""
    name: str
    price_czk: int
    unit_iskn_id: int | None = None

    @property
    def of(self) -> tuple[str, int | None]:
        return self.name, self.unit_iskn_id


PRICES = Kind("prices", PRICE_CZK)
_OF = "kind = %s and unit_iskn_id is not distinct from %s"


def unit(iskn_id: int) -> Kind:
    return Kind("unit", UNIT_PRICE_CZK, iskn_id)


class NoUnit(LookupError):
    """The unit is in no deed this account bought for the building: only such a unit can be asked about."""


@dataclass(frozen=True)
class Purchase:
    id: int
    status: str
    report_id: int | None
    fetch_sent: bool
    kept: bytes | None
    fetching: bool  # a fetch was sent too recently to send another
    gone: bool      # ČÚZK said the report no longer exists


_BUILDING_SQL = """
select b.code, b.iskn_building_id, p.id is not null as found
  from properties p
  left join listing_location ll on ll.listing_id = p.location_listing_ref_id
  left join ruian_address_points ap on ap.kod_adm = ll.ruian_adm_kod
  left join ruian_building_objects b on b.code = ap.stavebni_objekt_code and b.valid_to is null
   and not {out_of_scope}
 where p.id = %s and p.status = 'active'
""".format(out_of_scope=holds_no_building_ad("p"))


def building_of(conn: psycopg.Connection, property_id: int) -> Building:
    with conn.cursor() as cur:
        cur.execute(_BUILDING_SQL, (property_id,))
        row = cur.fetchone()
    if row is None:
        raise NoProperty(property_id)
    if row[0] is None or row[1] is None:
        raise NoBuilding(property_id)
    return Building(int(row[0]), int(row[1]))


# A fetch that was sent is not sent again for this long, whoever asks. On our side nothing is in flight past
# CALL_MAX_S (the caller ends the request and shuts its socket), so this is the time we allow ČÚZK to finish a
# fetch it has already received: its "only at the first opening" speaks of a repeat after the charge, not of two
# first openings at once. The premise, stated: ČÚZK finishes handling a request within FETCH_COOL_S - CALL_MAX_S
# (140 s) of receiving it. It is a judgement, not ČÚZK's rule.
FETCH_COOL_S = 180
assert FETCH_COOL_S >= 3 * wsdp_client.CALL_MAX_S
# A fenced move also needs the lease renewed this recently, so a holder stalled between its renewal and its write
# (a slow database connection) cannot write on a lease it is about to lose.
BEAT_FRESH_S = 60
_GONE = "gone: "  # the prefix of `error` when ČÚZK said the report no longer exists

_PURCHASE_COLS = (
    "id, status, report_id, fetch_sent_at is not null, raw_report, "
    f"raw_report is null and fetch_sent_at > now() - make_interval(secs => {FETCH_COOL_S}), "
    f"coalesce(error like '{_GONE}%%', false)"
)


def _purchase(row: tuple[Any, ...] | None) -> Purchase | None:
    if row is None:
        return None
    return Purchase(int(row[0]), row[1], row[2], bool(row[3]), bytes(row[4]) if row[4] is not None else None,
                    bool(row[5]), bool(row[6]))


def bought_at(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int, kind: Kind = PRICES) -> Any:
    """When the account last paid for this output (read or not); None when it never did."""
    with conn.cursor() as cur:
        cur.execute(
            "select max(coalesce(finished_at, requested_at)) from cadastre_price_purchases "
            f"where account_id = %s and building_code = %s and {_OF} and status in ('done', 'paid_unread')",
            (account_id, building_code, *kind.of),
        )
        return cur.fetchone()[0]


def target(conn: psycopg.Connection, account_id: uuid.UUID, building: Building, *, resume: int | None,
           repeat: bool, kind: Kind = PRICES) -> Purchase | None:
    """Which purchase this request is about; None for a new one. A resume names a purchase that can still be
    continued: one in flight, one paid and unread that ČÚZK has not said is gone (or whose file is kept), or a
    finished one whose kept file held no deed."""
    with conn.cursor() as cur:
        if resume is not None:
            cur.execute(
                f"select {_PURCHASE_COLS} from cadastre_price_purchases "
                f"where id = %s and account_id = %s and building_code = %s and {_OF} "
                "and (status in ('pending', 'ordered', 'paid_unread') or (status = 'done' and raw_report is not null))",
                (resume, account_id, building.code, *kind.of),
            )
            named = _purchase(cur.fetchone())
            if named is None or (named.gone and named.kept is None):
                raise NotResumable(str(resume))
            return named
        cur.execute(
            "select 1 from cadastre_price_purchases "
            f"where account_id = %s and building_code = %s and {_OF} and status in ('pending', 'ordered')",
            (account_id, building.code, *kind.of),
        )
        if cur.fetchone() is not None:
            raise InProgress("in_flight")
    if not repeat and (at := bought_at(conn, account_id, building.code, kind)) is not None:
        raise AlreadyBought(at)
    return None


def _start(conn: psycopg.Connection, account_id: uuid.UUID, building: Building, kind: Kind = PRICES) -> Purchase:
    with conn.cursor() as cur:
        cur.execute(
            "insert into cadastre_price_purchases (account_id, building_code, iskn_building_id, kind, unit_iskn_id) "
            f"values (%s, %s, %s, %s, %s) returning {_PURCHASE_COLS}",
            (account_id, building.code, building.iskn_id, *kind.of),
        )
        return _purchase(cur.fetchone())  # type: ignore[return-value]


def _move(conn: psycopg.Connection, account_id: uuid.UUID, token: uuid.UUID, purchase_id: int,
          expect: tuple[str, ...], sets: str, params: tuple[Any, ...] = (), *, no_file: bool = False) -> None:
    """One fenced state change: the row is the account's, in an expected state, and the lease is this run's and
    freshly renewed. Anything else means this run no longer owns the purchase, and it stops without touching it.
    `no_file`: only a purchase with no kept file may be ended as uncharged or as lost."""
    with conn.cursor() as cur:
        cur.execute(
            f"update cadastre_price_purchases p set {sets} "
            "where p.id = %s and p.account_id = %s and p.status = any(%s) "
            f"{'and p.raw_report is null ' if no_file else ''}"
            "and exists (select 1 from account_cuzk_logins l where l.account_id = p.account_id "
            "            and l.run_token = %s and l.run_beat_at > now() - make_interval(secs => %s))",
            (*params, purchase_id, account_id, list(expect), token, BEAT_FRESH_S),
        )
        if cur.rowcount != 1:
            raise InProgress("busy")


def _status(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("select status from cadastre_price_purchases where id = %s and account_id = %s",
                    (purchase_id, account_id))
        row = cur.fetchone()
    return row[0] if row else None


def keep_fetched(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, cost_czk: int | None,
                 document: bytes) -> None:
    """The fetched file and what it cost, before anything reads it. Never fenced and never dropped: ČÚZK has
    answered the charging fetch, so whatever the row says now, it becomes a paid purchase with its file."""
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set cost_czk = %s, raw_report = %s, "
            "       fetch_sent_at = coalesce(fetch_sent_at, now()), "
            "       status = case when status = 'failed' then 'paid_unread' else status end "
            "where id = %s and account_id = %s and status in ('ordered', 'paid_unread', 'failed')",
            (cost_czk, cuzk_login.seal(document), purchase_id, account_id),
        )
        kept = cur.rowcount == 1
    if not kept and _status(conn, account_id, purchase_id) != "done":
        LOG.error("CADASTRE fetched report not kept: purchase=%s", purchase_id)
        raise PaidUnread(str(purchase_id))


def keep_cost_only(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, cost_czk: int | None,
                   error: str) -> None:
    """The charging fetch answered and its file must not be kept: the purchase is paid and unread, with no file.
    Never fenced, like `keep_fetched`."""
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set cost_czk = %s, status = 'paid_unread', error = %s, "
            "       fetch_sent_at = coalesce(fetch_sent_at, now()), finished_at = coalesce(finished_at, now()) "
            "where id = %s and account_id = %s and status in ('ordered', 'paid_unread', 'failed') "
            "and raw_report is null",
            (cost_czk, error[:200], purchase_id, account_id),
        )
        if cur.rowcount != 1:
            LOG.error("CADASTRE unkept report not marked: purchase=%s", purchase_id)


def mark_unread(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, error: str) -> None:
    """The fetched file cannot be read: the purchase ends paid and unread, its file kept."""
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'paid_unread', error = %s, "
            "       finished_at = coalesce(finished_at, now()) "
            "where id = %s and account_id = %s and status in ('ordered', 'paid_unread')",
            (error[:200], purchase_id, account_id),
        )
        marked = cur.rowcount == 1
    if not marked and _status(conn, account_id, purchase_id) != "done":
        LOG.error("CADASTRE unread report not marked: purchase=%s", purchase_id)


def finish(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int,
           deeds: list[cadastre_price_report.Deed], *, facts: cadastre_unit_report.UnitFacts | None = None) -> None:
    """Store the deeds and clear the kept file. A report that held no deed keeps its file: "the cadastre knows no
    price" and "we read none" look the same from here, and the file is the only proof of which; it can be read
    again later. A purchase already finished with its file cleared keeps the deeds it has. A unit's report always
    holds its unit (`facts`), so its file is always cleared."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'done', error = null, "
            "       finished_at = coalesce(case when status = 'done' then finished_at end, now()), "
            "       raw_report = case when %s then null else raw_report end "
            "where id = %s and account_id = %s "
            "and (status in ('ordered', 'paid_unread') or (status = 'done' and raw_report is not null))",
            (bool(deeds) or facts is not None, purchase_id, account_id),
        )
        if cur.rowcount != 1:
            if _status(conn, account_id, purchase_id) != "done":
                raise PaidUnread(str(purchase_id))
            return
        for deed in deeds:
            cur.execute(
                """
                insert into cadastre_deeds (purchase_id, account_id, proceeding, kind, price, currency,
                                            signed_on, effective_on, entered_on, properties)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (purchase_id, account_id, deed.proceeding, deed.kind, deed.price, deed.currency, deed.signed_on,
                 deed.effective_on, deed.entered_on,
                 Jsonb([{"kind": p.kind, "iskn_id": p.iskn_id, "label": p.label} for p in deed.properties])),
            )
        if facts is not None:
            cur.execute(
                """
                insert into cadastre_unit_facts (purchase_id, account_id, unit_iskn_id, label, floor_area_m2,
                                                 use_code, use_label, share_num, share_den)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (purchase_id, account_id, facts.iskn_id, facts.label, facts.floor_area_m2, facts.use_code,
                 facts.use_label, facts.share_num, facts.share_den),
            )


def _read(conn_of: Opener, account_id: uuid.UUID, purchase_id: int, document: bytes, kind: Kind = PRICES) -> None:
    """A fetched file → what it states, stored; a file that cannot be read ends the purchase as paid and unread,
    kept. A unit's report must be about the unit that was asked for."""
    try:
        deeds: list[cadastre_price_report.Deed] = []
        facts = None
        if kind.unit_iskn_id is None:
            deeds = cadastre_price_report.parse(document)
        else:
            facts = cadastre_unit_report.parse(document)
            if facts.iskn_id != kind.unit_iskn_id:
                raise cadastre_unit_report.UnitReportError(f"a report about unit {facts.iskn_id}")
        with conn_of() as conn:
            finish(conn, account_id, purchase_id, deeds, facts=facts)
    except PaidUnread:
        raise
    except Exception as exc:
        with conn_of() as conn:
            mark_unread(conn, account_id, purchase_id, f"{type(exc).__name__}: {exc}")
        raise PaidUnread(str(purchase_id)) from exc


_STATE_SQL = f"""
select id, status, cost_czk, object_count, requested_at, finished_at,
       fetch_sent_at is not null as fetch_sent, raw_report is not null as file_kept,
       status in ('ordered', 'paid_unread') and raw_report is null
         and fetch_sent_at > now() - make_interval(secs => {FETCH_COOL_S}) as fetching,
       coalesce(error like '{_GONE}%%', false) as gone
  from cadastre_price_purchases
 where account_id = %s and building_code = %s and kind = 'prices' and status <> 'failed'
 order by requested_at desc, id desc
 limit 1
"""
# The deeds shown are those of the newest purchase that HAS deeds: a later purchase that read none must not hide
# what an earlier one bought.
_DEEDS_SQL = """
select d.id, d.purchase_id, d.proceeding, d.kind, d.price, d.currency, d.signed_on, d.effective_on, d.entered_on,
       d.properties
  from cadastre_deeds d
 where d.account_id = %s
   and d.purchase_id = (select p.id from cadastre_price_purchases p
                         where p.account_id = %s and p.building_code = %s and p.kind = 'prices'
                           and p.status = 'done'
                           and exists (select 1 from cadastre_deeds x where x.purchase_id = p.id)
                         order by p.requested_at desc, p.id desc limit 1)
 order by d.effective_on desc nulls last, d.proceeding
"""


# Each unit the account asked about: its newest purchase that did not end uncharged, and what that one read.
_UNITS_SQL = f"""
select distinct on (p.unit_iskn_id)
       p.unit_iskn_id, p.id, p.status, p.cost_czk, p.fetch_sent_at is not null as fetch_sent,
       p.raw_report is not null as file_kept,
       p.status in ('ordered', 'paid_unread') and p.raw_report is null
         and p.fetch_sent_at > now() - make_interval(secs => {FETCH_COOL_S}) as fetching,
       coalesce(p.error like '{_GONE}%%', false) as gone,
       f.id is not null as read, f.floor_area_m2, f.use_code, f.use_label, f.share_num, f.share_den
  from cadastre_price_purchases p
  left join cadastre_unit_facts f on f.purchase_id = p.id and f.account_id = p.account_id
 where p.account_id = %s and p.building_code = %s and p.kind = 'unit' and p.status <> 'failed'
 order by p.unit_iskn_id, p.requested_at desc, p.id desc
"""
_UNIT_PURCHASE = ("id", "status", "cost_czk", "fetch_sent", "file_kept", "fetching", "gone")
_UNIT_FACTS = ("floor_area_m2", "use_code", "use_label", "share_num", "share_den")


def state(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int) -> dict[str, Any]:
    """The account's newest purchase of the building that did not end uncharged, the deeds it holds for the
    building (each with what we hold of the same sale, `cadastre_deed_matches`), and whether a request of the account
    is live right now. A read: it calls nothing at ČÚZK."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_STATE_SQL, (account_id, building_code))
        purchase = cur.fetchone()
        cur.execute(_DEEDS_SQL, (account_id, account_id, building_code))
        deeds = cur.fetchall()
        cur.execute(_UNITS_SQL, (account_id, building_code))
        units = cur.fetchall()
    running = cuzk_login.run_live(conn, account_id)
    # The card re-reads this state every few seconds while a run is live; the match is not worth that (it scans
    # `listing_location` for the building's doors), so it waits for the run to end.
    matched = (cadastre_deed_matches.matches(conn, account_id, deeds[0]["purchase_id"], building_code)
               if deeds and not running else {})
    if purchase is not None:
        purchase = {**purchase, "requested_at": purchase["requested_at"].isoformat(),
                    "finished_at": purchase["finished_at"].isoformat() if purchase["finished_at"] else None}
    return {
        "price_czk": PRICE_CZK,
        "unit_price_czk": UNIT_PRICE_CZK,
        "units": [{"unit_iskn_id": u["unit_iskn_id"], "purchase": {k: u[k] for k in _UNIT_PURCHASE},
                   "facts": ({**{k: u[k] for k in _UNIT_FACTS},
                              "floor_area_m2": float(u["floor_area_m2"]) if u["floor_area_m2"] is not None else None}
                             if u["read"] else None)}
                  for u in units],
        "running": running,
        "purchase": purchase,
        "deeds": [{**{k: v for k, v in d.items() if k not in ("id", "purchase_id")},
                   "price": float(d["price"]) if d["price"] is not None else None,
                   **{k: d[k].isoformat() if d[k] else None for k in ("signed_on", "effective_on", "entered_on")},
                   "match": matched.get(d["id"])}
                  for d in deeds],
    }


class _Run:
    """The lease holder's calls to ČÚZK. Each call is one row of the login-check ledger: admitted through the
    budgets just before it leaves, settled by its own answer, never changed afterwards. A refusal marks the login
    the run read. An error ČÚZK gives AFTER accepting the login is an accepted login."""

    def __init__(self, account_id: uuid.UUID, token: uuid.UUID, held: cuzk_login.Leased, first_check: int,
                 opener: Opener, started: float, clock: Callable[[], float]) -> None:
        self.account_id, self.token, self.held = account_id, token, held
        self.opener, self._started, self._clock = opener, started, clock
        self._first: int | None = first_check  # admitted with the run, for its first call

    def unused(self) -> None:
        """The run ends without having sent its first call: that check never happened."""
        if self._first is not None:
            with self.opener() as conn:
                cuzk_login.withdraw_check(conn, self._first)
            self._first = None

    def call(self, fn: Callable[..., Any], *args: Any, before: Callable[[], None] | None = None,
             then: Callable[[Any], None] | None = None) -> Any:
        """Send one call, only if this run may still send: inside its time, the lease renewed and still its own,
        the login still the one it read, and the call admitted by the budgets (it is not sent if it could be the
        username's third unaccepted check). `before` runs once that is known and before the call leaves; `then`
        runs on the answer BEFORE the ledger is settled, so what the answer brought is stored first."""
        if self._clock() - self._started > RUN_S:
            raise InProgress("time")
        with self.opener() as conn:
            if not cuzk_login.renew(conn, self.account_id, self.token, self.held):
                raise InProgress("login")
            check_id = self._first
            if check_id is None:
                try:
                    check_id = cuzk_login.admit_check(conn, self.account_id, self.held.username, in_run=True)
                except cuzk_login.Throttled:
                    raise InProgress("login") from None
        self._first = None
        try:
            if before is not None:
                before()
        except BaseException:
            with self.opener() as conn:
                cuzk_login.withdraw_check(conn, check_id)
            raise
        try:
            result = fn(*args, self.held.username, self.held.password)
        except wsdp_client.WsdpError as exc:
            outcome = "refused" if exc.reason == "refused" else "accepted" if exc.authenticated else "no_answer"
            with self.opener() as conn:
                cuzk_login.settle_check(conn, check_id, outcome)
                if outcome == "refused":
                    cuzk_login.mark_refused(conn, self.account_id, self.held)
            raise
        try:
            if then is not None:
                then(result)
        finally:
            with self.opener() as conn:
                cuzk_login.settle_check(conn, check_id, "accepted")
        return result

    def move(self, purchase_id: int, expect: tuple[str, ...], sets: str, params: tuple[Any, ...] = (), *,
             no_file: bool = False) -> None:
        with self.opener() as conn:
            _move(conn, self.account_id, self.token, purchase_id, expect, sets, params, no_file=no_file)

    def close(self, purchase_id: int, expect: tuple[str, ...], sets: str, params: tuple[Any, ...]) -> None:
        """A move made while another error is on its way out: it must not replace that error."""
        try:
            self.move(purchase_id, expect, sets, params, no_file=True)
        except InProgress:
            LOG.warning("CADASTRE purchase %s not closed: no longer this run's", purchase_id)


def _release(opener: Opener, account_id: uuid.UUID, token: uuid.UUID) -> None:
    """Give the lease back without ever replacing the request's own outcome: a lease that cannot be released now
    simply goes stale."""
    try:
        with opener() as conn:
            cuzk_login.release(conn, account_id, token)
    except Exception:
        LOG.exception("CADASTRE lease not released: account=%s", account_id)


def _read_kept(opener: Opener, account_id: uuid.UUID, purchase: Purchase, kind: Kind = PRICES) -> bool:
    """Read a purchase's kept file again (free: nothing is sent). False when the file can no longer be decrypted."""
    try:
        document = cuzk_login.unseal(purchase.kept or b"")
    except (cuzk_login.Unreadable, cuzk_login.NotConfigured):
        return False
    _read(opener, account_id, purchase.id, document, kind)
    return True


def _unit_in_a_bought_deed(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int, iskn_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from cadastre_deeds d join cadastre_price_purchases p on p.id = d.purchase_id "
            "where d.account_id = %s and p.account_id = %s and p.building_code = %s and p.kind = 'prices' "
            "and d.properties @> %s limit 1",
            (account_id, account_id, building_code, Jsonb([{"kind": "jednotka", "iskn_id": iskn_id}])),
        )
        return cur.fetchone() is not None


def buy(account_id: uuid.UUID, property_id: int, *, opener: Opener, repeat: bool = False,
        resume: int | None = None, kind: Kind = PRICES, sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> Building:
    """Start the account's purchase of the property's building's price report (or, `kind`, of one unit's
    information), or continue the purchase `resume` names, and store what it states. Holds the account's login
    lease from before its first call to its end."""
    started = clock()
    with opener() as conn:
        building = building_of(conn, property_id)
        if kind.unit_iskn_id is not None and not _unit_in_a_bought_deed(conn, account_id, building.code,
                                                                        kind.unit_iskn_id):
            raise NoUnit(str(kind.unit_iskn_id))
    wsdp_client.base_url()  # a misconfigured address sends nothing, so it must spend nothing
    token = uuid.uuid4()
    with opener() as conn:
        try:
            held = cuzk_login.lease(conn, account_id, token)
        except cuzk_login.LeaseHeld:
            raise InProgress("busy") from None
    if held is None:
        # No login is stored, so no run can be live. Reading a file that is already kept sends nothing and needs
        # none; everything else does.
        with opener() as conn:
            purchase = (target(conn, account_id, building, resume=resume, repeat=repeat, kind=kind)
                        if resume is not None else None)
        if purchase is None or purchase.kept is None:
            raise NoLogin(str(account_id))
        if not _read_kept(opener, account_id, purchase, kind):
            raise PaidUnread(str(purchase.id))
        return building
    give_back = True
    try:
        _run(account_id, building, token, held, opener=opener, repeat=repeat, resume=resume, kind=kind,
             sleep=sleep, clock=clock, started=started)
    except ChargeUncertain:
        # The fetch may still be being served. The lease is left to go stale, so nothing of this account talks to
        # ČÚZK until this request's own call cannot be alive any more.
        give_back = False
        raise
    finally:
        if give_back:
            _release(opener, account_id, token)
    return building


def cancel(account_id: uuid.UUID, property_id: int, purchase_id: int, *, opener: Opener,
           kind: Kind = PRICES) -> Building:
    """Give up a purchase in flight, sending nothing: the way out when it cannot be continued. One that was never
    fetched ends `failed` (nothing charged: an unfetched report costs nothing); one whose fetch was sent ends
    `paid_unread` (it may have been charged), and the building can then be bought anew with the confirmed repeat.
    Needs no stored login; with one, it waits its turn behind a live request like everything else."""
    with opener() as conn:
        building = building_of(conn, property_id)
    token = uuid.uuid4()
    with opener() as conn:
        try:
            leased = cuzk_login.take_lease(conn, account_id, token) is not None
        except cuzk_login.LeaseHeld:
            raise InProgress("busy") from None
    try:
        with opener() as conn, conn.cursor() as cur:
            cur.execute(
                "update cadastre_price_purchases set finished_at = now(), error = 'given up by the user', "
                "       status = case when fetch_sent_at is null then 'failed' else 'paid_unread' end "
                f"where id = %s and account_id = %s and building_code = %s and {_OF} "
                "and status in ('pending', 'ordered') and raw_report is null",
                (purchase_id, account_id, building.code, *kind.of),
            )
            if cur.rowcount != 1:
                raise NotResumable(str(purchase_id))
    finally:
        if leased:
            _release(opener, account_id, token)
    return building


def _run(account_id: uuid.UUID, building: Building, token: uuid.UUID, held: cuzk_login.Leased, *, opener: Opener,
         repeat: bool, resume: int | None, kind: Kind, sleep: Callable[[float], None], clock: Callable[[], float],
         started: float) -> None:
    with opener() as conn:
        purchase = target(conn, account_id, building, resume=resume, repeat=repeat, kind=kind)
    if purchase is not None and purchase.kept is not None:
        if _read_kept(opener, account_id, purchase, kind):  # free: nothing is sent, so the login is not looked at
            return
        if purchase.status == "done":
            raise PaidUnread(str(purchase.id))  # its deeds are stored; only the proof of an empty report is lost
        # The kept file can no longer be decrypted (the key changed). ČÚZK serves a fetched report again without
        # charging, so drop the dead copy and fetch it anew below.
        with opener() as conn:
            _move(conn, account_id, token, purchase.id, ("ordered", "paid_unread"), "raw_report = null")
    if held.refused:
        raise LoginRefused(str(account_id))
    if purchase is not None and purchase.fetching:
        raise InProgress("fetching")
    with opener() as conn, conn.transaction():
        first_check = cuzk_login.admit_check(conn, account_id, held.username)
        if purchase is None:
            purchase = _start(conn, account_id, building, kind)
    run = _Run(account_id, token, held, first_check, opener, started, clock)
    try:
        _talk(run, building, purchase, kind, sleep=sleep, clock=clock)
    finally:
        run.unused()


def _order(run: _Run, building: Building, kind: Kind) -> tuple[wsdp_client.Report, int]:
    """Order the kind's report: (the report, how many properties it covers)."""
    if kind.unit_iskn_id is not None:
        return run.call(wsdp_client.generate_unit_info, kind.unit_iskn_id), 1
    units = run.call(wsdp_client.find_units, building.iskn_id)
    if len(units) > wsdp_client.MAX_PRICE_OBJECTS:
        raise TooManyUnits(len(units))
    object_type, ids = ("JED_S", [u.iskn_id for u in units]) if units else ("STA_S", [building.iskn_id])
    return run.call(wsdp_client.generate_prices, object_type, ids), len(ids)


def _talk(run: _Run, building: Building, purchase: Purchase, kind: Kind, *, sleep: Callable[[float], None],
          clock: Callable[[], float]) -> None:
    """The run's conversation with ČÚZK: order if not ordered, wait for the report, fetch it once, store it."""
    report_id = purchase.report_id
    if report_id is None:
        try:
            ordered, covered = _order(run, building, kind)
        except (wsdp_client.WsdpError, TooManyUnits) as exc:
            run.close(purchase.id, ("pending",), "status = 'failed', error = %s, finished_at = now()",
                      (f"{type(exc).__name__}: {exc}"[:200],))
            raise
        report_id = ordered.id
        run.move(purchase.id, ("pending",), "status = 'ordered', report_id = %s, object_count = %s",
                 (report_id, covered))

    fetch_sent = purchase.fetch_sent or purchase.status == "paid_unread"

    def gone(why: str, *, sent: bool) -> None:
        """ČÚZK no longer serves the report: nothing charged if no fetch of it was ever sent."""
        if sent:
            run.close(purchase.id, ("ordered", "paid_unread"),
                      "status = 'paid_unread', error = %s, finished_at = coalesce(finished_at, now())",
                      ((_GONE + why)[:200],))
        else:
            run.close(purchase.id, ("ordered",), "status = 'failed', error = %s, finished_at = now()",
                      ((_GONE + why)[:200],))
        raise ReportGone(sent)

    deadline = clock() + POLL_FOR_S
    while True:
        try:
            report = run.call(wsdp_client.report_state, report_id)
        except wsdp_client.WsdpError as exc:
            if exc.reason == "service" and exc.status == wsdp_client.REPORT_GONE:
                gone(f"report {report_id}: error 304", sent=fetch_sent)
            raise  # any other error says nothing about the report: the purchase stays as it is
        if report.state in (_UNCHARGED, _CHARGED):
            break
        if report.state not in _WAITING:
            gone(f"report {report_id}: state {report.state}", sent=fetch_sent)
        if clock() >= deadline:
            raise InProgress("preparing")
        sleep(POLL_EVERY_S)

    if report.state == _UNCHARGED and (report.price_czk is None or report.price_czk > kind.price_czk):
        # ČÚZK shows it as never fetched, so nothing was charged whatever we sent before; and it will not be.
        run.close(purchase.id, ("ordered", "paid_unread"),
                  "status = 'failed', error = %s, finished_at = now(), fetch_sent_at = null",
                  (f"stated price {report.price_czk}",))
        raise PriceChanged(report.price_czk)

    def mark_sent() -> None:
        run.move(purchase.id, ("ordered", "paid_unread"), "fetch_sent_at = now()")

    def keep(answer: tuple[wsdp_client.Report, bytes]) -> None:
        fetched, document = answer
        cost = fetched.price_czk if fetched.price_czk is not None else report.price_czk
        if kind.unit_iskn_id is not None:
            # A unit's report names its owners: the file is kept without that part, or (if the cut fails) not at
            # all. The purchase is then paid with no file, and ČÚZK serves the report again for free.
            try:
                document = cadastre_unit_report.redact(document)
            except cadastre_unit_report.UnitReportError as exc:
                with run.opener() as conn:
                    keep_cost_only(conn, run.account_id, purchase.id, cost, f"not kept: {exc}")
                raise PaidUnread(str(purchase.id)) from exc
        with run.opener() as conn:
            keep_fetched(conn, run.account_id, purchase.id, cost, document)

    try:
        _fetched, document = run.call(wsdp_client.fetch_report, report_id, before=mark_sent, then=keep)
    except wsdp_client.WsdpError as exc:
        if exc.reason == "refused":
            # Refused before anything was served, so this call charged nothing: do not leave it reading as sent.
            if not fetch_sent:
                run.close(purchase.id, ("ordered",), "fetch_sent_at = %s", (None,))
            raise
        if exc.reason == "service" and exc.status == wsdp_client.REPORT_GONE:
            gone(f"report {report_id}: error 304 at the fetch", sent=True)
        raise ChargeUncertain(str(report_id)) from exc
    _read(run.opener, run.account_id, purchase.id, document, kind)
