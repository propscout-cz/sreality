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
nothing and spends nothing: it is answered with the stored state. The holder renews the lease before EVERY call to
ČÚZK and sends the call only if the lease is still its own, the login is still the one it read (not refused, not
re-saved), and nobody else's check on that username is in doubt. A lease may be taken over only when it has not
been renewed for `cuzk_login.LEASE_S`, three times what any single call can last by the wall clock
(`wsdp_client.CALL_MAX_S`), so a live holder is never displaced.

THE PURCHASE'S STATES (`cadastre_price_purchases.status`). Every move is made by the lease holder and is fenced on
the lease in its own UPDATE, except the three marked *, which follow a fetch ČÚZK has already answered and must
never be dropped:

  from          to            when                                                       guard
  (none)        pending       a new purchase is admitted                                 lease; no purchase in flight
                                                                                         for the building; not already
                                                                                         paid for unless `repeat`
  pending       ordered       ČÚZK accepted the order (report id stored)                 lease, status
  pending       failed        the order failed (anything it ordered stays unfetched),    lease, status
                              or the user gave the purchase up
  ordered       ordered       `fetch_sent_at` set, BEFORE the fetch is sent (cleared     lease, status
                              again only if ČÚZK refused the login on that very call);
                              or a kept file that can no longer be decrypted is dropped
  ordered       failed        never fetched, and: ČÚZK no longer serves it (304, an      lease, status, no file kept,
                              error state), or the user gave it up                       fetch never sent
  ordered /     failed        ČÚZK shows the report as not yet charged (`zpracován`)     lease, status, no file kept
  paid_unread                 and states another price than acknowledged: not fetched
  ordered       paid_unread   a fetch was sent, and: ČÚZK no longer serves the report,   lease, status, no file kept
                              or the user gave it up
  ordered       paid_unread * the fetched file cannot be read                            status
  failed        paid_unread * a fetch answered after the purchase had been closed        (always)
  any but done  (file kept) * the fetch answered: file and cost stored first             status <> done
  ordered /     done        * the deeds are stored; the kept file cleared unless it      status
  paid_unread                 held no deed

Ways out of every state that is not an end: `pending`/`ordered` are continued by id, or given up (`cancel`, which
sends nothing). `paid_unread` is retried for free by id (the kept file is read again; with no file, or one that can
no longer be decrypted, the report is fetched again, which ČÚZK does not charge twice), and a new purchase of the
building may be confirmed beside it.

