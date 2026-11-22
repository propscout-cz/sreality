"""Cadastre prices bought on request: one building, one paid WSDP output, on the asking account's own ČÚZK login.

ČÚZK charges at the FIRST fetch of a finished report; ordering and polling are free, and so is a second fetch.
So the rules that keep one purchase to one charge are about who may order and who may fetch:

- the purchase row is written before anything is sent, and one transaction under a per-(account, building) lock
  decides what a request is: a new purchase, the resume of one in flight, or a refusal (`claim`);
- only the request holding a `pending` row's claim orders; any other request finds it "being ordered" until the
  claim is stale, and a request that lost its claim never fetches the report it ordered (unfetched = uncharged);
- the fetch is claimed too, and marked BEFORE it is sent, so an answer lost after that is known as possibly charged;
- a fetched report is kept, encrypted, until its deeds are stored, so nothing paid for is lost to a parser.

Every purchase is one use of the login in the login-check ledger (`cuzk_login.admit_check`, #2608): a login ČÚZK
refuses here counts like a refused save, and is not used again until it is saved again. No database connection is
held while waiting on ČÚZK. Everything is the buying account's own (operator ruling 2026-11-22): every statement
names the account (service-role connection)."""

from __future__ import annotations

import datetime
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

PRICE_CZK = 50  # vyhláška 358/2013, item 5010: one output over up to 200 properties of one type
POLL_EVERY_S = 2
POLL_FOR_S = 60
# A claim is another request's until it is older than the longest that request can still be working: two calls
# to order, or one fetch, each at the client's 25 s timeout, with room to spare.
CLAIM_S = 120
_READY = frozenset({"zpracován", "zaúčtován"})
_LOCK_PURCHASE = 2602  # advisory-lock namespace; taken BEFORE cuzk_login's two, always in that order

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
    """The purchase is in flight and this request did nothing to it: another request holds it (`pending`,
    being ordered or fetched), or ČÚZK is still preparing the report (`ordered`). Asking again resumes it."""


class AlreadyBought(RuntimeError):
    """The account already holds this building's prices; a second paid report needs the caller's `repeat`."""

    def __init__(self, finished_at: Any) -> None:
        super().__init__("already bought")
        self.finished_at = finished_at


class PriceChanged(RuntimeError):
    """ČÚZK states another price for the finished report than the one acknowledged; it was not fetched."""

    def __init__(self, stated_czk: int | None) -> None:
        super().__init__(f"stated price {stated_czk}")
        self.stated_czk = stated_czk


class ReportGone(RuntimeError):
    """ČÚZK no longer serves the ordered report, and it was never fetched: nothing was charged."""


class ChargeUncertain(RuntimeError):
    """The charging fetch was sent and its answer did not arrive; asking again fetches the same report."""


class PaidUnread(RuntimeError):
    """The report was fetched (charged) and its deeds could not be stored; the file is kept."""


@dataclass(frozen=True)
class Building:
    code: int
    iskn_id: int


@dataclass(frozen=True)
class Claim:
    purchase_id: int
    report_id: int | None  # None: this request holds the order
    claimed_at: datetime.datetime
    check_id: int


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


_IN_FLIGHT = "status in ('pending', 'ordered')"


def bought_at(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int) -> Any:
    """When the account last paid for this building's report (read or not); None when it never did."""
    with conn.cursor() as cur:
        cur.execute(
            "select max(finished_at) from cadastre_price_purchases "
            "where account_id = %s and building_code = %s and status in ('done', 'paid_unread')",
            (account_id, building_code),
        )
        return cur.fetchone()[0]