Every purchase run is one use of the login in the login-check ledger (`cuzk_login.admit_check`, #2608). No database
connection is held while waiting on ČÚZK. Everything is the buying account's own (operator ruling 2026-11-22): every
statement names the account (service-role connection)."""

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

from api import cadastre_price_report, cuzk_login, wsdp_client

LOG = logging.getLogger(__name__)

PRICE_CZK = 50  # ČÚZK's price list, item 5010; one output covers up to 200 properties of one type (vyhláška 358/2013)
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
    """This request changed nothing it must answer for: another run of the account holds the lease, a purchase
    of this building is in flight and was not named, ČÚZK is still preparing the report, or the request's own
    time ran out before its next call. The stored state says where the purchase stands."""


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
class Purchase:
    id: int
    status: str
    report_id: int | None
    fetch_sent: bool
    kept: bytes | None


_BUILDING_SQL = """
select b.code, b.iskn_building_id, p.id is not null as found
  from properties p
  left join listing_location ll on ll.listing_id = p.location_listing_ref_id
  left join ruian_address_points ap on ap.kod_adm = ll.ruian_adm_kod
  left join ruian_building_objects b on b.code = ap.stavebni_objekt_code and b.valid_to is null
 where p.id = %s and p.status = 'active'
"""


def building_of(conn: psycopg.Connection, property_id: int) -> Building:
    with conn.cursor() as cur:
        cur.execute(_BUILDING_SQL, (property_id,))
        row = cur.fetchone()
    if row is None:
        raise NoProperty(property_id)
    if row[0] is None or row[1] is None:
        raise NoBuilding(property_id)
    return Building(int(row[0]), int(row[1]))


_PURCHASE_COLS = "id, status, report_id, fetch_sent_at is not null, raw_report"


def _purchase(row: tuple[Any, ...] | None) -> Purchase | None:
    if row is None:
        return None
    return Purchase(int(row[0]), row[1], row[2], bool(row[3]), bytes(row[4]) if row[4] is not None else None)


def bought_at(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int) -> Any:
    """When the account last paid for this building's report (read or not); None when it never did."""
    with conn.cursor() as cur:
        cur.execute(
            "select max(coalesce(finished_at, requested_at)) from cadastre_price_purchases "
            "where account_id = %s and building_code = %s and status in ('done', 'paid_unread')",
            (account_id, building_code),
        )
        return cur.fetchone()[0]


def target(conn: psycopg.Connection, account_id: uuid.UUID, building: Building, *, resume: int | None,
           repeat: bool) -> Purchase | None:
    """Which purchase this request is about; None for a new one. Read under the lease, so it cannot change."""
    with conn.cursor() as cur:
        if resume is not None:
            cur.execute(
                f"select {_PURCHASE_COLS} from cadastre_price_purchases "
                "where id = %s and account_id = %s and building_code = %s "
                "and status in ('pending', 'ordered', 'paid_unread')",
                (resume, account_id, building.code),
            )
            if (named := _purchase(cur.fetchone())) is None:
                raise NotResumable(str(resume))
            return named
        cur.execute(
            "select 1 from cadastre_price_purchases "
            "where account_id = %s and building_code = %s and status in ('pending', 'ordered')",
            (account_id, building.code),
        )
        if cur.fetchone() is not None:
            raise InProgress("a purchase of this building is in flight")
    if not repeat and (at := bought_at(conn, account_id, building.code)) is not None:
        raise AlreadyBought(at)
    return None


def _start(conn: psycopg.Connection, account_id: uuid.UUID, building: Building) -> Purchase:
    with conn.cursor() as cur:
        cur.execute(
            "insert into cadastre_price_purchases (account_id, building_code, iskn_building_id) "
            f"values (%s, %s, %s) returning {_PURCHASE_COLS}",
            (account_id, building.code, building.iskn_id),
        )
        return _purchase(cur.fetchone())  # type: ignore[return-value]


def _move(conn: psycopg.Connection, account_id: uuid.UUID, token: uuid.UUID, purchase_id: int,
          expect: tuple[str, ...], sets: str, params: tuple[Any, ...] = (), *, no_file: bool = False) -> None:
    """One fenced state change: the row is the account's, in an expected state, and the lease is still this run's.
    Anything else means this run no longer owns the purchase, and it stops without touching it. `no_file`: only
    a purchase with no kept file may be ended as uncharged or as lost."""
    with conn.cursor() as cur:
        cur.execute(
            f"update cadastre_price_purchases p set {sets} "
            "where p.id = %s and p.account_id = %s and p.status = any(%s) "
            f"{'and p.raw_report is null ' if no_file else ''}"
            "and exists (select 1 from account_cuzk_logins l where l.account_id = p.account_id and l.run_token = %s)",
            (*params, purchase_id, account_id, list(expect), token),
        )
        if cur.rowcount != 1:
            raise InProgress("the purchase is no longer this run's")


def keep_fetched(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, cost_czk: int | None,
                 document: bytes) -> None:
    """The fetched file and what it cost, before anything reads it. Never fenced and never dropped: ČÚZK has
    answered the charging fetch, so whatever the row says now, it becomes a paid purchase with its file."""
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set cost_czk = %s, raw_report = %s, "
            "       fetch_sent_at = coalesce(fetch_sent_at, now()), "
            "       status = case when status = 'failed' then 'paid_unread' else status end "
            "where id = %s and account_id = %s and status <> 'done'",
            (cost_czk, cuzk_login.seal(document), purchase_id, account_id),
        )
        if cur.rowcount == 1:
            return
        cur.execute("select status from cadastre_price_purchases where id = %s and account_id = %s",
                    (purchase_id, account_id))
        row = cur.fetchone()
    if row is None or row[0] != "done":
        LOG.error("CADASTRE fetched report not kept: purchase=%s row=%s", purchase_id, row)
        raise PaidUnread(str(purchase_id))


def mark_unread(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'paid_unread', error = %s, "
            "       finished_at = coalesce(finished_at, now()) "
            "where id = %s and account_id = %s and status in ('ordered', 'paid_unread')",
            (error[:200], purchase_id, account_id),
        )