def claim(conn: psycopg.Connection, account_id: uuid.UUID, building: Building, username: str, *,
          repeat: bool) -> Claim:
    """Decide, in one transaction, what this request is, and take its row of the login ledger last.

    Under the (account, building) lock: no purchase in flight → a new one (refused as `AlreadyBought` without
    `repeat` when one was paid for); a `pending` one → `InProgress` while its claim is fresh, else this request
    takes the claim; an `ordered` one → resumed. A refusal or a spent budget (`cuzk_login.Throttled`) writes
    nothing."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, %s))",
                    (f"{account_id}:{building.code}", _LOCK_PURCHASE))
        cur.execute(
            f"select id, status, report_id, claimed_at < now() - make_interval(secs => %s) "
            f"from cadastre_price_purchases where account_id = %s and building_code = %s and {_IN_FLIGHT}",
            (CLAIM_S, account_id, building.code),
        )
        row = cur.fetchone()
        if row is None and not repeat and (at := bought_at(conn, account_id, building.code)) is not None:
            raise AlreadyBought(at)
        if row is not None and row[1] == "pending" and not row[3]:
            raise InProgress("being ordered")
        check_id = cuzk_login.admit_check(conn, account_id, username)
        if row is None:
            cur.execute(
                "insert into cadastre_price_purchases (account_id, building_code, iskn_building_id) "
                "values (%s, %s, %s) returning id, report_id, claimed_at",
                (account_id, building.code, building.iskn_id),
            )
        elif row[1] == "pending":
            cur.execute("update cadastre_price_purchases set claimed_at = now() where id = %s "
                        "returning id, report_id, claimed_at", (row[0],))
        else:
            cur.execute("select id, report_id, claimed_at from cadastre_price_purchases where id = %s", (row[0],))
        purchase_id, report_id, claimed_at = cur.fetchone()
    return Claim(int(purchase_id), report_id, claimed_at, check_id)


def mark_ordered(conn: psycopg.Connection, held: Claim, report_id: int, object_count: int) -> bool:
    """Record the order; False when this request no longer holds the claim (it then must not fetch)."""
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'ordered', report_id = %s, object_count = %s "
            "where id = %s and status = 'pending' and claimed_at = %s",
            (report_id, object_count, held.purchase_id, held.claimed_at),
        )
        return cur.rowcount == 1


def fail_order(conn: psycopg.Connection, held: Claim, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'failed', error = %s, finished_at = now() "
            "where id = %s and status = 'pending' and claimed_at = %s",
            (error[:200], held.purchase_id, held.claimed_at),
        )


def close_unfetched(conn: psycopg.Connection, purchase_id: int, error: str) -> bool:
    """End an ordered purchase whose report cannot be had. `failed` if no fetch was ever sent (nothing charged),
    else `paid_unread`: a fetch whose answer we lost may have charged. True when nothing was charged."""
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set finished_at = now(), error = %s, "
            "       status = case when fetch_sent_at is null then 'failed' else 'paid_unread' end "
            "where id = %s and status = 'ordered' returning status",
            (error[:200], purchase_id),
        )
        row = cur.fetchone()
    return row is None or row[0] == "failed"


def claim_fetch(conn: psycopg.Connection, purchase_id: int) -> bool:
    """Mark the charging fetch as sent, before it is; False while another request's fetch is still fresh."""
    with conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set fetch_sent_at = now() where id = %s and status = 'ordered' "
            "and (fetch_sent_at is null or fetch_sent_at < now() - make_interval(secs => %s))",
            (purchase_id, CLAIM_S),
        )
        return cur.rowcount == 1


def keep_fetched(conn: psycopg.Connection, purchase_id: int, cost_czk: int | None, document: bytes) -> None:
    """The fetched file and what it cost, before anything reads it."""
    with conn.cursor() as cur:
        cur.execute("update cadastre_price_purchases set cost_czk = %s, raw_report = %s "
                    "where id = %s and status = 'ordered'", (cost_czk, cuzk_login.seal(document), purchase_id))


def mark_unread(conn: psycopg.Connection, purchase_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute("update cadastre_price_purchases set status = 'paid_unread', error = %s, finished_at = now() "
                    "where id = %s and status = 'ordered'", (error[:200], purchase_id))


def _store_deeds(cur: psycopg.Cursor, account_id: uuid.UUID, purchase_id: int,
                 deeds: list[cadastre_price_report.Deed]) -> None:
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


def finish(conn: psycopg.Connection, account_id: uuid.UUID, purchase_id: int,
           deeds: list[cadastre_price_report.Deed], *, was: str = "ordered") -> bool:
    """Store the deeds and clear the kept file; False when another request finished it first (its deeds stand)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "update cadastre_price_purchases set status = 'done', raw_report = null, error = null, "
            "       finished_at = now() where id = %s and status = %s",
            (purchase_id, was),
        )
        if cur.rowcount != 1:
            return False
        _store_deeds(cur, account_id, purchase_id, deeds)
    return True


def reread_kept(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int) -> bool:
    """Read the kept file of the account's paid-but-unread purchase again (free: nothing is sent to ČÚZK). True
    when its deeds are now stored, False when there is no kept file, `PaidUnread` when it still cannot be read."""
    with conn.cursor() as cur:
        cur.execute(
            "select id, raw_report from cadastre_price_purchases where account_id = %s and building_code = %s "
            "and status = 'paid_unread' and raw_report is not null order by id desc limit 1",
            (account_id, building_code),
        )
        row = cur.fetchone()
    if row is None:
        return False
    try:
        deeds = cadastre_price_report.parse(cuzk_login.unseal(row[1]))
    except Exception as exc:
        raise PaidUnread(str(row[0])) from exc
    finish(conn, account_id, int(row[0]), deeds, was="paid_unread")
    return True


_STATE_SQL = """
select id, status, cost_czk, object_count, requested_at, finished_at
  from cadastre_price_purchases
 where account_id = %s and building_code = %s and status <> 'failed'
 order by requested_at desc
 limit 1
"""
_DEEDS_SQL = """
select d.proceeding, d.kind, d.price, d.currency, d.signed_on, d.effective_on, d.entered_on, d.properties
  from cadastre_deeds d
 where d.account_id = %s
   and d.purchase_id = (select p.id from cadastre_price_purchases p
                         where p.account_id = %s and p.building_code = %s and p.status = 'done'
                         order by p.requested_at desc limit 1)
 order by d.effective_on desc nulls last, d.proceeding
"""


def state(conn: psycopg.Connection, account_id: uuid.UUID, building_code: int) -> dict[str, Any]:
    """The account's newest request for the building and the deeds of its newest finished one."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_STATE_SQL, (account_id, building_code))
        purchase = cur.fetchone()
        cur.execute(_DEEDS_SQL, (account_id, account_id, building_code))
        deeds = cur.fetchall()
    if purchase is not None:
        purchase = {"status": purchase["status"], "cost_czk": purchase["cost_czk"],
                    "object_count": purchase["object_count"],
                    "requested_at": purchase["requested_at"].isoformat(),
                    "finished_at": purchase["finished_at"].isoformat() if purchase["finished_at"] else None}
    return {
        "price_czk": PRICE_CZK,
        "purchase": purchase,
        "deeds": [{**d, "price": float(d["price"]) if d["price"] is not None else None,
                   **{k: d[k].isoformat() if d[k] else None for k in ("signed_on", "effective_on", "entered_on")}}
                  for d in deeds],
    }


class _LoginUse:
    """One purchase's use of the stored login, as one row of the login-check ledger: settled by ČÚZK's first
    answer, and settled again as refused if a later call of the run is refused (the login is then marked, and the
    purchase ends). An error ČÚZK gives AFTER accepting the login is an accepted login."""

    def __init__(self, account_id: uuid.UUID, check_id: int, opener: Opener) -> None:
        self._account_id, self._check_id, self._opener, self._settled = account_id, check_id, opener, False

    def _settle(self, outcome: str) -> None:
        if self._settled and outcome != "refused":
            return
        self._settled = True
        with self._opener() as conn:
            cuzk_login.settle_check(conn, self._check_id, outcome)
            if outcome == "refused":
                cuzk_login.mark_refused(conn, self._account_id)

    def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            result = fn(*args)
        except wsdp_client.WsdpError as exc:
            self._settle("refused" if exc.reason == "refused" else "accepted" if exc.authenticated else "no_answer")
            raise
        self._settle("accepted")
        return result


def buy(account_id: uuid.UUID, property_id: int, *, opener: Opener, repeat: bool = False,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Building:
    """Order (or resume) the account's price report for the property's building and store its deeds."""
    with opener() as conn:
        building = building_of(conn, property_id)
        if reread_kept(conn, account_id, building.code):
            return building
    wsdp_client.base_url()  # a misconfigured address sends nothing, so it must spend nothing
    with opener() as conn:
        login = cuzk_login.login_of(conn, account_id)
        if login is None:
            raise NoLogin(str(account_id))
        if cuzk_login.state(conn, account_id)["refused"]:
            raise LoginRefused(str(account_id))
        held = claim(conn, account_id, building, login[0], repeat=repeat)
    use = _LoginUse(account_id, held.check_id, opener)
    report_id = held.report_id
    if report_id is None:
        try:
            units = use.call(wsdp_client.find_units, building.iskn_id, *login)
            if len(units) > wsdp_client.MAX_PRICE_OBJECTS:
                raise TooManyUnits(len(units))
            object_type, ids = ("JED_S", [u.iskn_id for u in units]) if units else ("STA_S", [building.iskn_id])
            ordered = use.call(wsdp_client.generate_prices, object_type, ids, *login)
        except (wsdp_client.WsdpError, TooManyUnits) as exc:
            with opener() as conn:
                fail_order(conn, held, f"{type(exc).__name__}: {exc}")
            raise
        report_id = ordered.id
        with opener() as conn:
            if not mark_ordered(conn, held, report_id, len(ids)):
                # Another request took the stale claim and orders its own; ours stays unfetched, so uncharged.
                raise InProgress("claim lost")
    deadline = clock() + POLL_FOR_S
    while True:
        try:
            report = use.call(wsdp_client.report_state, report_id, *login)
        except wsdp_client.WsdpError as exc:
            if exc.reason != "service":
                raise
            with opener() as conn:
                if close_unfetched(conn, held.purchase_id, f"report {report_id}: service error {exc.status}"):
                    raise ReportGone(str(report_id)) from None
            raise PaidUnread(str(report_id)) from None
        if report.state in _READY:
            break
        if clock() >= deadline:
            raise InProgress(report.state)
        sleep(POLL_EVERY_S)
    if report.price_czk is None or report.price_czk > PRICE_CZK:
        with opener() as conn:
            close_unfetched(conn, held.purchase_id, f"stated price {report.price_czk}")
        raise PriceChanged(report.price_czk)
    with opener() as conn:
        if not claim_fetch(conn, held.purchase_id):
            raise InProgress("being fetched")
    try:
        fetched, document = use.call(wsdp_client.fetch_report, report_id, *login)  # the first fetch is the charge
    except wsdp_client.WsdpError as exc:
        if exc.reason == "refused":
            raise  # refused before anything was served: not charged, and the login is now marked
        raise ChargeUncertain(str(report_id)) from exc
    with opener() as conn:
        keep_fetched(conn, held.purchase_id, fetched.price_czk if fetched.price_czk is not None else report.price_czk,
                     document)
    try:
        deeds = cadastre_price_report.parse(document)
        with opener() as conn:
            finish(conn, account_id, held.purchase_id, deeds)
    except Exception as exc:
        with opener() as conn:
            mark_unread(conn, held.purchase_id, f"{type(exc).__name__}: {exc}")
        raise PaidUnread(str(report_id)) from exc
    return building