def finish(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int,
           deeds: list[cadastre_price_report.Deed]) -> None:
    """Store the deeds and clear the kept file. A report that held no deed keeps its file: "the cadastre knows no
    price" and "we read none" look the same from here, and the file is the only proof of which. A purchase already
    finished keeps the deeds it has."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'done', error = null, finished_at = now(), "
            "       raw_report = case when %s then null else raw_report end "
            "where id = %s and account_id = %s and status in ('ordered', 'paid_unread')",
            (bool(deeds), purchase_id, account_id),
        )
        if cur.rowcount != 1:
            cur.execute("select status from cadastre_price_purchases where id = %s and account_id = %s",
                        (purchase_id, account_id))
            row = cur.fetchone()
            if row is None or row[0] != "done":
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


def _read(conn_of: Opener, account_id: uuid.UUID, purchase_id: int, document: bytes) -> None:
    """A fetched file → stored deeds; a file that cannot be read ends the purchase as paid and unread, kept."""
    try:
        deeds = cadastre_price_report.parse(document)
        with conn_of() as conn:
            finish(conn, account_id, purchase_id, deeds)
    except PaidUnread:
        raise
    except Exception as exc:
        with conn_of() as conn:
            mark_unread(conn, account_id, purchase_id, f"{type(exc).__name__}: {exc}")
        raise PaidUnread(str(purchase_id)) from exc


_STATE_SQL = """
select id, status, cost_czk, object_count, requested_at, finished_at,
       fetch_sent_at is not null as fetch_sent, raw_report is not null as file_kept
  from cadastre_price_purchases
 where account_id = %s and building_code = %s and status <> 'failed'
 order by requested_at desc, id desc
 limit 1
"""
_DEEDS_SQL = """
select d.proceeding, d.kind, d.price, d.currency, d.signed_on, d.effective_on, d.entered_on, d.properties
  from cadastre_deeds d
 where d.account_id = %s
   and d.purchase_id = (select p.id from cadastre_price_purchases p
                         where p.account_id = %s and p.building_code = %s and p.status = 'done'
                         order by p.requested_at desc, p.id desc limit 1)
 order by d.effective_on desc nulls last, d.proceeding
"""


def state(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int) -> dict[str, Any]:
    """The account's newest purchase of the building that did not end uncharged, the deeds of its newest finished
    one, and whether a run of the account is live right now. A read: it calls nothing at ČÚZK."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_STATE_SQL, (account_id, building_code))
        purchase = cur.fetchone()
        cur.execute(_DEEDS_SQL, (account_id, account_id, building_code))
        deeds = cur.fetchall()
    if purchase is not None:
        purchase = {**purchase, "requested_at": purchase["requested_at"].isoformat(),
                    "finished_at": purchase["finished_at"].isoformat() if purchase["finished_at"] else None}
    return {
        "price_czk": PRICE_CZK,
        "running": cuzk_login.run_live(conn, account_id),
        "purchase": purchase,
        "deeds": [{**d, "price": float(d["price"]) if d["price"] is not None else None,
                   **{k: d[k].isoformat() if d[k] else None for k in ("signed_on", "effective_on", "entered_on")}}
                  for d in deeds],
    }


class _Run:
    """The lease holder's calls to ČÚZK, and its one row of the login-check ledger. The row is in flight (counted
    as unaccepted) during every call and settled by that call's own answer; a refusal marks the login the run
    read. An error ČÚZK gives AFTER accepting the login is an accepted login."""

    def __init__(self, account_id: uuid.UUID, token: uuid.UUID, held: cuzk_login.Leased, check_id: int,
                 opener: Opener, started: float, clock: Callable[[], float]) -> None:
        self.account_id, self.token, self.held, self._check_id = account_id, token, held, check_id
        self.opener, self._started, self._clock = opener, started, clock
        self._last: str | None = None  # the row's outcome before the call in flight; None: never answered

    def _settle(self, outcome: str) -> None:
        self._last = outcome
        with self.opener() as conn:
            cuzk_login.settle_check(conn, self._check_id, outcome)
            if outcome == "refused":
                cuzk_login.mark_refused(conn, self.account_id, self.held)

    def call(self, fn: Callable[..., Any], *args: Any, before: Callable[[], None] | None = None) -> Any:
        """Send one call, only if this run may still send: inside its time, the lease renewed and still its own,
        the login still the one it read, and nobody else's check on that username in doubt. `before` runs once
        that is known and before the call leaves."""
        if self._clock() - self._started > RUN_S:
            raise InProgress("the request's time is used up")
        with self.opener() as conn:
            if not cuzk_login.renew(conn, self.account_id, self.token, self.held):
                raise InProgress("the stored login may no longer be used by this run")
            if not cuzk_login.call_begins(conn, self._check_id):
                raise InProgress("another check on this username is in doubt")
        try:
            if before is not None:
                before()
        except BaseException:
            if self._last is not None:  # nothing was sent: the row reads as its last answer again
                self._settle(self._last)
            raise
        try:
            result = fn(*args, self.held.username, self.held.password)
        except wsdp_client.WsdpError as exc:
            self._settle("refused" if exc.reason == "refused" else "accepted" if exc.authenticated else "no_answer")
            raise
        self._settle("accepted")
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


def buy(account_id: uuid.UUID, property_id: int, *, opener: Opener, repeat: bool = False,
        resume: int | None = None, sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> Building:
    """Start the account's purchase of the property's building's price report, or continue the purchase `resume`
    names, and store its deeds. Holds the account's purchase lease from before its first call to its end."""
    started = clock()
    with opener() as conn:
        building = building_of(conn, property_id)
    wsdp_client.base_url()  # a misconfigured address sends nothing, so it must spend nothing
    token = uuid.uuid4()
    with opener() as conn:
        try:
            held = cuzk_login.lease(conn, account_id, token)
        except cuzk_login.LeaseHeld:
            raise InProgress("another run of this account is live") from None
    if held is None:
        raise NoLogin(str(account_id))
    try:
        _run(account_id, building, token, held, opener=opener, repeat=repeat, resume=resume, sleep=sleep,
             clock=clock, started=started)
    finally:
        _release(opener, account_id, token)
    return building


def _release(opener: Opener, account_id: uuid.UUID, token: uuid.UUID) -> None:
    """Give the lease back without ever replacing the run's own outcome: a lease that cannot be released now
    simply goes stale."""
    try:
        with opener() as conn:
            cuzk_login.release(conn, account_id, token)
    except Exception:
        LOG.exception("CADASTRE lease not released: account=%s", account_id)


def cancel(account_id: uuid.UUID, property_id: int, purchase_id: int, *, opener: Opener) -> Building:
    """Give up a purchase in flight, sending nothing: the way out when it cannot be continued. One that was never
    fetched ends `failed` (nothing charged: an unfetched report costs nothing); one whose fetch was sent ends
    `paid_unread` (it may have been charged), and the building can then be bought anew with the confirmed repeat."""
    with opener() as conn:
        building = building_of(conn, property_id)
    token = uuid.uuid4()
    with opener() as conn:
        try:
            held = cuzk_login.take_lease(conn, account_id, token)
        except cuzk_login.LeaseHeld:
            raise InProgress("another run of this account is live") from None
    if held is None:
        raise NoLogin(str(account_id))
    try:
        with opener() as conn, conn.cursor() as cur:
            cur.execute(
                "update cadastre_price_purchases set finished_at = now(), error = 'given up by the user', "
                "       status = case when fetch_sent_at is null then 'failed' else 'paid_unread' end "
                "where id = %s and account_id = %s and building_code = %s and status in ('pending', 'ordered') "
                "and raw_report is null",
                (purchase_id, account_id, building.code),
            )
            if cur.rowcount != 1:
                raise NotResumable(str(purchase_id))
    finally:
        _release(opener, account_id, token)
    return building


def _run(account_id: uuid.UUID, building: Building, token: uuid.UUID, held: cuzk_login.Leased, *, opener: Opener,
         repeat: bool, resume: int | None, sleep: Callable[[float], None], clock: Callable[[], float],
         started: float) -> None:
    with opener() as conn:
        purchase = target(conn, account_id, building, resume=resume, repeat=repeat)
    if purchase is not None and purchase.kept is not None:
        try:
            document = cuzk_login.unseal(purchase.kept)
        except (cuzk_login.Unreadable, cuzk_login.NotConfigured):
            document = None
        if document is not None:
            _read(opener, account_id, purchase.id, document)  # free: nothing is sent, so no login is needed
            return
        # The kept file can no longer be decrypted (the key changed). ČÚZK serves a fetched report again without
        # charging, so drop the dead copy and fetch it anew below.
        with opener() as conn:
            _move(conn, account_id, token, purchase.id, ("ordered", "paid_unread"), "raw_report = null")
    if held.refused:
        raise LoginRefused(str(account_id))
    with opener() as conn, conn.transaction():
        check_id = cuzk_login.admit_check(conn, account_id, held.username)
        if purchase is None:
            purchase = _start(conn, account_id, building)
    run = _Run(account_id, token, held, check_id, opener, started, clock)

    report_id = purchase.report_id
    if report_id is None:
        try:
            units = run.call(wsdp_client.find_units, building.iskn_id)
            if len(units) > wsdp_client.MAX_PRICE_OBJECTS:
                raise TooManyUnits(len(units))
            object_type, ids = ("JED_S", [u.iskn_id for u in units]) if units else ("STA_S", [building.iskn_id])
            ordered = run.call(wsdp_client.generate_prices, object_type, ids)
        except (wsdp_client.WsdpError, TooManyUnits) as exc:
            run.close(purchase.id, ("pending",), "status = 'failed', error = %s, finished_at = now()",
                      (f"{type(exc).__name__}: {exc}"[:200],))
            raise
        report_id = ordered.id
        run.move(purchase.id, ("pending",), "status = 'ordered', report_id = %s, object_count = %s",
                 (report_id, len(ids)))

    def gone(why: str, *, fetch_sent: bool) -> None:
        """ČÚZK no longer serves the report: nothing charged if no fetch of it was ever sent."""
        if fetch_sent:
            run.close(purchase.id, ("ordered", "paid_unread"),
                      "status = 'paid_unread', error = %s, finished_at = coalesce(finished_at, now())", (why[:200],))
        else:
            run.close(purchase.id, ("ordered",), "status = 'failed', error = %s, finished_at = now()", (why[:200],))
        raise ReportGone(fetch_sent)

    fetch_sent = purchase.fetch_sent or purchase.status == "paid_unread"
    deadline = clock() + POLL_FOR_S
    while True:
        try:
            report = run.call(wsdp_client.report_state, report_id)
        except wsdp_client.WsdpError as exc:
            if exc.reason == "service" and exc.status == wsdp_client.REPORT_GONE:
                gone(f"report {report_id}: error 304", fetch_sent=fetch_sent)
            raise  # any other error says nothing about the report: the purchase stays as it is
        if report.state in (_UNCHARGED, _CHARGED):
            break
        if report.state not in _WAITING:
            gone(f"report {report_id}: state {report.state}", fetch_sent=fetch_sent)
        if clock() >= deadline:
            raise InProgress(report.state)
        sleep(POLL_EVERY_S)

    if report.state == _UNCHARGED and (report.price_czk is None or report.price_czk > PRICE_CZK):
        # ČÚZK shows it as never fetched, so nothing was charged whatever we sent before; and it will not be.
        run.close(purchase.id, ("ordered", "paid_unread"),
                  "status = 'failed', error = %s, finished_at = now(), fetch_sent_at = null",
                  (f"stated price {report.price_czk}",))
        raise PriceChanged(report.price_czk)

    def mark_sent() -> None:
        run.move(purchase.id, ("ordered", "paid_unread"), "fetch_sent_at = coalesce(fetch_sent_at, now())")

    try:
        fetched, document = run.call(wsdp_client.fetch_report, report_id, before=mark_sent)
    except wsdp_client.WsdpError as exc:
        if exc.reason == "refused":
            # Refused before anything was served, so this call charged nothing: do not leave it reading as sent.
            if not fetch_sent:
                run.close(purchase.id, ("ordered",), "fetch_sent_at = %s", (None,))
            raise
        if exc.reason == "service" and exc.status == wsdp_client.REPORT_GONE:
            gone(f"report {report_id}: error 304 at the fetch", fetch_sent=True)
        raise ChargeUncertain(str(report_id)) from exc
    cost = fetched.price_czk if fetched.price_czk is not None else report.price_czk
    with opener() as conn:
        keep_fetched(conn, account_id, purchase.id, cost, document)
    _read(opener, account_id, purchase.id, document)
